import copy
import hashlib
import unittest

from backend.evaluation import score, summarize, validate_references


class EvaluationTests(unittest.TestCase):
    def setUp(self):
        self.text = 'Regular examination fee: INR 2,200. Non-refundable.'
        self.snapshot = {'documents': [{'id': 'fee', 'title': 'Fee notice', 'status': 'approved', 'sample': 0, 'expires_on': None}],
                         'pages': [{'document_id': 'fee', 'page': 1, 'text': self.text}]}
        self.case = {'expect': 'answer', 'document_id': 'fee', 'pages': [1], 'facts': ['2,?200'], 'evidence': ['2,?200']}
        self.response = {'answer': 'The fee is INR 2,200. [1]', 'mode': 'gemini', 'fallback': False,
                         'retrieval': 'hybrid-pgvector',
                         'sources': [{'id': 1, 'document_id': 'fee', 'page': 1, 'excerpt': self.text}]}

    def test_wrong_amount_fails_even_with_valid_citation(self):
        self.assertTrue(score(self.case, self.response, self.snapshot)['passed'])
        self.response['answer'] = 'The fee is INR 12,000. [1]'
        result = score(self.case, self.response, self.snapshot)
        self.assertTrue(result['checks']['citation_identifiers'])
        self.assertFalse(result['checks']['expected_facts'])
        self.assertFalse(result['passed'])

    def test_correct_fact_citing_the_wrong_passage_fails_evidence_check(self):
        self.response['sources'].append({**self.response['sources'][0], 'id': 2})
        self.response['sources'][0]['excerpt'] = 'Non-refundable.'
        result = score(self.case, self.response, self.snapshot)
        self.assertTrue(result['checks']['expected_facts'])
        self.assertTrue(result['checks']['relevant_citation'])
        self.assertFalse(result['checks']['cited_evidence'])

    def test_archived_expired_wrong_page_and_changed_excerpt_fail_access(self):
        for change in ['archived', 'expired', 'page', 'excerpt']:
            snapshot, response = copy.deepcopy(self.snapshot), copy.deepcopy(self.response)
            if change == 'archived':
                snapshot['documents'][0]['status'] = 'archived'
            elif change == 'expired':
                snapshot['documents'][0]['expires_on'] = '2000-01-01'
            elif change == 'page':
                response['sources'][0]['page'] = 99
            else:
                response['sources'][0]['excerpt'] = 'Made-up source text'
            with self.subTest(change=change):
                self.assertFalse(score(self.case, response, snapshot)['checks']['source_access'])

    def test_fake_citation_and_unlabelled_sample_fail(self):
        self.response['answer'] = 'INR 2,200. [99]'
        self.assertFalse(score(self.case, self.response, self.snapshot)['checks']['citation_identifiers'])
        self.response['answer'] = 'INR 2,200. [1]'
        self.case['sample'] = True
        self.assertFalse(score(self.case, self.response, self.snapshot)['checks']['sample_label'])

    def test_excerpts_or_provider_failure_do_not_pass_as_abstention(self):
        case = {'expect': 'abstain'}
        self.assertFalse(score(case, self.response, self.snapshot)['passed'])
        self.response.update({'answer': 'This is not provided in approved sources.', 'fallback': True, 'generation_error': 'provider_busy'})
        self.assertFalse(score(case, self.response, self.snapshot)['passed'])
        self.response['generation_error'] = None
        self.assertTrue(score(case, self.response, self.snapshot)['passed'])

    def test_source_changes_invalidate_the_gold_reference(self):
        suite = {'references': [{'document_id': 'fee', 'title': 'Fee notice', 'status': 'approved',
                                'page_sha256': {'1': hashlib.sha256(self.text.encode()).hexdigest()}}]}
        validate_references(suite, self.snapshot)
        self.snapshot['pages'][0]['text'] = 'A newer policy'
        with self.assertRaises(ValueError):
            validate_references(suite, self.snapshot)

    def test_request_errors_count_against_overall_score(self):
        result = score(self.case, self.response, self.snapshot)
        summary = summarize([{'expect': 'answer', 'evaluation': result, 'seconds': 2},
                             {'expect': 'answer', 'request_error': 'ReadTimeout', 'seconds': 70}])
        self.assertEqual(summary['cases'], 2)
        self.assertEqual(summary['passed'], 1)
        self.assertEqual(summary['request_errors'], 1)

    def test_retrieval_only_assesses_evidence_without_claiming_generation_quality(self):
        self.response.update({'mode': 'retrieval-only', 'answer': ''})
        result = score(self.case, self.response, self.snapshot)
        self.assertTrue(result['passed'])
        self.assertTrue(result['checks']['retrieved_evidence'])
        self.assertIsNone(result['checks']['expected_facts'])
        self.assertIsNone(result['checks']['generated_answer'])

    def test_billing_failure_is_blocked_not_a_fact_accuracy_measurement(self):
        self.response.update({'mode': 'extractive-fallback', 'generation_error': 'provider_billing_required'})
        result = score(self.case, self.response, self.snapshot)
        self.assertEqual(result['status'], 'blocked')
        self.assertIsNone(result['checks']['expected_facts'])
        self.assertFalse(result['passed'])

    def test_keyword_fallback_does_not_pass_as_semantic_retrieval(self):
        self.response.update({'retrieval': 'postgres-fts-fallback', 'retrieval_error': 'embedding_billing_required'})
        result = score(self.case, self.response, self.snapshot)
        self.assertTrue(result['checks']['retrieved_evidence'])
        self.assertFalse(result['checks']['hybrid_retrieval'])
        self.assertEqual(result['status'], 'blocked')

    def test_plural_source_abstention_and_equivalent_range_are_recognized(self):
        from backend.evaluation import matches, ABSTENTION
        self.assertTrue(matches(ABSTENTION,'The sources do not contain that information.'))
        self.assertTrue(matches(ABSTENTION,'The sources do not specify a fee.'))
        import json
        from pathlib import Path
        suite=json.loads((Path(__file__).resolve().parents[2]/'evaluation/campus_questions.json').read_text(encoding='utf-8'))
        pattern=next(case for case in suite['cases'] if case['id']=='honors-students')['facts'][0]
        self.assertTrue(matches(pattern,'Select 2 to 5 students.'))
        self.assertFalse(matches(pattern,'Select 2 to 6 students.'))
