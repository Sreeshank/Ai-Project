"""Consistent, checksummed backups. Restores require an empty migrated database."""
import argparse
import base64
import gzip
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

TABLES = {
    'documents': 'id,title,department,status,created_at,expires_on,sample,filename,media_type,original',
    'chunks': 'id,document_id,page,text',
    'document_pages': 'document_id,page,text,method,warnings,confidence,reviewed',
    'document_revisions': 'id,document_id,created_at,reason,pages',
    'document_replacements': 'replacement_id,previous_id',
    'answers': 'id,question,payload',
    'feedback': 'id,answer_id,helpful,created_at',
    'embedding_cache': 'cache_key,model,dimensions,embedding',
    'vector_manifest': 'document_id,revision,model,chunks,indexed_at',
    'vector_jobs': 'document_id,token,attempts,next_attempt,error',
}


def encode(value):
    return {'base64_bytes': base64.b64encode(bytes(value)).decode()} if isinstance(value, (bytes, memoryview)) else value


def decode(value):
    return base64.b64decode(value['base64_bytes'], validate=True) if isinstance(value, dict) and set(value)=={'base64_bytes'} else value


def create(connect, destination):
    with connect() as db:
        if getattr(db, 'dialect', '') == 'postgres':
            db.raw.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        else:
            db.execute('BEGIN')
        tables = {table: [{key: encode(value) for key,value in dict(row).items()}
                          for row in db.execute('SELECT '+columns+' FROM '+table)] for table,columns in TABLES.items()}
    payload = {'format': 1, 'created_at': datetime.now(timezone.utc).isoformat(), 'tables': tables}
    content = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()
    envelope = {'sha256': hashlib.sha256(content).hexdigest(), 'payload': payload}
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive creation avoids silently overwriting an existing backup.
    with destination.open('xb') as raw, gzip.GzipFile(fileobj=raw, mode='wb') as stream:
        stream.write(json.dumps(envelope, ensure_ascii=False).encode())
    verify(destination)
    return {table: len(rows) for table,rows in tables.items()}


def verify(path):
    with gzip.open(path,'rt',encoding='utf-8') as stream:
        envelope = json.load(stream)
    payload = envelope['payload']
    content = json.dumps(payload,ensure_ascii=False,sort_keys=True,separators=(',', ':')).encode()
    if payload.get('format')!=1 or hashlib.sha256(content).hexdigest()!=envelope['sha256'] or set(payload['tables'])!=set(TABLES):
        raise ValueError('Backup checksum or format mismatch')
    for table, rows in payload['tables'].items():
        for row in rows:
            if set(row)!=set(TABLES[table].split(',')):
                raise ValueError('Unexpected backup columns')
            for value in row.values():
                decode(value)
    return payload


def restore(connect, path):
    payload = verify(path)
    with connect() as db:
        for table in TABLES:
            if db.execute('SELECT 1 FROM '+table+' LIMIT 1').fetchone():
                raise ValueError('Restore refused: target database must be empty')
        for table, columns in TABLES.items():
            names = columns.split(',')
            for row in payload['tables'][table]:
                if table=='vector_jobs':
                    # Source triggers have already created conservative rebuild jobs.
                    continue
                db.execute('INSERT INTO '+table+' ('+columns+') VALUES ('+','.join('?' for _ in names)+')',
                           tuple(decode(row[name]) for name in names))
        if getattr(db,'dialect','')=='postgres':
            db.execute("SELECT setval(pg_get_serial_sequence('campus.chunks','id'), COALESCE((SELECT max(id) FROM chunks),1), EXISTS(SELECT 1 FROM chunks))")
        from backend.indexing import enqueue_all
        enqueue_all(db)
    return {table:len(rows) for table,rows in payload['tables'].items()}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['create','verify','restore'])
    parser.add_argument('--file',type=Path)
    args = parser.parse_args()
    from backend import main
    path = args.file or Path('work/backups') / ('campus-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'.json.gz')
    try:
        if args.action=='create': result=create(main.connect,path)
        elif args.action=='verify': result={table:len(rows) for table,rows in verify(path)['tables'].items()}
        else: result=restore(main.connect,path)
        print(json.dumps({'file':str(path),'counts':result},indent=2))
    except Exception as exc:
        parser.exit(1, 'Backup operation failed ('+type(exc).__name__+'). Check the file and target database; credentials are not logged.\n')
