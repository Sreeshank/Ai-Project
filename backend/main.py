"""Campus knowledge service with Supabase storage and a local demo backend."""
from contextlib import asynccontextmanager, contextmanager, suppress
import asyncio
from datetime import date, datetime, timezone
from pathlib import Path
import io
import json
import os
import re
import sqlite3
import uuid

import httpx
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile, Request
from fastapi.responses import Response, JSONResponse
from starlette.concurrency import run_in_threadpool
from backend import semantic, database, generation, runtime_status, operations, auth
from backend.indexing import VectorIndexer, enqueue_all
from backend.extraction import ExtractionBusy, ExtractedPage, extract_document, render_pdf_page, search_chunks
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / '.env')
DATA = ROOT / 'data'
DB = DATA / 'campus.db'
STOP = set('a an the is are was were be to of for in on at and or can i my me how what when where do does will please tell about'.split())
DEPARTMENTS = [
    {'name': 'Library', 'description': 'Membership, borrowing and study spaces', 'location': 'Sample: Learning Resource Centre'},
    {'name': 'Academic Office', 'description': 'Calendars, examinations and registration', 'location': 'Sample: Administration Block'},
    {'name': 'Student Services', 'description': 'Campus facilities and general support', 'location': 'Sample: Student Centre'},
]
SAMPLES = [
    ('Library handbook', 'Library', 'Library opening hours are Monday to Friday, 8:00 AM to 8:00 PM, and Saturday, 9:00 AM to 4:00 PM. The library is closed on Sundays and public holidays.\n\nTo apply for library membership, bring your student ID card to the library help desk and complete the membership form. Students may borrow up to three books for 14 days. Renew a book at the help desk before its due date.'),
    ('Student services guide', 'Student Services', 'The student support desk is open Monday to Friday, 9:00 AM to 5:00 PM in the Student Centre. Visit the desk for guidance on campus facilities and general enquiries.\n\nFor campus Wi-Fi access, visit the IT help desk with your student ID. Staff will help you activate your college network account. Never share your password.\n\nLost student ID cards should be reported to Student Services. Submit the replacement request form at the support desk. Any replacement fee must be confirmed with the office.'),
    ('Academic procedures', 'Academic Office', 'Examination timetables are published by the Academic Office on the official college noticeboard. Confirm your programme and semester before checking a timetable. Contact the Academic Office if your subject is missing.\n\nFor a bonafide certificate, submit the certificate request form with your student ID at the Academic Office. Processing time and fees should be confirmed with the office.\n\nFor course registration, meet your academic adviser before submitting the registration form to your department. Registration deadlines are announced separately in the approved academic calendar.'),
]

@contextmanager
def connect():
    if database.using_postgres():
        with database.postgres_connection() as db:
            yield db
        return
    db = sqlite3.connect(DB)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA foreign_keys=ON')
    try:
        with db:
            yield db
    finally:
        db.close()

vector_index = VectorIndexer(connect)

def initialize():
    if database.using_postgres():
        with connect() as db:
            # Explicit migration/copy happens before switching the backend.
            db.execute('SELECT 1 FROM documents LIMIT 1')
            vector_index.initialize(db)
        return
    DATA.mkdir(exist_ok=True)
    with connect() as db:
        db.executescript('''
        CREATE TABLE IF NOT EXISTS documents (
          id TEXT PRIMARY KEY, title TEXT NOT NULL, department TEXT NOT NULL,
          status TEXT NOT NULL, created_at TEXT NOT NULL, expires_on TEXT,
          sample INTEGER NOT NULL DEFAULT 0, filename TEXT NOT NULL,
          media_type TEXT NOT NULL, original BLOB NOT NULL);
        CREATE TABLE IF NOT EXISTS chunks (
          id INTEGER PRIMARY KEY, document_id TEXT REFERENCES documents(id),
          page INTEGER NOT NULL, text TEXT NOT NULL);
        CREATE VIRTUAL TABLE IF NOT EXISTS search USING fts5(text, content='chunks', content_rowid='id');
        CREATE TRIGGER IF NOT EXISTS chunk_insert AFTER INSERT ON chunks BEGIN
          INSERT INTO search(rowid,text) VALUES(new.id,new.text); END;
        CREATE TRIGGER IF NOT EXISTS chunk_delete AFTER DELETE ON chunks BEGIN
          INSERT INTO search(search,rowid,text) VALUES('delete',old.id,old.text); END;
        CREATE TABLE IF NOT EXISTS document_pages (
          document_id TEXT REFERENCES documents(id), page INTEGER NOT NULL, text TEXT NOT NULL,
          method TEXT NOT NULL, warnings TEXT NOT NULL DEFAULT '[]', confidence REAL,
          reviewed INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(document_id,page));
        CREATE TABLE IF NOT EXISTS document_revisions (
          id TEXT PRIMARY KEY, document_id TEXT REFERENCES documents(id),
          created_at TEXT NOT NULL, reason TEXT NOT NULL, pages TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS document_replacements (
          replacement_id TEXT PRIMARY KEY REFERENCES documents(id), previous_id TEXT NOT NULL UNIQUE REFERENCES documents(id));
        CREATE TABLE IF NOT EXISTS staff_members (
          user_id TEXT PRIMARY KEY, role TEXT NOT NULL CHECK(role IN ('admin','staff')),
          enabled INTEGER NOT NULL DEFAULT 1, updated_at TEXT DEFAULT CURRENT_TIMESTAMP, updated_by TEXT);
        CREATE TABLE IF NOT EXISTS answers (id TEXT PRIMARY KEY, question TEXT, payload TEXT);
        CREATE TABLE IF NOT EXISTS feedback (id TEXT PRIMARY KEY, answer_id TEXT REFERENCES answers(id), helpful INTEGER, created_at TEXT);
        ''')
        vector_index.initialize(db)
        # Backfill full pages for older uploads without OCR/model downloads at startup.
        legacy = db.execute('SELECT d.* FROM documents d WHERE NOT EXISTS (SELECT 1 FROM document_pages p WHERE p.document_id=d.id)').fetchall()
        for row in legacy:
            try:
                if row['media_type'] == 'application/pdf':
                    from pypdf import PdfReader
                    reader = PdfReader(io.BytesIO(row['original']))
                    pages = [ExtractedPage(i + 1, page.extract_text(extraction_mode='layout') or '', 'legacy', ['Older text extraction. Run OCR if the text is garbled, then compare with the original.']) for i, page in enumerate(reader.pages)]
                else:
                    pages = [ExtractedPage(1, bytes(row['original']).decode('utf-8-sig'))]
                store_pages(db, row['id'], pages)
            except Exception:
                # Preserve recoverable legacy data without displaying overlapping chunks twice.
                chunks = db.execute('SELECT page,text FROM chunks WHERE document_id=? ORDER BY id', (row['id'],)).fetchall()
                grouped = {}
                for chunk in chunks:
                    grouped.setdefault(chunk['page'], []).append(chunk['text'])
                store_pages(db, row['id'], [ExtractedPage(page, '\n\n'.join(texts), 'legacy', ['Legacy passages require re-extraction from the original.']) for page,texts in grouped.items()])
        if not db.execute('SELECT 1 FROM documents LIMIT 1').fetchone():
            for title, department, content in SAMPLES:
                add_document(db, title, department, 'approved', None, True,
                             title + '.txt', 'text/plain', ('SAMPLE DATA — not official college information.\n\n' + content).encode(), [(1, content)])

def add_document(db, title, department, status, expires_on, sample, filename, media_type, original, pages):
    identifier = str(uuid.uuid4())
    db.execute('INSERT INTO documents VALUES(?,?,?,?,?,?,?,?,?,?)',
               (identifier, title, department, status, datetime.now(timezone.utc).isoformat(), expires_on, int(sample), filename, media_type, original))
    normalized = [page if isinstance(page, ExtractedPage) else ExtractedPage(page[0], page[1]) for page in pages]
    store_pages(db, identifier, normalized)
    return identifier


def store_pages(db, identifier, pages):
    db.execute('DELETE FROM chunks WHERE document_id=?', (identifier,))
    db.execute('DELETE FROM document_pages WHERE document_id=?', (identifier,))
    for page in pages:
        db.execute('INSERT INTO document_pages VALUES(?,?,?,?,?,?,?)',
            (identifier, page.page, page.text, page.method, json.dumps(page.warnings), page.confidence, int(page.reviewed)))
        for text in search_chunks(page.text):
            db.execute('INSERT INTO chunks(document_id,page,text) VALUES(?,?,?)', (identifier,page.page,text))


def page_payload(row, media_type):
    return {'page': row['page'], 'text': row['text'], 'method': row['method'],
        'warnings': json.loads(row['warnings']), 'confidence': row['confidence'],
        'reviewed': bool(row['reviewed']), 'has_image': media_type == 'application/pdf'}


def snapshot_pages(db, identifier, reason):
    rows = db.execute('SELECT * FROM document_pages WHERE document_id=? ORDER BY page', (identifier,)).fetchall()
    db.execute('INSERT INTO document_revisions VALUES(?,?,?,?,?)',
        (str(uuid.uuid4()), identifier, datetime.now(timezone.utc).isoformat(), reason, json.dumps([dict(row) for row in rows])))

@asynccontextmanager
async def lifespan(app):
    initialize()
    worker = asyncio.create_task(vector_index.run())
    try:
        yield
    finally:
        worker.cancel()
        with suppress(asyncio.CancelledError):
            await worker

app = FastAPI(title='Campus Compass API', version='0.2.0', lifespan=lifespan)
app.middleware('http')(operations.middleware)

@app.exception_handler(database.psycopg.Error)
async def database_unavailable(request, exception):
    return JSONResponse(status_code=503, content={'detail': 'The document database is temporarily unavailable. Please try again.'})

def admin(request: Request):
    return auth.require_staff(request, connect)

app.include_router(auth.router(connect))

class Question(BaseModel):
    question: str = Field(min_length=2, max_length=2000)

class Feedback(BaseModel):
    answer_id: str
    helpful: bool

def public_document(row):
    return {key: row[key] for key in ('id', 'title', 'department', 'status', 'created_at', 'expires_on', 'sample')}

def active_clause():
    return "d.status='approved' AND (d.expires_on IS NULL OR d.expires_on>=?)"

@app.get('/api/health')
def health():
    return {'status': 'ok', 'document_storage': 'supabase-postgres' if database.using_postgres() else 'sqlite', 'mode': 'gemini' if os.getenv('GEMINI_API_KEY') else 'extractive', 'retrieval': 'hybrid-pgvector' if semantic.configured() else ('postgres-fts' if database.using_postgres() else 'sqlite-fts5'), 'semantic': vector_index.status(), 'admin_enabled': auth.configured(), 'authentication': 'supabase'}

@app.get('/api/admin/search/status', dependencies=[Depends(admin)])
def search_status():
    return vector_index.status()

@app.post('/api/admin/search/reindex', dependencies=[Depends(admin)], status_code=202)
def reindex_search():
    with connect() as db:
        enqueue_all(db)
    return {'queued': True, **vector_index.status()}

@app.get('/api/documents')
def documents():
    with connect() as db:
        rows = db.execute('SELECT d.id,d.title,d.department,d.status,d.created_at,d.expires_on,d.sample FROM documents d WHERE ' + active_clause() + ' ORDER BY title', (date.today().isoformat(),)).fetchall()
    return [public_document(row) for row in rows]

@app.get('/api/admin/documents', dependencies=[Depends(admin)])
def all_documents():
    with connect() as db:
        rows = db.execute('''SELECT d.id,d.title,d.department,d.status,d.created_at,d.expires_on,d.sample,
            p.previous_id,n.replacement_id FROM documents d
            LEFT JOIN document_replacements p ON p.replacement_id=d.id
            LEFT JOIN document_replacements n ON n.previous_id=d.id ORDER BY d.created_at DESC''').fetchall()
    return [{**public_document(row), 'previous_id': row['previous_id'], 'replacement_id': row['replacement_id']} for row in rows]

@app.get('/api/departments')
def departments():
    return DEPARTMENTS

@app.get('/api/admin/documents/{identifier}/preview', dependencies=[Depends(admin)])
def preview(identifier: str):
    with connect() as db:
        document = db.execute('SELECT media_type FROM documents WHERE id=?', (identifier,)).fetchone()
        if document is None:
            raise HTTPException(404, 'Document not found.')
        pages = db.execute('SELECT * FROM document_pages WHERE document_id=? ORDER BY page', (identifier,)).fetchall()
    return [page_payload(page, document['media_type']) for page in pages]


@app.get('/api/admin/documents/{identifier}/pages/{number}/image', dependencies=[Depends(admin)])
def page_image(identifier: str, number: int):
    with connect() as db:
        row = db.execute('SELECT original,media_type FROM documents WHERE id=?', (identifier,)).fetchone()
    if row is None or row['media_type'] != 'application/pdf':
        raise HTTPException(404, 'Original PDF not found.')
    try:
        image = render_pdf_page(bytes(row['original']), number, max_side=1900)
    except ValueError as exc:
        raise HTTPException(404, str(exc))
    return Response(image, media_type='image/png', headers={'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff'})


class ReviewedPage(BaseModel):
    page: int = Field(ge=1, le=300)
    text: str = Field(max_length=100_000)


class ReviewUpdate(BaseModel):
    pages: list[ReviewedPage] = Field(min_length=1, max_length=300)


@app.put('/api/admin/documents/{identifier}/review', dependencies=[Depends(admin)])
def save_review(identifier: str, request: ReviewUpdate):
    with connect() as db:
        document = db.execute('SELECT * FROM documents WHERE id=?', (identifier,)).fetchone()
        if document is None:
            raise HTTPException(404, 'Document not found.')
        current = db.execute('SELECT * FROM document_pages WHERE document_id=? ORDER BY page', (identifier,)).fetchall()
        numbers = [page.page for page in request.pages]
        if sorted(numbers) != [page['page'] for page in current] or len(set(numbers)) != len(numbers):
            raise HTTPException(422, 'Submit each original page exactly once.')
        if not any(page.text.strip() for page in request.pages) or sum(len(page.text) for page in request.pages) > 3_000_000:
            raise HTTPException(422, 'Provide readable page text within the document size limit.')
        snapshot_pages(db, identifier, 'Before reviewed text update')
        edits = {page.page: page.text for page in request.pages}
        pages = [ExtractedPage(row['page'], edits[row['page']], row['method'], json.loads(row['warnings']), row['confidence'], True) for row in current]
        store_pages(db, identifier, pages)
        if document['status'] != 'archived':
            db.execute("UPDATE documents SET status='pending' WHERE id=?", (identifier,))
    return {'saved': True, 'status': 'archived' if document['status'] == 'archived' else 'pending'}


class ReprocessRequest(BaseModel):
    force_ocr: bool = True


@app.post('/api/admin/documents/{identifier}/reprocess', dependencies=[Depends(admin)])
async def reprocess(identifier: str, request: ReprocessRequest):
    with connect() as db:
        document = db.execute('SELECT * FROM documents WHERE id=?', (identifier,)).fetchone()
    if document is None:
        raise HTTPException(404, 'Document not found.')
    suffix = '.pdf' if document['media_type'] == 'application/pdf' else '.txt'
    try:
        pages, _, _ = await run_in_threadpool(extract_document, bytes(document['original']), suffix, request.force_ocr)
    except ExtractionBusy as exc:
        raise HTTPException(429, str(exc), headers={'Retry-After': '5'})
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    with connect() as db:
        snapshot_pages(db, identifier, 'Before re-extraction')
        store_pages(db, identifier, pages)
        # Preserve an explicit archive action, including one performed while OCR ran.
        db.execute("UPDATE documents SET status='pending' WHERE id=? AND status!='archived'", (identifier,))
    return {'processed': True, 'pages': len(pages)}


@app.get('/api/documents/{identifier}/source')
def source(identifier: str):
    with connect() as db:
        row = db.execute('SELECT d.* FROM documents d WHERE d.id=? AND ' + active_clause(), (identifier, date.today().isoformat())).fetchone()
    if row is None:
        raise HTTPException(404, 'Document unavailable.')
    return Response(bytes(row['original']), media_type=row['media_type'], headers={'Content-Disposition': 'inline', 'X-Content-Type-Options': 'nosniff'})

async def retrieve_sources(question):
    if len(question.strip()) < 2:
        raise HTTPException(422, 'Enter a question with at least two characters.')
    tokens = list(dict.fromkeys(w for w in re.findall(r'[a-z0-9]+', question.lower()) if w not in STOP))[:24]
    query = ' OR '.join('"' + w + '"' for w in tokens)
    with connect() as db:
        if database.using_postgres():
            rows = db.execute('''WITH q AS (SELECT websearch_to_tsquery('english', ?) AS query)
                SELECT c.text,c.page,d.id,d.title,d.sample,d.department,ts_rank_cd(c.search,q.query) AS rank
                FROM chunks c JOIN documents d ON d.id=c.document_id CROSS JOIN q
                WHERE c.search @@ q.query AND ''' + active_clause() + ' ORDER BY rank DESC LIMIT 20',
                (query, date.today().isoformat())).fetchall() if query else []
        else:
            rows = db.execute('''SELECT c.text,c.page,d.id,d.title,d.sample,d.department,bm25(search) AS rank
                FROM search JOIN chunks c ON c.id=search.rowid JOIN documents d ON d.id=c.document_id
                WHERE search MATCH ? AND ''' + active_clause() + ' ORDER BY rank LIMIT 20',
                (query, date.today().isoformat())).fetchall() if query else []
    keyword_backend = 'postgres-fts' if database.using_postgres() else 'sqlite-fts5'
    retrieval = keyword_backend
    retrieval_error = None
    if semantic.configured():
        try:
            semantic_rows = await run_in_threadpool(vector_index.search, question)
            rows = semantic.fuse(rows, semantic_rows)
            retrieval = 'hybrid-pgvector'
        except Exception as exc:
            # Retrieval continues when PostgreSQL or the embedding provider is down.
            rows = rows[:4]
            retrieval = keyword_backend + '-fallback'
            retrieval_error = semantic.failure_reason(exc)
    else:
        rows = rows[:4]
    sources = [{'id': i + 1, 'document_id': row['id'], 'title': row['title'], 'page': row['page'], 'excerpt': row['text'], 'sample': bool(row['sample']), 'url': f"/api/documents/{row['id']}/source#page={row['page']}"} for i, row in enumerate(rows)]
    return sources, retrieval, retrieval_error


@app.post('/api/admin/evaluation/retrieval', dependencies=[Depends(admin)])
async def evaluate_retrieval(request: Question):
    # Read-only path through the exact production retriever. No model generation
    # or answer-history write; a verified staff session is required.
    sources, retrieval, retrieval_error = await retrieve_sources(request.question.strip())
    return {'sources': sources, 'retrieval': retrieval, 'retrieval_error': retrieval_error, 'mode': 'retrieval-only', 'answer': '', 'fallback': not bool(sources)}


@app.post('/api/chat')
async def chat(request: Question):
    question = request.question.strip()
    if len(question) < 2:
        raise HTTPException(422, 'Enter a question with at least two characters.')
    sources, retrieval, retrieval_error = await retrieve_sources(question)
    answer = 'I could not find enough information in the current approved documents. Please check with Student Services or the relevant department.'
    mode = 'extractive'
    generation_model, generation_error = None, None
    fallback = not bool(sources)
    if sources:
        answer = 'Here are the closest matching passages. They may not fully answer your question:\n\n' + '\n\n'.join(f"{s['excerpt']} [{s['id']}]" for s in sources[:2])
        if os.getenv('GEMINI_API_KEY'):
            try:
                response = await generation.answer(question, sources)
                answer, mode, fallback = response['answer'], 'gemini', not response['supported']
                generation_model = response['model']
            except generation.GenerationUnavailable as exc:
                mode = 'extractive-fallback'
                generation_error = exc.reason
    result = {'id': str(uuid.uuid4()), 'answer': answer, 'sources': sources, 'mode': mode, 'generation_model': generation_model, 'generation_error': generation_error, 'retrieval': retrieval, 'retrieval_error': retrieval_error, 'fallback': fallback, 'sample': any(s['sample'] for s in sources)}
    with connect() as db:
        db.execute('INSERT INTO answers VALUES(?,?,?)', (result['id'], question, json.dumps(result)))
    return result

@app.post('/api/feedback')
def feedback(request: Feedback):
    with connect() as db:
        if not db.execute('SELECT 1 FROM answers WHERE id=?', (request.answer_id,)).fetchone():
            raise HTTPException(404, 'Answer not found.')
        db.execute('INSERT INTO feedback VALUES(?,?,?,?)', (str(uuid.uuid4()), request.answer_id, int(request.helpful), datetime.now(timezone.utc).isoformat()))
    return {'saved': True}

@app.post('/api/admin/documents', dependencies=[Depends(admin)])
async def upload(file: UploadFile = File(...), title: str = Form(..., min_length=2, max_length=200), department: str = Form('Student Services', max_length=100), expires_on: str = Form('')):
    if len(title.strip()) < 2 or not department.strip():
        raise HTTPException(422, 'Provide a document title and department.')
    suffix = Path(file.filename or '').suffix.lower()
    if suffix not in {'.pdf', '.txt', '.md', '.docx'}:
        raise HTTPException(400, 'Upload a PDF, DOCX, TXT or Markdown file.')
    content = await file.read(10 * 1024 * 1024 + 1)
    if len(content) > 10 * 1024 * 1024:
        raise HTTPException(413, 'Maximum file size is 10 MB.')
    if expires_on:
        try:
            date.fromisoformat(expires_on)
        except ValueError:
            raise HTTPException(422, 'Expiry must be YYYY-MM-DD.')
    try:
        pages, content, media_type = await run_in_threadpool(extract_document, content, suffix)
    except ExtractionBusy as exc:
        raise HTTPException(429, str(exc), headers={'Retry-After': '5'})
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except Exception:
        raise HTTPException(400, 'Could not extract text. Use an unencrypted PDF, DOCX, or UTF-8 text file.')
    with connect() as db:
        identifier = add_document(db, title.strip(), department, 'pending', expires_on or None, False, file.filename or '', media_type, content, pages)
    return {'id': identifier, 'status': 'pending'}

def document_action(identifier: str, action: str):
    if action not in {'approve', 'archive'}:
        raise HTTPException(404, 'Unknown action.')
    status = 'approved' if action == 'approve' else 'archived'
    with connect() as db:
        if action == 'approve':
            if db.execute('SELECT 1 FROM document_replacements WHERE previous_id=?', (identifier,)).fetchone():
                raise HTTPException(409, 'Approve the latest replacement, not an earlier version.')
            unreviewed = db.execute("SELECT 1 FROM document_pages WHERE document_id=? AND reviewed=0 AND method IN ('ocr','text-fallback','legacy') LIMIT 1", (identifier,)).fetchone()
            if unreviewed:
                raise HTTPException(409, 'Compare the extracted pages with the original and save the review before approving.')
        result = db.execute('UPDATE documents SET status=? WHERE id=?', (status, identifier))
        if result.rowcount == 0:
            raise HTTPException(404, 'Document not found.')
        if action == 'approve':
            current = identifier
            while True:
                parent = db.execute('SELECT previous_id FROM document_replacements WHERE replacement_id=?', (current,)).fetchone()
                if not parent:
                    break
                current = parent['previous_id']
                db.execute("UPDATE documents SET status='archived' WHERE id=?", (current,))
    return {'id': identifier, 'status': status}


@app.get('/api/admin/system/status', dependencies=[Depends(admin)])
def system_status():
    from datetime import timedelta
    with connect() as db:
        jobs = db.execute('''SELECT j.document_id,d.title,j.attempts,j.error,j.next_attempt
            FROM vector_jobs j JOIN documents d ON d.id=j.document_id WHERE j.error IS NOT NULL ORDER BY j.next_attempt LIMIT 50''').fetchall()
        expiring = db.execute('''SELECT id,title,expires_on FROM documents
            WHERE status='approved' AND expires_on IS NOT NULL AND expires_on<=? ORDER BY expires_on''',
            ((date.today()+timedelta(days=30)).isoformat(),)).fetchall()
    return {'storage': 'Supabase PostgreSQL' if database.using_postgres() else 'Local SQLite',
            'providers': runtime_status.snapshot(), 'index': vector_index.status(),
            'failed_jobs': [dict(job) for job in jobs], 'expiry_warnings': [dict(doc) for doc in expiring]}


@app.post('/api/admin/system/check', dependencies=[Depends(admin)])
async def check_providers():
    if not os.getenv('GEMINI_API_KEY'):
        runtime_status.record('generation', error='not_configured')
        runtime_status.record('embeddings', error='not_configured')
    else:
        async def check_generation():
            try:
                await generation.answer('What is the demonstration library rule?',
                    [{'id': 1, 'sample': True, 'excerpt': 'Demonstration: return borrowed library books on time.'}])
            except generation.GenerationUnavailable:
                pass
        async def check_embeddings():
            try:
                await run_in_threadpool(semantic.embed, 'Campus information search')
            except Exception:
                pass
        await asyncio.gather(check_generation(), check_embeddings())
    return await run_in_threadpool(system_status)


@app.post('/api/admin/search/{identifier}/retry', dependencies=[Depends(admin)], status_code=202)
def retry_index(identifier: str):
    with connect() as db:
        if not db.execute('SELECT 1 FROM documents WHERE id=?', (identifier,)).fetchone():
            raise HTTPException(404, 'Document not found.')
        token = uuid.uuid4().hex
        db.execute('''INSERT INTO vector_jobs(document_id,token) VALUES(?,?)
            ON CONFLICT(document_id) DO UPDATE SET token=excluded.token,attempts=0,next_attempt=0,error=NULL''', (identifier,token))
    return {'queued': True}


class MetadataUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=2, max_length=200)
    department: str | None = Field(default=None, min_length=1, max_length=100)
    expires_on: str | None = None


@app.patch('/api/admin/documents/{identifier}', dependencies=[Depends(admin)])
def update_document(identifier: str, request: MetadataUpdate):
    values = request.model_dump(exclude_unset=True)
    for field in ['title','department']:
        if field in values:
            if values[field] is None or len(values[field].strip()) < (2 if field=='title' else 1):
                raise HTTPException(422, 'Provide a title and department.')
            values[field] = values[field].strip()
    if values.get('expires_on'):
        try:
            date.fromisoformat(values['expires_on'])
        except ValueError:
            raise HTTPException(422, 'Expiry must be YYYY-MM-DD.')
    elif 'expires_on' in values:
        values['expires_on'] = None
    if not values:
        raise HTTPException(422, 'Provide at least one change.')
    with connect() as db:
        result = db.execute('UPDATE documents SET '+','.join(field+'=?' for field in values)+
            ",status=CASE WHEN status='archived' THEN 'archived' ELSE 'pending' END WHERE id=?", (*values.values(),identifier))
        if not result.rowcount:
            raise HTTPException(404, 'Document not found.')
    return {'saved': True}


@app.post('/api/admin/documents/{identifier}/replace', dependencies=[Depends(admin)])
async def replace_document(identifier: str, file: UploadFile = File(...)):
    with connect() as db:
        old = db.execute('SELECT * FROM documents WHERE id=?', (identifier,)).fetchone()
        successor = db.execute('SELECT replacement_id FROM document_replacements WHERE previous_id=?', (identifier,)).fetchone()
    if old is None:
        raise HTTPException(404, 'Document not found.')
    if successor:
        raise HTTPException(409, 'A replacement already exists. Open the latest version to replace it.')
    result = await upload(file=file, title=old['title'], department=old['department'], expires_on=old['expires_on'] or '')
    replacement_id = result['id']
    try:
        with connect() as db:
            db.execute('INSERT INTO document_replacements VALUES(?,?)', (replacement_id,identifier))
    except (sqlite3.IntegrityError, database.psycopg.IntegrityError):
        # The newly uploaded pending file remains visible to staff, never public.
        raise HTTPException(409, 'Another replacement was submitted. The extra upload is retained as pending.')
    return {**result, 'previous_id': identifier}


def version_ids(db, identifier):
    if not db.execute('SELECT 1 FROM documents WHERE id=?', (identifier,)).fetchone():
        raise HTTPException(404, 'Document not found.')
    current, seen = identifier, set()
    while current not in seen:
        seen.add(current)
        parent = db.execute('SELECT previous_id FROM document_replacements WHERE replacement_id=?', (current,)).fetchone()
        if not parent:
            break
        current = parent['previous_id']
    result, seen = [], set()
    while current not in seen:
        seen.add(current)
        result.append(current)
        child = db.execute('SELECT replacement_id FROM document_replacements WHERE previous_id=?', (current,)).fetchone()
        if not child:
            break
        current = child['replacement_id']
    return result


@app.get('/api/admin/documents/{identifier}/history', dependencies=[Depends(admin)])
def document_history(identifier: str):
    with connect() as db:
        versions = []
        for number, version in enumerate(version_ids(db,identifier),1):
            row = db.execute('SELECT id,title,filename,status,created_at FROM documents WHERE id=?', (version,)).fetchone()
            versions.append({**dict(row),'version': number})
        revisions = db.execute('SELECT id,created_at,reason FROM document_revisions WHERE document_id=? ORDER BY created_at DESC', (identifier,)).fetchall()
    return {'versions': versions, 'revisions': [dict(row) for row in revisions]}


@app.get('/api/admin/documents/{identifier}/revisions/{revision_id}', dependencies=[Depends(admin)])
def revision_detail(identifier: str, revision_id: str):
    with connect() as db:
        row = db.execute('SELECT pages FROM document_revisions WHERE id=? AND document_id=?', (revision_id,identifier)).fetchone()
    if row is None:
        raise HTTPException(404, 'Revision not found.')
    return {'pages': json.loads(row['pages'])}


@app.get('/api/admin/documents/{identifier}/original', dependencies=[Depends(admin)])
def admin_original(identifier: str):
    with connect() as db:
        row = db.execute('SELECT original,media_type FROM documents WHERE id=?', (identifier,)).fetchone()
    if row is None:
        raise HTTPException(404, 'Document not found.')
    return Response(bytes(row['original']),media_type=row['media_type'],headers={'Content-Disposition':'inline'})


@app.post('/api/admin/documents/{identifier}/approve', dependencies=[Depends(admin)])
def approve_document(identifier: str):
    return document_action(identifier,'approve')


@app.post('/api/admin/documents/{identifier}/archive', dependencies=[Depends(admin)])
def archive_document(identifier: str):
    return document_action(identifier,'archive')
