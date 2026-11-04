import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

import httpx

from fastapi.testclient import TestClient
from backend import main, auth


class CampusAPITest(unittest.TestCase):
    def setUp(self):
        scratch = Path(__file__).resolve().parents[2] / 'work' / 'test-data'
        scratch.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=scratch)
        self.environment = patch.dict(os.environ, {'SUPABASE_URL': 'https://test.supabase.co', 'SUPABASE_PUBLISHABLE_KEY': 'test-public-key', 'APP_ORIGIN': 'http://127.0.0.1:3000', 'GEMINI_API_KEY': '', 'DATABASE_URL': '', 'DOCUMENT_BACKEND': 'sqlite', 'RATE_LIMIT_ENABLED': '0', 'GEMINI_FALLBACK_MODELS': ''})
        self.environment.start()
        self.data = patch.object(main, 'DATA', Path(self.temp.name))
        self.database = patch.object(main, 'DB', Path(self.temp.name) / 'test.db')
        self.data.start()
        self.database.start()
        self.client = TestClient(main.app)
        self.client.__enter__()
        self.user_id = '11111111-1111-4111-8111-111111111111'
        with main.connect() as db:
            db.execute('INSERT INTO staff_members(user_id,role,enabled) VALUES(?,?,?)', (self.user_id, 'admin', 1))
        self.verify = patch.object(auth, 'verified_user', return_value={'id': self.user_id, 'email': 'staff@example.com'})
        self.verify.start()
        self.auth = {'Authorization': 'Bearer test-supabase-session'}

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.verify.stop()
        self.database.stop()
        self.data.stop()
        self.environment.stop()
        self.temp.cleanup()

    def upload(self, text='The campus observatory opens at midnight.', expires=''):
        response = self.client.post('/api/admin/documents', headers=self.auth,
            data={'title': 'Observatory policy', 'department': 'Student Services', 'expires_on': expires},
            files={'file': ('observatory.txt', text.encode(), 'text/plain')})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()['id']

    def ask(self, question):
        response = self.client.post('/api/chat', json={'question': question})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_seed_retrieval_citation_and_source(self):
        answer = self.ask('What are the library opening hours?')
        self.assertEqual(answer['mode'], 'extractive')
        self.assertTrue(answer['sample'])
        self.assertIn('8:00 AM', answer['answer'])
        self.assertEqual(answer['sources'][0]['title'], 'Library handbook')
        source = self.client.get(answer['sources'][0]['url'].split('#')[0])
        self.assertEqual(source.status_code, 200)
        self.assertIn('SAMPLE DATA', source.text)

    def test_missing_information_abstains(self):
        answer = self.ask('interplanetary shuttle teleportation')
        self.assertTrue(answer['fallback'])
        self.assertEqual(answer['sources'], [])

    def test_admin_protected_and_disabled_when_unconfigured(self):
        self.assertEqual(self.client.get('/api/admin/documents').status_code, 401)
        self.assertEqual(self.client.get('/api/admin/documents', headers=self.auth).status_code, 200)
        with patch.dict(os.environ, {'SUPABASE_PUBLISHABLE_KEY': '', 'SUPABASE_ANON_KEY': ''}):
            self.assertEqual(self.client.get('/api/admin/documents', headers=self.auth).status_code, 503)

    def test_replacement_keeps_old_version_until_new_approval_and_preserves_history(self):
        identifier = self.upload('The observatory opens at midnight.')
        self.client.post(f'/api/admin/documents/{identifier}/approve', headers=self.auth)
        result = self.client.post(f'/api/admin/documents/{identifier}/replace', headers=self.auth,
            files={'file': ('new.txt', b'The observatory opens at sunset.', 'text/plain')})
        self.assertEqual(result.status_code, 200, result.text)
        replacement = result.json()['id']
        self.assertEqual(self.client.get(f'/api/documents/{identifier}/source').status_code, 200)
        self.assertEqual(self.client.get(f'/api/documents/{replacement}/source').status_code, 404)
        self.assertEqual(self.client.post(f'/api/admin/documents/{identifier}/replace', headers=self.auth,
            files={'file': ('extra.txt', b'Another observatory policy', 'text/plain')}).status_code, 409)
        self.assertEqual(self.client.post(f'/api/admin/documents/{replacement}/approve', headers=self.auth).status_code, 200)
        self.assertEqual(self.client.get(f'/api/documents/{identifier}/source').status_code, 404)
        self.assertEqual(self.client.get(f'/api/admin/documents/{identifier}/original', headers=self.auth).content, b'The observatory opens at midnight.')
        self.assertEqual(self.client.post(f'/api/admin/documents/{identifier}/approve', headers=self.auth).status_code, 409)
        history = self.client.get(f'/api/admin/documents/{replacement}/history', headers=self.auth).json()
        self.assertEqual([v['id'] for v in history['versions']], [identifier,replacement])
        self.assertEqual(self.client.get(f'/api/admin/documents/{replacement}/history').status_code, 401)

    def test_metadata_update_requires_review_and_revision_access_is_scoped(self):
        identifier = self.upload()
        self.client.post(f'/api/admin/documents/{identifier}/approve', headers=self.auth)
        self.assertEqual(self.client.patch(f'/api/admin/documents/{identifier}', headers=self.auth,
            json={'department':'Research', 'expires_on':'2027-01-01'}).status_code, 200)
        self.assertEqual(self.client.get(f'/api/documents/{identifier}/source').status_code, 404)
        self.assertEqual(self.client.patch(f'/api/admin/documents/{identifier}',headers=self.auth,json={'expires_on':'invalid'}).status_code, 422)
        self.client.put(f'/api/admin/documents/{identifier}/review',headers=self.auth,json={'pages':[{'page':1,'text':'The observatory opens at sunset.'}]})
        history = self.client.get(f'/api/admin/documents/{identifier}/history',headers=self.auth).json()
        revision = history['revisions'][0]['id']
        self.assertIn('midnight',self.client.get(f'/api/admin/documents/{identifier}/revisions/{revision}',headers=self.auth).json()['pages'][0]['text'])
        other = self.upload()
        self.assertEqual(self.client.get(f'/api/admin/documents/{other}/revisions/{revision}',headers=self.auth).status_code,404)

    def test_chat_rate_limit_returns_retry_header(self):
        from backend.operations import RequestLimits
        with patch.dict(os.environ, {'RATE_LIMIT_ENABLED':'1','CHAT_REQUESTS_PER_MINUTE':'1'}), patch.object(main.operations,'limits',RequestLimits()):
            first=self.client.post('/api/chat',json={'question':'library opening hours'})
            second=self.client.post('/api/chat',json={'question':'library opening hours'})
            self.assertEqual(first.status_code,200)
            self.assertEqual(second.status_code,429)
            self.assertGreater(int(second.headers['Retry-After']),0)
            self.assertTrue(second.headers['X-Request-ID'])

    def test_operations_status_and_retry_are_authenticated(self):
        self.assertEqual(self.client.get('/api/admin/system/status').status_code,401)
        self.assertEqual(self.client.post('/api/admin/system/check').status_code,401)
        identifier=self.upload()
        with main.connect() as db:
            db.execute("UPDATE vector_jobs SET error='embedding_access_denied',attempts=2 WHERE document_id=?",(identifier,))
        status=self.client.get('/api/admin/system/status',headers=self.auth).json()
        self.assertEqual(status['failed_jobs'][0]['document_id'],identifier)
        self.assertEqual(self.client.post(f'/api/admin/search/{identifier}/retry',headers=self.auth).status_code,202)
        self.assertEqual(self.client.get('/api/admin/system/status',headers=self.auth).json()['failed_jobs'],[])
        result=self.client.post('/api/admin/system/check',headers=self.auth)
        self.assertEqual(result.json()['providers']['generation']['error'],'not_configured')

    def test_backup_roundtrip_preserves_originals_and_refuses_nonempty_restore(self):
        from backend import backup
        path=Path(self.temp.name)/'snapshot.json.gz'
        counts=backup.create(main.connect,path)
        self.assertEqual(counts['documents'],3)
        self.assertEqual(len(backup.verify(path)['tables']['documents']),3)
        with self.assertRaises(ValueError): backup.restore(main.connect,path)
        with main.connect() as db:
            original=bytes(db.execute('SELECT original FROM documents ORDER BY id LIMIT 1').fetchone()[0])
            for table in reversed(backup.TABLES): db.execute('DELETE FROM '+table)
            db.execute('DELETE FROM vector_jobs')
        backup.restore(main.connect,path)
        with main.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM documents').fetchone()[0],3)
            self.assertEqual(bytes(db.execute('SELECT original FROM documents ORDER BY id LIMIT 1').fetchone()[0]),original)

    def test_database_outage_returns_safe_error_without_old_local_data(self):
        with patch.object(main, 'connect', side_effect=main.database.psycopg.OperationalError('private connection details')):
            result = self.client.get('/api/documents')
            self.assertEqual(result.status_code, 503)
            self.assertNotIn('private connection', result.text)

    def test_retrieval_evaluation_is_authenticated_and_does_not_save_answers(self):
        endpoint = '/api/admin/evaluation/retrieval'
        self.assertEqual(self.client.post(endpoint, json={'question': 'library opening hours'}).status_code, 401)
        with main.connect() as db:
            count = db.execute('SELECT count(*) FROM answers').fetchone()[0]
        with patch.object(main.generation, 'answer', new_callable=AsyncMock) as generate:
            response = self.client.post(endpoint, headers=self.auth, json={'question': 'library opening hours'})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()['sources'][0]['title'], 'Library handbook')
            self.assertEqual(response.json()['mode'], 'retrieval-only')
            generate.assert_not_called()
        with main.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM answers').fetchone()[0], count)

    def test_approval_archive_and_source_access(self):
        identifier = self.upload()
        self.assertEqual(self.client.get(f'/api/admin/documents/{identifier}/preview').status_code, 401)
        preview = self.client.get(f'/api/admin/documents/{identifier}/preview', headers=self.auth)
        self.assertEqual(preview.status_code, 200)
        self.assertIn('observatory', preview.json()[0]['text'])
        self.assertTrue(self.ask('observatory midnight')['fallback'])
        self.assertEqual(self.client.get(f'/api/documents/{identifier}/source').status_code, 404)
        self.assertEqual(self.client.post(f'/api/admin/documents/{identifier}/approve', headers=self.auth).status_code, 200)
        self.assertFalse(self.ask('observatory midnight')['fallback'])
        self.assertEqual(self.client.get(f'/api/documents/{identifier}/source').status_code, 200)
        self.assertEqual(self.client.post(f'/api/admin/documents/{identifier}/archive', headers=self.auth).status_code, 200)
        self.assertTrue(self.ask('observatory midnight')['fallback'])
        self.assertEqual(self.client.get(f'/api/documents/{identifier}/source').status_code, 404)

    def test_expired_sources_are_excluded(self):
        identifier = self.upload(expires='2000-01-01')
        self.client.post(f'/api/admin/documents/{identifier}/approve', headers=self.auth)
        self.assertTrue(self.ask('observatory midnight')['fallback'])
        self.assertNotIn(identifier, [doc['id'] for doc in self.client.get('/api/documents').json()])
        self.assertEqual(self.client.get(f'/api/documents/{identifier}/source').status_code, 404)

    def test_feedback_requires_an_answer(self):
        answer = self.ask('library membership')
        self.assertEqual(self.client.post('/api/feedback', json={'answer_id': answer['id'], 'helpful': True}).status_code, 200)
        self.assertEqual(self.client.post('/api/feedback', json={'answer_id': 'missing', 'helpful': False}).status_code, 404)

    def test_upload_validation(self):
        response = self.client.post('/api/admin/documents', headers=self.auth, data={'title': 'Invalid file'}, files={'file': ('script.html', b'<script/>', 'text/html')})
        self.assertEqual(response.status_code, 400)
        response = self.client.post('/api/admin/documents', headers=self.auth, data={'title': 'Empty source'}, files={'file': ('empty.txt', b'   ', 'text/plain')})
        self.assertEqual(response.status_code, 400)

    def test_fts_special_characters_are_safe(self):
        answer = self.ask('"library" OR * (membership) - NEAR')
        self.assertTrue(answer['sources'])
        self.assertEqual(self.client.post('/api/chat', json={'question': '  '}).status_code, 422)

    def test_docx_normalizes_source_without_guessing_pages(self):
        import io
        from docx import Document
        buffer = io.BytesIO()
        doc = Document()
        doc.add_paragraph('The observatory opens at midnight.')
        doc.save(buffer)
        response = self.client.post('/api/admin/documents', headers=self.auth,
            data={'title': 'Observatory guide'},
            files={'file': ('guide.docx', buffer.getvalue(), 'application/vnd.openxmlformats-officedocument.wordprocessingml.document')})
        self.assertEqual(response.status_code, 200)
        identifier = response.json()['id']
        self.client.post(f'/api/admin/documents/{identifier}/approve', headers=self.auth)
        answer = self.ask('observatory midnight')
        self.assertEqual(answer['sources'][0]['page'], 1)
        source = self.client.get(f'/api/documents/{identifier}/source')
        self.assertTrue(source.headers['content-type'].startswith('text/plain'))
        self.assertIn('observatory', source.text)

    def test_pdf_keeps_source_page_number(self):
        import io
        from pypdf import PdfWriter
        from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject
        writer = PdfWriter()
        writer.add_blank_page(width=600, height=800)
        page = writer.add_blank_page(width=600, height=800)
        font = DictionaryObject({NameObject('/Type'): NameObject('/Font'), NameObject('/Subtype'): NameObject('/Type1'), NameObject('/BaseFont'): NameObject('/Helvetica')})
        page[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'): DictionaryObject({NameObject('/F1'): writer._add_object(font)})})
        stream = DecodedStreamObject()
        stream.set_data(b'BT /F1 12 Tf 50 700 Td (Observatory midnight viewing.) Tj ET')
        page[NameObject('/Contents')] = writer._add_object(stream)
        buffer = io.BytesIO()
        writer.write(buffer)
        response = self.client.post('/api/admin/documents', headers=self.auth,
            data={'title': 'Observatory PDF'}, files={'file': ('guide.pdf', buffer.getvalue(), 'application/pdf')})
        self.assertEqual(response.status_code, 200, response.text)
        identifier = response.json()['id']
        self.client.post(f'/api/admin/documents/{identifier}/approve', headers=self.auth)
        answer = self.ask('observatory midnight')
        self.assertEqual(answer['sources'][0]['page'], 2)
        self.assertTrue(answer['sources'][0]['url'].endswith('#page=2'))

    def test_busy_extraction_returns_retryable_error_without_partial_upload(self):
        with main.connect() as db:
            before=db.execute('SELECT count(*) FROM documents').fetchone()[0]
        with patch.object(main,'extract_document',side_effect=main.ExtractionBusy('Another document is being processed.')):
            response=self.client.post('/api/admin/documents',headers=self.auth,data={'title':'Queued document'},files={'file':('policy.txt',b'Library rules','text/plain')})
        self.assertEqual(response.status_code,429)
        self.assertEqual(response.headers.get('Retry-After'),'5')
        with main.connect() as db:self.assertEqual(db.execute('SELECT count(*) FROM documents').fetchone()[0],before)

    def test_gemini_valid_and_invalid_citation_responses(self):
        import json
        for citation, expected_mode in [('[1]', 'gemini'), ('[99]', 'extractive-fallback')]:
            with self.subTest(citation=citation), patch.dict(os.environ, {'GEMINI_API_KEY': 'test-only-key'}), patch.object(main.httpx, 'AsyncClient') as client:
                response = httpx.Response(200, request=httpx.Request('POST', 'https://example.invalid'),
                    json={'status': 'completed', 'steps': [{'type': 'thought', 'signature': 'not answer text'},
                        {'type': 'model_output', 'content': [{'type': 'text', 'text': json.dumps({'answer': 'Demonstration: the library opens at 8 AM. ' + citation, 'supported': True})}]}]})
                client.return_value.__aenter__.return_value.post = AsyncMock(return_value=response)
                self.assertEqual(self.ask('library opening hours')['mode'], expected_mode)

    def test_gemini_incomplete_or_malformed_output_preserves_excerpts(self):
        import json
        for status, content in [('incomplete', {'answer': 'Open [1]', 'supported': True}),
                                ('completed', {'answer': 'Open', 'supported': True}),
                                ('completed', {'answer': '', 'supported': False}),
                                ('completed', {'answer': 123, 'supported': True}),
                                ('completed', {'answer': 'Open [1]', 'supported': 'true'})]:
            with self.subTest(status=status, content=content), patch.dict(os.environ, {'GEMINI_API_KEY': 'test-only-key'}), patch.object(main.httpx, 'AsyncClient') as client:
                response = httpx.Response(200, request=httpx.Request('POST', 'https://example.invalid'),
                    json={'status': status, 'steps': [{'type': 'model_output', 'content': [{'type': 'text', 'text': json.dumps(content)}]}]})
                client.return_value.__aenter__.return_value.post = AsyncMock(return_value=response)
                answer = self.ask('library opening hours')
                self.assertEqual(answer['mode'], 'extractive-fallback')
                self.assertIn('8:00 AM', answer['answer'])

    def test_gemini_uses_stateless_structured_request_and_joins_text_blocks(self):
        with patch.dict(os.environ, {'GEMINI_API_KEY': 'test-only-key', 'GEMINI_MODEL': 'gemini-3.8-flash'}), patch.object(main.httpx, 'AsyncClient') as client:
            response = httpx.Response(200, request=httpx.Request('POST', 'https://example.invalid'),
                json={'status': 'completed', 'steps': [{'type': 'thought', 'signature': 'ignored'},
                    {'type': 'model_output', 'content': [{'type': 'text', 'text': '{"answer":"Demonstration: open [1]",'},
                                                        {'type': 'text', 'text': '"supported":true}'}]}]})
            post = AsyncMock(return_value=response)
            client.return_value.__aenter__.return_value.post = post
            answer = self.ask('library opening hours')
            self.assertEqual(answer['mode'], 'gemini')
            self.assertFalse(answer['fallback'])
            args, kwargs = post.call_args
            self.assertEqual(args[0], 'https://generativelanguage.googleapis.com/v1beta/interactions')
            self.assertFalse(kwargs['json']['store'])
            self.assertEqual(kwargs['json']['response_format']['mime_type'], 'application/json')
            self.assertIn('untrusted DATA', kwargs['json']['system_instruction'])
            self.assertIn('sources', kwargs['json']['input'])

    def test_gemini_failure_preserves_source_excerpts(self):
        with patch.dict(os.environ, {'GEMINI_API_KEY': 'test-only-key'}), patch.object(main.httpx, 'AsyncClient') as client:
            client.return_value.__aenter__.return_value.post = AsyncMock(side_effect=httpx.ConnectError('test failure'))
            answer = self.ask('library opening hours')
            self.assertEqual(answer['mode'], 'extractive-fallback')
            self.assertIn('8:00 AM', answer['answer'])


    def test_semantic_search_finds_sources_without_matching_keywords(self):
        from backend import semantic
        with main.connect() as db:
            library = dict(db.execute("SELECT d.*,c.page,c.text FROM documents d JOIN chunks c ON c.document_id=d.id WHERE d.title='Library handbook'").fetchone())
        with patch.dict(os.environ, {'DATABASE_URL': 'test-only', 'GEMINI_API_KEY': ''}), patch.object(semantic, 'configured', return_value=True), patch.object(main.vector_index, 'search', return_value=[library]) as search:
            result = self.ask('May I take textbooks home?')
            self.assertEqual(result['retrieval'], 'hybrid-pgvector')
            self.assertEqual(result['sources'][0]['title'], 'Library handbook')
            search.assert_called_once()

    def test_semantic_failure_keeps_keyword_search(self):
        with patch.object(main.semantic, 'configured', return_value=True), patch.object(main.vector_index, 'search', side_effect=ConnectionError('unavailable')):
            result = self.ask('library opening hours')
            self.assertEqual(result['retrieval'], 'sqlite-fts5-fallback')
            self.assertIn('8:00 AM', result['answer'])

    def test_vector_candidates_cannot_reveal_unapproved_expired_or_stale_text(self):
        from backend import semantic
        identifier = self.upload('A telescope is available after dark.')
        with main.connect() as db:
            chunks = [dict(row) for row in db.execute('SELECT page,text FROM chunks WHERE document_id=? ORDER BY id', (identifier,))]
        candidate = {'document_id': identifier, 'revision': semantic.revision(chunks),
                     'chunk_key': semantic.chunk_key(identifier, 1, chunks[0]['text']), 'similarity': .9}
        with patch.object(semantic, 'embed', return_value=[1.0] * 768), patch.object(semantic, 'search', return_value=[candidate]):
            self.assertEqual(main.vector_index.search('stargazing'), [])
            self.client.post(f'/api/admin/documents/{identifier}/approve', headers=self.auth)
            self.assertEqual(main.vector_index.search('stargazing')[0]['id'], identifier)
            with main.connect() as db:
                db.execute("UPDATE documents SET expires_on='2000-01-01' WHERE id=?", (identifier,))
            self.assertEqual(main.vector_index.search('stargazing'), [])
            with main.connect() as db:
                db.execute('UPDATE documents SET expires_on=NULL WHERE id=?', (identifier,))
            self.client.post(f'/api/admin/documents/{identifier}/archive', headers=self.auth)
            self.assertEqual(main.vector_index.search('stargazing'), [])
            self.client.put(f'/api/admin/documents/{identifier}/review', headers=self.auth,
                json={'pages': [{'page': 1, 'text': 'The telescope is now unavailable.'}]})
            self.client.post(f'/api/admin/documents/{identifier}/approve', headers=self.auth)
            self.assertEqual(main.vector_index.search('stargazing'), [])

    def test_index_queue_caches_embeddings_and_cleans_archives(self):
        from backend import semantic
        identifier = self.upload('A telescope is available after dark.')
        self.client.post(f'/api/admin/documents/{identifier}/approve', headers=self.auth)
        with main.connect() as db:
            db.execute('DELETE FROM vector_jobs WHERE document_id!=?', (identifier,))
        with patch.object(semantic, 'embed', return_value=[1.0] + [0.0] * 767) as embed, patch.object(semantic, 'replace_document') as replace:
            self.assertTrue(main.vector_index.process_one())
            self.assertEqual(len(replace.call_args.args[2]), 1)
            self.assertEqual(main.vector_index.status()['pending_documents'], 0)
            self.client.post(f'/api/admin/documents/{identifier}/approve', headers=self.auth)
            self.assertTrue(main.vector_index.process_one())
            self.assertEqual(embed.call_count, 1)
            self.client.post(f'/api/admin/documents/{identifier}/archive', headers=self.auth)
            self.assertTrue(main.vector_index.process_one())
            self.assertEqual(replace.call_args.args[2], [])

    def test_index_failure_retries_and_concurrent_changes_keep_the_job(self):
        from backend import semantic
        identifier = self.upload('A telescope is available after dark.')
        with main.connect() as db:
            db.execute('DELETE FROM vector_jobs WHERE document_id!=?', (identifier,))
        with patch.object(semantic, 'replace_document', side_effect=ConnectionError('secret URI should never be stored')):
            self.assertFalse(main.vector_index.process_one())
        with main.connect() as db:
            job = db.execute('SELECT * FROM vector_jobs WHERE document_id=?', (identifier,)).fetchone()
            self.assertEqual(job['error'], 'ConnectionError')
            self.assertEqual(job['attempts'], 1)
            db.execute('UPDATE vector_jobs SET next_attempt=0 WHERE document_id=?', (identifier,))
        def change_during_index(*args):
            with main.connect() as db:
                db.execute("UPDATE documents SET status='archived' WHERE id=?", (identifier,))
        with patch.object(semantic, 'replace_document', side_effect=change_during_index):
            self.assertTrue(main.vector_index.process_one())
        self.assertEqual(main.vector_index.status()['pending_documents'], 1)

    def test_vector_management_endpoints_require_admin(self):
        self.assertEqual(self.client.get('/api/admin/search/status').status_code, 401)
        self.assertEqual(self.client.post('/api/admin/search/reindex').status_code, 401)
        result = self.client.post('/api/admin/search/reindex', headers=self.auth)
        self.assertEqual(result.status_code, 202)
        self.assertEqual(result.json()['dimensions'], 768)
        self.assertEqual(result.json()['pending_documents'], 3)

    def test_review_shows_a_full_page_once_not_overlapping_chunks(self):
        text = '\n'.join(f'Unique line {i}: campus observatory viewing procedures.' for i in range(100))
        identifier = self.upload(text)
        preview = self.client.get(f'/api/admin/documents/{identifier}/preview', headers=self.auth).json()
        self.assertEqual(len(preview), 1)
        self.assertEqual(preview[0]['text'], text)
        with main.connect() as db:
            self.assertGreater(db.execute('SELECT count(*) FROM chunks WHERE document_id=?', (identifier,)).fetchone()[0], 1)

    def test_saved_corrections_replace_index_and_require_reapproval(self):
        identifier = self.upload()
        with main.connect() as db:
            original = bytes(db.execute('SELECT original FROM documents WHERE id=?', (identifier,)).fetchone()[0])
        self.client.post(f'/api/admin/documents/{identifier}/approve', headers=self.auth)
        response = self.client.put(f'/api/admin/documents/{identifier}/review', headers=self.auth,
            json={'pages': [{'page': 1, 'text': 'The telescope closes at sunset.'}]})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['status'], 'pending')
        self.assertTrue(self.ask('telescope sunset')['fallback'])
        self.client.post(f'/api/admin/documents/{identifier}/approve', headers=self.auth)
        self.assertFalse(self.ask('telescope sunset')['fallback'])
        self.assertTrue(self.ask('observatory midnight')['fallback'])
        with main.connect() as db:
            self.assertEqual(bytes(db.execute('SELECT original FROM documents WHERE id=?', (identifier,)).fetchone()[0]), original)
            self.assertEqual(db.execute('SELECT count(*) FROM document_revisions WHERE document_id=?', (identifier,)).fetchone()[0], 1)

    def test_ocr_needs_review_before_approval(self):
        from backend.extraction import ExtractedPage
        with patch.object(main, 'extract_document', return_value=([ExtractedPage(1, 'Fee: 2200', 'ocr', ['Compare the amount.'])], b'original', 'text/plain')):
            identifier = self.upload()
        result = self.client.post(f'/api/admin/documents/{identifier}/approve', headers=self.auth)
        self.assertEqual(result.status_code, 409)
        result = self.client.put(f'/api/admin/documents/{identifier}/review', headers=self.auth,
            json={'pages': [{'page': 1, 'text': 'Fee: 2200'}]})
        self.assertEqual(result.status_code, 200)
        self.assertEqual(self.client.post(f'/api/admin/documents/{identifier}/approve', headers=self.auth).status_code, 200)

    def test_review_rejects_missing_duplicate_and_unknown_pages(self):
        identifier = self.upload()
        for pages in [[], [{'page': 2, 'text': 'Unknown page'}], [{'page': 1, 'text': 'one'}, {'page': 1, 'text': 'duplicate'}]]:
            result = self.client.put(f'/api/admin/documents/{identifier}/review', headers=self.auth, json={'pages': pages})
            self.assertEqual(result.status_code, 422)
        self.assertEqual(self.client.put(f'/api/admin/documents/{identifier}/review', json={'pages': [{'page': 1, 'text': 'test'}]}).status_code, 401)

    def test_reprocess_does_not_unarchive_document(self):
        from backend.extraction import ExtractedPage
        identifier = self.upload()
        self.client.post(f'/api/admin/documents/{identifier}/archive', headers=self.auth)
        with patch.object(main, 'extract_document', return_value=([ExtractedPage(1, 'Updated telescope procedure', 'ocr')], b'original', 'text/plain')):
            response = self.client.post(f'/api/admin/documents/{identifier}/reprocess', headers=self.auth, json={'force_ocr': True})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(self.ask('telescope procedure')['fallback'])
        self.client.put(f'/api/admin/documents/{identifier}/review', headers=self.auth, json={'pages': [{'page': 1, 'text': 'Updated telescope procedure'}]})
        documents = self.client.get('/api/admin/documents', headers=self.auth).json()
        self.assertEqual(next(doc for doc in documents if doc['id'] == identifier)['status'], 'archived')

    def test_legacy_migration_restores_full_text_without_chunk_duplication(self):
        text = 'A telescope manual.\n' * 150
        identifier = self.upload(text)
        with main.connect() as db:
            db.execute('DELETE FROM document_pages WHERE document_id=?', (identifier,))
        main.initialize()
        preview = self.client.get(f'/api/admin/documents/{identifier}/preview', headers=self.auth).json()
        self.assertEqual(len(preview), 1)
        self.assertEqual(preview[0]['text'], text)

    def test_page_images_require_authentication(self):
        identifier = self.upload()
        self.assertEqual(self.client.get(f'/api/admin/documents/{identifier}/pages/1/image').status_code, 401)
        self.assertEqual(self.client.get(f'/api/admin/documents/{identifier}/pages/1/image', headers=self.auth).status_code, 404)


if __name__ == '__main__':
    unittest.main()
