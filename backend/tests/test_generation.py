import json
import os
import unittest
from unittest.mock import AsyncMock, patch

import httpx

from backend import generation


def response(status, payload):
    return httpx.Response(status, request=httpx.Request('POST', 'https://example.invalid'), json=payload)


def completed(answer='The desk opens at 9 AM. [1]'):
    return {'status': 'completed', 'steps': [{'type': 'model_output', 'content': [
        {'type': 'text', 'text': json.dumps({'answer': answer, 'supported': True})}]}]}


class GenerationTests(unittest.IsolatedAsyncioTestCase):
    def test_grouped_citations_normalize_without_allowing_unknown_ids(self):
        value=generation.parse_response(completed('Open from 9 AM. [1, 2]'),[{'id':1},{'id':2}])
        self.assertIn('[1] [2]',value['answer'])
        with self.assertRaises(ValueError):
            generation.parse_response(completed('Open from 9 AM. [1, 99]'),[{'id':1},{'id':2}])

    def test_sample_citation_label_moves_outside_validated_identifier(self):
        value=generation.parse_response(completed('Open at 9 AM. [1 (DEMONSTRATION)]'),[{'id':1}])
        self.assertIn('[1] (demonstration)',value['answer'])
        for citation in ['[99 (SAMPLE)]','[1-99]','[1 arbitrary note]']:
            with self.subTest(citation=citation),self.assertRaises(ValueError):
                generation.parse_response(completed('Open at 9 AM. '+citation),[{'id':1}])
    async def test_depleted_credits_stop_failover_and_report_billing(self):
        with patch.dict(os.environ, {'GEMINI_API_KEY': 'fake', 'GEMINI_MODEL': 'primary', 'GEMINI_FALLBACK_MODELS': 'alternate'}), patch.object(generation.httpx, 'AsyncClient') as client:
            post = AsyncMock(return_value=response(402, [{'error': {'message': 'credits depleted'}}]))
            client.return_value.__aenter__.return_value.post = post
            with self.assertRaises(generation.GenerationUnavailable) as error:
                await generation.answer('When?', [{'id': 1}])
            self.assertEqual(error.exception.reason, 'provider_billing_required')
            self.assertEqual(post.call_count, 1)

    async def test_busy_model_fails_over_and_reports_used_model(self):
        with patch.dict(os.environ, {'GEMINI_API_KEY': 'fake', 'GEMINI_MODEL': 'primary', 'GEMINI_FALLBACK_MODELS': 'alternate'}), patch.object(generation.httpx, 'AsyncClient') as client, patch.object(generation.asyncio, 'sleep', new_callable=AsyncMock):
            post = AsyncMock(side_effect=[response(503, {}), response(200, completed())])
            client.return_value.__aenter__.return_value.post = post
            result = await generation.answer('When?', [{'id': 1}])
            self.assertEqual(result['model'], 'alternate')
            self.assertEqual([call.kwargs['json']['model'] for call in post.call_args_list], ['primary', 'alternate'])

    async def test_timeout_fails_over_but_invalid_citations_never_do(self):
        with patch.dict(os.environ, {'GEMINI_API_KEY': 'fake', 'GEMINI_MODEL': 'primary', 'GEMINI_FALLBACK_MODELS': 'alternate'}), patch.object(generation.httpx, 'AsyncClient') as client, patch.object(generation.asyncio, 'sleep', new_callable=AsyncMock):
            post = AsyncMock(side_effect=[httpx.ReadTimeout('test'), response(200, completed())])
            client.return_value.__aenter__.return_value.post = post
            self.assertEqual((await generation.answer('When?', [{'id': 1}]))['model'], 'alternate')
            post.reset_mock(side_effect=True)
            post.return_value = response(200, completed('Invented source [99]'))
            with self.assertRaises(generation.GenerationUnavailable) as error:
                await generation.answer('When?', [{'id': 1}])
            self.assertEqual(error.exception.reason, 'invalid_answer_or_citations')
            self.assertEqual(post.call_count, 1)

    async def test_access_denied_does_not_retry_or_expose_provider_details(self):
        with patch.dict(os.environ, {'GEMINI_API_KEY': 'fake', 'GEMINI_MODEL': 'primary', 'GEMINI_FALLBACK_MODELS': 'alternate'}), patch.object(generation.httpx, 'AsyncClient') as client:
            post = AsyncMock(return_value=response(403, {'error': {'message': 'private details'}}))
            client.return_value.__aenter__.return_value.post = post
            with self.assertRaises(generation.GenerationUnavailable) as error:
                await generation.answer('When?', [{'id': 1}])
            self.assertEqual(error.exception.reason, 'provider_access_denied')
            self.assertNotIn('private', str(error.exception))
            self.assertEqual(post.call_count, 1)
