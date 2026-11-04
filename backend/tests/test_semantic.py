import json
import math
import os
import unittest
import uuid
from unittest.mock import patch
from unittest.mock import AsyncMock
import asyncio

import httpx

from backend import semantic


class EmbeddingTests(unittest.TestCase):
    def test_billing_diagnostic_does_not_expose_provider_message(self):
        response = httpx.Response(402, request=httpx.Request('POST', 'https://example.invalid'), json={'error': 'private'})
        error = httpx.HTTPStatusError('private', request=response.request, response=response)
        self.assertEqual(semantic.failure_reason(error), 'embedding_billing_required')
    def test_embeddings_use_distinct_document_and_query_tasks(self):
        vector = [2.0] + [0.0] * 767
        response = httpx.Response(200, request=httpx.Request('POST', 'https://example.invalid'),
                                  json={'embedding': {'values': vector}})
        with patch.dict(os.environ, {'GEMINI_API_KEY': 'fake', 'EMBEDDING_MODEL': 'gemini-embedding-001'}), patch.object(semantic.httpx, 'post', return_value=response) as post:
            result = semantic.embed('borrowing', document=True, title='Library')
            self.assertEqual(post.call_args.kwargs['json']['taskType'], 'RETRIEVAL_DOCUMENT')
            self.assertEqual(post.call_args.kwargs['json']['title'], 'Library')
            self.assertEqual(post.call_args.kwargs['json']['outputDimensionality'], 768)
            self.assertAlmostEqual(sum(v*v for v in result), 1)
            semantic.embed('textbooks')
            self.assertEqual(post.call_args.kwargs['json']['taskType'], 'RETRIEVAL_QUERY')
            self.assertNotIn('title', post.call_args.kwargs['json'])

    def test_invalid_dimensions_nonfinite_and_zero_vectors_are_rejected(self):
        for vector in [[1.0], [0.0] * 768, [math.nan] * 768, [math.inf] * 768, [True] * 768]:
            with self.subTest(vector=vector[:1]), self.assertRaises(ValueError):
                semantic.normalize(vector)

    def test_rank_fusion_rewards_agreement_and_deduplicates(self):
        first = {'id': 'a', 'page': 1, 'text': 'Library'}
        second = {'id': 'b', 'page': 2, 'text': 'Fees'}
        result = semantic.fuse([first, second], [second, second])
        self.assertEqual(result, [second, first])


class IndexWorkerTests(unittest.IsolatedAsyncioTestCase):
    async def test_worker_retries_after_lost_database_connection(self):
        from backend.indexing import VectorIndexer
        worker = VectorIndexer(None)
        with patch.object(semantic, 'configured', return_value=True), patch.object(worker, 'process_one', side_effect=[ConnectionError('offline'), False]) as process, patch('backend.indexing.asyncio.sleep', new_callable=AsyncMock, side_effect=[None, asyncio.CancelledError()]):
            with self.assertRaises(asyncio.CancelledError):
                await worker.run()
            self.assertEqual(process.call_count, 2)


@unittest.skipUnless(os.getenv('TEST_DATABASE_URL'), 'Set TEST_DATABASE_URL to run real pgvector integration tests')
class PgvectorIntegrationTests(unittest.TestCase):
    def test_migration_atomic_replacement_and_model_filtered_cosine_search(self):
        identifier = str(uuid.uuid4())
        vector = [1.0] + [0.0] * 767
        selected = os.getenv('EMBEDDING_MODEL', semantic.DEFAULT_MODEL)
        with patch.dict(os.environ, {'DATABASE_URL': os.environ['TEST_DATABASE_URL'], 'EMBEDDING_MODEL': selected}):
            semantic.migrate()
            try:
                semantic.replace_document(identifier, 'revision1', [{'key': identifier, 'page': 1, 'text': 'integration fixture', 'embedding': vector}])
                matches = semantic.search(vector, 100)
                self.assertTrue(any(row['document_id'] == identifier and row['similarity'] > .999 for row in matches))
                with self.assertRaises(Exception):
                    semantic.replace_document(identifier, 'revision2', [{'key': identifier, 'page': 1, 'text': 'invalid vector', 'embedding': [1]}])
                with semantic.connection() as db:
                    row = db.execute('SELECT revision FROM campus_semantic_chunks WHERE document_id=%s', (identifier,)).fetchone()
                    self.assertEqual(row['revision'], 'revision1')
                with patch.dict(os.environ, {'EMBEDDING_MODEL': 'different-model'}):
                    self.assertFalse(any(row['document_id'] == identifier for row in semantic.search(vector, 100)))
                semantic.replace_document(identifier, 'revision3', [])
                with semantic.connection() as db:
                    self.assertEqual(db.execute('SELECT count(*) AS n FROM campus_semantic_chunks WHERE document_id=%s', (identifier,)).fetchone()['n'], 0)
            finally:
                semantic.replace_document(identifier, '', [])
