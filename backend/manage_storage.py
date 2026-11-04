"""Copy and verify SQLite into private Supabase tables before switching storage."""
import sqlite3
from pathlib import Path

from dotenv import load_dotenv
from backend.database import postgres_connection

ROOT = Path(__file__).resolve().parent
TABLES = ['documents', 'chunks', 'document_pages', 'document_revisions', 'document_replacements', 'answers',
          'feedback', 'embedding_cache', 'vector_manifest']


def migrate(source_path):
    sql = (ROOT.parent / 'supabase/migrations/003_document_repository.sql').read_text(encoding='utf-8')
    sql += '\n' + (ROOT.parent / 'supabase/migrations/004_document_versions.sql').read_text(encoding='utf-8')
    with sqlite3.connect(source_path) as source, postgres_connection() as target:
        source.row_factory = sqlite3.Row
        source.execute('BEGIN')
        target.raw.execute(sql)
        report = {}
        for table in TABLES:
            if table == 'document_replacements' and not source.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone():
                report[table] = 0
                continue
            records = source.execute(f'SELECT * FROM {table}').fetchall()
            if not records:
                report[table] = 0
                continue
            columns = records[0].keys()
            statement = f"INSERT INTO {table} ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)}) ON CONFLICT DO NOTHING"
            for record in records:
                target.execute(statement, tuple(record))
            # Verify every imported field, including original bytes and revisions.
            keys = ['document_id', 'page'] if table == 'document_pages' else [columns[0]]
            for record in records:
                result = target.execute(f"SELECT {','.join(columns)} FROM {table} WHERE " + ' AND '.join(key+'=?' for key in keys), tuple(record[key] for key in keys)).fetchone()
                differences = [column for column in columns if result is None or result[column] != record[column]]
                if differences:
                    raise ValueError(f'Cloud verification differs in {table} ({", ".join(differences)}); migration rolled back.')
            report[table] = len(records)
        target.execute("SELECT setval(pg_get_serial_sequence('campus.chunks','id'), COALESCE((SELECT max(id) FROM chunks),1), EXISTS(SELECT 1 FROM chunks))")
        # Queue all copied documents so their derived index is checked/rebuilt.
        target.execute('''INSERT INTO vector_jobs(document_id,token)
            SELECT id,md5(random()::text || clock_timestamp()::text) FROM documents
            ON CONFLICT(document_id) DO UPDATE SET token=excluded.token,attempts=0,next_attempt=0,error=NULL''')
        return report


if __name__ == '__main__':
    import json
    load_dotenv(ROOT / '.env')
    try:
        print(json.dumps(migrate(ROOT / 'data/campus.db'), indent=2))
    except Exception as exc:
        # Driver exceptions can include credentials; retain only the class.
        raise SystemExit(f'Migration failed ({type(exc).__name__}). No storage switch was made.') from None
