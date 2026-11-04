"""Gemini embeddings and a private, disposable PostgreSQL/pgvector index."""
import hashlib
import json
import math
import os
from contextlib import contextmanager
from pathlib import Path

import httpx
from backend import runtime_status
import psycopg
from psycopg.rows import dict_row

DIMENSIONS = 768
DEFAULT_MODEL = 'gemini-embedding-001'


def configured():
    return bool(os.getenv('DATABASE_URL') and os.getenv('GEMINI_API_KEY'))


def failure_reason(error):
    """Safe diagnostics for degraded retrieval, without provider response bodies."""
    if isinstance(error, httpx.HTTPStatusError):
        code = error.response.status_code
        if code == 402:
            return 'embedding_billing_required'
        if code in {401, 403}:
            return 'embedding_access_denied'
        if code == 429:
            return 'embedding_rate_limited'
        return 'embedding_provider_busy' if code >= 500 else 'embedding_request_rejected'
    if isinstance(error, httpx.TimeoutException):
        return 'embedding_timeout'
    if isinstance(error, httpx.TransportError):
        return 'embedding_connection_failed'
    if isinstance(error, psycopg.Error):
        return 'vector_database_unavailable'
    return 'semantic_unavailable'


def model():
    # Changing the model requires reindexing; spaces must never be mixed.
    return os.getenv('EMBEDDING_MODEL', DEFAULT_MODEL)


def chunk_key(document_id, page, text):
    return hashlib.sha256(json.dumps([document_id, page, text], ensure_ascii=False).encode()).hexdigest()


def revision(chunks):
    return hashlib.sha256(json.dumps([(c['page'], c['text']) for c in chunks], ensure_ascii=False).encode()).hexdigest()


def normalize(values):
    if not isinstance(values, list) or len(values) != DIMENSIONS:
        raise ValueError('Embedding dimension mismatch')
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in values):
        raise ValueError('Invalid embedding values')
    magnitude = math.sqrt(sum(v * v for v in values))
    if not math.isfinite(magnitude) or magnitude == 0:
        raise ValueError('Invalid embedding norm')
    return [v / magnitude for v in values]


def _embed(text, *, document=False, title=''):
    selected = model()
    if selected not in {'gemini-embedding-001', 'gemini-embedding-2'}:
        raise ValueError('Unsupported embedding model')
    body = {'model': f'models/{selected}', 'outputDimensionality': DIMENSIONS}
    if selected == 'gemini-embedding-001':
        body['taskType'] = 'RETRIEVAL_DOCUMENT' if document else 'RETRIEVAL_QUERY'
        if document:
            body['title'] = title
    else:
        text = f'title: {title} | text: {text}' if document else f'task: search result | query: {text}'
    body['content'] = {'parts': [{'text': text}]}
    response = httpx.post(f'https://generativelanguage.googleapis.com/v1beta/models/{selected}:embedContent',
        headers={'x-goog-api-key': os.environ['GEMINI_API_KEY']}, json=body, timeout=25)
    response.raise_for_status()
    return normalize(response.json()['embedding']['values'])


@contextmanager
def connection():
    # Never include the URI or provider response bodies in API errors/logs.
    with psycopg.connect(os.environ['DATABASE_URL'], connect_timeout=5,
                         options='-c statement_timeout=15000', row_factory=dict_row) as db:
        db.execute('SET LOCAL search_path TO public, extensions')
        yield db


def migrate():
    sql = (Path(__file__).resolve().parents[1] / 'supabase/migrations/002_semantic_index.sql').read_text(encoding='utf-8')
    with connection() as db:
        db.execute(sql)


def replace_document(document_id, document_revision, chunks):
    """Atomic replacement: a failed upload leaves the previous complete index."""
    with connection() as db:
        # Serializes replacements from multiple workers for this document.
        db.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))', (document_id,))
        db.execute('DELETE FROM public.campus_semantic_chunks WHERE document_id=%s', (document_id,))
        for chunk in chunks:
            db.execute('''INSERT INTO public.campus_semantic_chunks
                (chunk_key,document_id,revision,page_number,content,embedding,embedding_model,embedding_dimensions)
                VALUES(%s,%s,%s,%s,%s,%s::extensions.vector,%s,%s)
                ON CONFLICT(chunk_key) DO NOTHING''',
                (chunk['key'], document_id, document_revision, chunk['page'], chunk['text'],
                 json.dumps(chunk['embedding']), model(), DIMENSIONS))


def search(vector, limit=20):
    with connection() as db:
        rows = db.execute('''SELECT chunk_key,document_id::text,revision,
                1-(embedding <=> %s::extensions.vector) AS similarity
            FROM public.campus_semantic_chunks
            WHERE embedding_model=%s AND embedding_dimensions=%s
            ORDER BY embedding <=> %s::extensions.vector LIMIT %s''',
            (json.dumps(vector), model(), DIMENSIONS, json.dumps(vector), limit)).fetchall()
    return rows


def fuse(keyword_rows, semantic_rows, limit=4):
    """Reciprocal rank fusion avoids mixing incomparable BM25/cosine scores."""
    scores, records = {}, {}
    for ranking in (keyword_rows, semantic_rows):
        seen = set()
        for rank, row in enumerate(ranking, 1):
            key = chunk_key(row['id'], row['page'], row['text'])
            if key in seen:
                continue
            seen.add(key)
            scores[key] = scores.get(key, 0) + 1 / (60 + rank)
            records[key] = row
    return [records[key] for key in sorted(scores, key=lambda key: (-scores[key], key))[:limit]]


def embed(text, *, document=False, title=''):
    try:
        result = _embed(text, document=document, title=title)
        runtime_status.record('embeddings', model=model())
        return result
    except Exception as exc:
        runtime_status.record('embeddings', error=failure_reason(exc), model=model())
        raise
