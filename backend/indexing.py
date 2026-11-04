"""Durable indexing queue: source mutations and invalidation commit together."""
import asyncio
import hashlib
import json
import time
from datetime import date

from backend import semantic

SCHEMA = '''
CREATE TABLE IF NOT EXISTS vector_jobs (
 document_id TEXT PRIMARY KEY, token TEXT NOT NULL,
 attempts INTEGER NOT NULL DEFAULT 0, next_attempt REAL NOT NULL DEFAULT 0,
 error TEXT);
CREATE TABLE IF NOT EXISTS embedding_cache (
 cache_key TEXT PRIMARY KEY, model TEXT NOT NULL, dimensions INTEGER NOT NULL,
 embedding TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS vector_manifest (
 document_id TEXT PRIMARY KEY, revision TEXT NOT NULL, model TEXT NOT NULL,
 chunks INTEGER NOT NULL, indexed_at REAL NOT NULL);
CREATE TRIGGER IF NOT EXISTS vector_document_insert AFTER INSERT ON documents BEGIN
 INSERT INTO vector_jobs(document_id,token) VALUES(new.id,hex(randomblob(16)))
 ON CONFLICT(document_id) DO UPDATE SET token=hex(randomblob(16)),attempts=0,next_attempt=0,error=NULL;
END;
CREATE TRIGGER IF NOT EXISTS vector_document_update AFTER UPDATE ON documents BEGIN
 INSERT INTO vector_jobs(document_id,token) VALUES(new.id,hex(randomblob(16)))
 ON CONFLICT(document_id) DO UPDATE SET token=hex(randomblob(16)),attempts=0,next_attempt=0,error=NULL;
END;
CREATE TRIGGER IF NOT EXISTS vector_chunk_insert AFTER INSERT ON chunks BEGIN
 INSERT INTO vector_jobs(document_id,token) VALUES(new.document_id,hex(randomblob(16)))
 ON CONFLICT(document_id) DO UPDATE SET token=hex(randomblob(16)),attempts=0,next_attempt=0,error=NULL;
END;
CREATE TRIGGER IF NOT EXISTS vector_chunk_delete AFTER DELETE ON chunks BEGIN
 INSERT INTO vector_jobs(document_id,token) VALUES(old.document_id,hex(randomblob(16)))
 ON CONFLICT(document_id) DO UPDATE SET token=hex(randomblob(16)),attempts=0,next_attempt=0,error=NULL;
END;
CREATE TRIGGER IF NOT EXISTS vector_chunk_update AFTER UPDATE ON chunks BEGIN
 INSERT INTO vector_jobs(document_id,token) VALUES(new.document_id,hex(randomblob(16)))
 ON CONFLICT(document_id) DO UPDATE SET token=hex(randomblob(16)),attempts=0,next_attempt=0,error=NULL;
END;
'''


def enqueue_all(db):
    token = "md5(random()::text || clock_timestamp()::text)" if getattr(db, 'dialect', '') == 'postgres' else 'hex(randomblob(16))'
    db.execute(f'''INSERT INTO vector_jobs(document_id,token)
        SELECT id,{token} FROM documents WHERE true
        ON CONFLICT(document_id) DO UPDATE SET token=excluded.token,attempts=0,next_attempt=0,error=NULL''')


def active(document):
    return document and document['status'] == 'approved' and (not document['expires_on'] or document['expires_on'] >= date.today().isoformat())


class VectorIndexer:
    def __init__(self, connect):
        self.connect = connect

    def initialize(self, db):
        postgres = getattr(db, 'dialect', '') == 'postgres'
        if not postgres:
            db.executescript(SCHEMA)
        if semantic.configured():
            token = "md5(random()::text || clock_timestamp()::text)" if postgres else 'hex(randomblob(16))'
            db.execute(f'''INSERT INTO vector_jobs(document_id,token)
                SELECT d.id,{token} FROM documents d
                LEFT JOIN vector_manifest m ON m.document_id=d.id
                WHERE m.document_id IS NULL OR m.model!=?
                ON CONFLICT(document_id) DO NOTHING''', (semantic.model(),))

    def status(self):
        with self.connect() as db:
            counts = db.execute('''SELECT
                (SELECT count(*) FROM vector_jobs) AS pending,
                (SELECT count(*) FROM vector_jobs WHERE error IS NOT NULL) AS failed,
                (SELECT count(*) FROM vector_manifest m JOIN documents d ON d.id=m.document_id
                WHERE m.model=? AND m.chunks>0 AND d.status='approved'
                AND (d.expires_on IS NULL OR d.expires_on>=?)
                AND NOT EXISTS (SELECT 1 FROM vector_jobs j WHERE j.document_id=d.id)) AS indexed''',
                (semantic.model(), date.today().isoformat())).fetchone()
        return {'configured': semantic.configured(), 'model': semantic.model(), 'dimensions': semantic.DIMENSIONS,
                'pending_documents': counts['pending'], 'failed_documents': counts['failed'], 'indexed_documents': counts['indexed']}

    def process_one(self):
        with self.connect() as db:
            job = db.execute('SELECT * FROM vector_jobs WHERE next_attempt<=? ORDER BY next_attempt,document_id LIMIT 1', (time.time(),)).fetchone()
            if job is None:
                return False
            document = db.execute('SELECT id,title,status,expires_on FROM documents WHERE id=?', (job['document_id'],)).fetchone()
            chunks = [dict(row) for row in db.execute('SELECT page,text FROM chunks WHERE document_id=? ORDER BY id', (job['document_id'],))]
        try:
            document_revision = semantic.revision(chunks)
            vectors = []
            if active(document):
                for chunk in chunks:
                    key = semantic.chunk_key(job['document_id'], chunk['page'], chunk['text'])
                    cache_key = hashlib.sha256(json.dumps([semantic.model(), semantic.DIMENSIONS, document['title'], key]).encode()).hexdigest()
                    with self.connect() as db:
                        cached = db.execute('SELECT embedding FROM embedding_cache WHERE cache_key=?', (cache_key,)).fetchone()
                    if cached:
                        embedding = semantic.normalize(json.loads(cached['embedding']))
                    else:
                        embedding = semantic.embed(chunk['text'], document=True, title=document['title'])
                        with self.connect() as db:
                            db.execute('''INSERT INTO embedding_cache VALUES(?,?,?,?) ON CONFLICT(cache_key) DO UPDATE
                                SET model=excluded.model,dimensions=excluded.dimensions,embedding=excluded.embedding''',
                                (cache_key, semantic.model(), semantic.DIMENSIONS, json.dumps(embedding)))
                    vectors.append({**chunk, 'key': key, 'embedding': embedding})
            semantic.replace_document(job['document_id'], document_revision, vectors)
            with self.connect() as db:
                # A concurrent review/archive creates a new token; never erase it.
                result = db.execute('DELETE FROM vector_jobs WHERE document_id=? AND token=?', (job['document_id'], job['token']))
                if result.rowcount:
                    db.execute('''INSERT INTO vector_manifest VALUES(?,?,?,?,?) ON CONFLICT(document_id) DO UPDATE
                        SET revision=excluded.revision,model=excluded.model,chunks=excluded.chunks,indexed_at=excluded.indexed_at''',
                        (job['document_id'], document_revision, semantic.model(), len(vectors), time.time()))
            return True
        except Exception as exc:
            # Store a diagnostic class, never a URI, credentials, or provider body.
            with self.connect() as db:
                db.execute('''UPDATE vector_jobs SET attempts=attempts+1,next_attempt=?,error=?
                    WHERE document_id=? AND token=?''',
                    (time.time() + min(300, 15 * 2 ** min(job['attempts'], 4)), type(exc).__name__, job['document_id'], job['token']))
            return False

    async def run(self):
        while True:
            if semantic.configured():
                try:
                    worked = await asyncio.to_thread(self.process_one)
                except Exception:
                    # A dropped database connection before claiming a job must
                    # not kill the worker. Pending jobs remain durable.
                    worked = False
                if worked:
                    continue
            await asyncio.sleep(5)

    def search(self, question):
        candidates = semantic.search(semantic.embed(question), limit=24)
        # The remote index is derived and may lag. Validate revision, status and
        # expiry against the authoritative local database before returning text.
        results, snapshots = [], {}
        with self.connect() as db:
            for candidate in candidates:
                identifier = candidate['document_id']
                if identifier not in snapshots:
                    document = db.execute('SELECT id,title,department,status,expires_on,sample FROM documents WHERE id=?', (identifier,)).fetchone()
                    chunks = [dict(row) for row in db.execute('SELECT page,text FROM chunks WHERE document_id=? ORDER BY id', (identifier,))]
                    snapshots[identifier] = (document, chunks, semantic.revision(chunks))
                document, chunks, current_revision = snapshots[identifier]
                if not active(document) or candidate['revision'] != current_revision or candidate['similarity'] < 0.5:
                    continue
                for chunk in chunks:
                    if semantic.chunk_key(identifier, chunk['page'], chunk['text']) == candidate['chunk_key']:
                        results.append({**dict(document), **chunk})
                        break
        return results
