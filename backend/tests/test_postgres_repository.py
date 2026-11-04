import os
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from backend import main, database


@unittest.skipUnless(os.getenv('TEST_DATABASE_URL'), 'Set TEST_DATABASE_URL for Supabase repository integration tests')
class PostgresRepositoryTests(unittest.TestCase):
    def test_float_fields_round_trip_without_timestamp_precision_loss(self):
        with patch.dict(os.environ, {'DATABASE_URL': os.environ['TEST_DATABASE_URL']}):
            timestamp, confidence = 1792900314.1912346, 0.9789012345678912
            with database.postgres_connection() as db:
                row = db.execute('SELECT ?::double precision AS indexed_at, ?::double precision AS confidence',
                                 (timestamp, confidence)).fetchone()
                self.assertEqual(row['indexed_at'], timestamp)
                self.assertEqual(row['confidence'], confidence)

    def test_cloud_upload_review_approval_original_download_and_archive(self):
        marker = 'cloudfixture' + uuid.uuid4().hex
        auth = {'Authorization': 'Bearer integration-session'}
        identifier = None
        replacement_id = None
        with patch.dict(os.environ, {'DOCUMENT_BACKEND': 'postgres', 'DATABASE_URL': os.environ['TEST_DATABASE_URL'],
                                     'GEMINI_API_KEY': ''}):
            # Apply the same idempotent, private schema as the real migration.
            sql = (Path(__file__).resolve().parents[2] / 'supabase/migrations/003_document_repository.sql').read_text(encoding='utf-8')
            with database.postgres_connection() as db:
                db.raw.execute(sql)
                db.raw.execute((Path(__file__).resolve().parents[2] / 'supabase/migrations/004_document_versions.sql').read_text(encoding='utf-8'))
            try:
                with patch.object(main.auth, 'require_staff', side_effect=lambda request, connect: {'id': 'fixture', 'role': 'admin'} if request.headers.get('authorization') == 'Bearer integration-session' else (_ for _ in ()).throw(main.HTTPException(401, 'Sign in'))), TestClient(main.app) as client:
                    self.assertEqual(client.post('/api/admin/search/reindex', headers=auth).status_code, 202)
                    original = f'{marker} The telescope opens at midnight.'.encode()
                    upload = client.post('/api/admin/documents', headers=auth,
                        data={'title': marker}, files={'file': ('test.txt', original, 'text/plain')})
                    self.assertEqual(upload.status_code, 200, upload.text)
                    identifier = upload.json()['id']
                    source = f'/api/documents/{identifier}/source'
                    self.assertEqual(client.get(source).status_code, 404)
                    self.assertEqual(client.post(f'/api/admin/documents/{identifier}/approve', headers=auth).status_code, 200)
                    self.assertEqual(client.get(source).content, original)
                    answer = client.post('/api/chat', json={'question': marker}).json()
                    self.assertEqual(answer['retrieval'], 'postgres-fts')
                    self.assertEqual(answer['sources'][0]['document_id'], identifier)
                    result = client.put(f'/api/admin/documents/{identifier}/review', headers=auth,
                        json={'pages': [{'page': 1, 'text': marker+' The telescope closes at sunset.'}]})
                    self.assertEqual(result.status_code, 200, result.text)
                    self.assertEqual(client.get(source).status_code, 404)
                    with main.connect() as db:
                        self.assertEqual(db.execute('SELECT count(*) FROM document_revisions WHERE document_id=?', (identifier,)).fetchone()[0], 1)
                        self.assertIsNotNone(db.execute('SELECT 1 FROM vector_jobs WHERE document_id=?', (identifier,)).fetchone())
                    client.post(f'/api/admin/documents/{identifier}/approve', headers=auth)
                    self.assertEqual(client.get(source).content, original)
                    replacement = client.post(f'/api/admin/documents/{identifier}/replace', headers=auth,
                        files={'file': ('replacement.txt', (marker+' New telescope policy.').encode(), 'text/plain')})
                    self.assertEqual(replacement.status_code,200,replacement.text)
                    replacement_id = replacement.json()['id']
                    self.assertEqual(client.get(source).status_code,200)
                    self.assertEqual(client.post(f'/api/admin/documents/{replacement_id}/approve',headers=auth).status_code,200)
                    self.assertEqual(client.get(source).status_code,404)
                    self.assertEqual(client.get(f'/api/admin/documents/{identifier}/original',headers=auth).content,original)
                    self.assertEqual(len(client.get(f'/api/admin/documents/{replacement_id}/history',headers=auth).json()['versions']),2)
                    client.post(f'/api/admin/documents/{replacement_id}/archive',headers=auth)
                    client.post(f'/api/admin/documents/{identifier}/archive', headers=auth)
                    self.assertEqual(client.get(source).status_code, 404)
                    self.assertEqual(client.get(f'/api/admin/documents/{identifier}/preview').status_code, 401)
                    self.assertTrue(client.post('/api/chat', json={'question': marker}).json()['fallback'])
            finally:
                with database.postgres_connection() as db:
                    db.execute('DELETE FROM answers WHERE question=?', (marker,))
                    if identifier:
                        db.execute('DELETE FROM document_replacements WHERE previous_id=? OR replacement_id=?',(identifier,replacement_id or identifier))
                        for version in [replacement_id,identifier]:
                            if not version: continue
                            for table in ['document_revisions', 'document_pages', 'chunks']:
                                db.execute(f'DELETE FROM {table} WHERE document_id=?', (version,))
                            db.execute('DELETE FROM documents WHERE id=?', (version,))
                            db.execute('DELETE FROM vector_jobs WHERE document_id=?', (version,))

    def test_transaction_rolls_back_and_originals_have_rls(self):
        identifier = str(uuid.uuid4())
        with patch.dict(os.environ, {'DATABASE_URL': os.environ['TEST_DATABASE_URL']}):
            with self.assertRaises(ValueError):
                with database.postgres_connection() as db:
                    db.execute('INSERT INTO documents VALUES(?,?,?,?,?,?,?,?,?,?)',
                        (identifier, 'Rollback fixture', 'Test', 'pending', '2026-01-01', None, 0, 'test.txt', 'text/plain', b'bytes'))
                    raise ValueError('Rollback')
            with database.postgres_connection() as db:
                self.assertIsNone(db.execute('SELECT 1 FROM documents WHERE id=?', (identifier,)).fetchone())
                self.assertTrue(db.execute("SELECT relrowsecurity FROM pg_class WHERE oid='campus.documents'::regclass").fetchone()[0])
