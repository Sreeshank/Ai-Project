"""Source-pinned RAG checks. Run: python -m backend.evaluation --label baseline."""
import argparse
import getpass
import asyncio
import hashlib
import json
import math
import os
import re
import statistics
from collections import Counter
import time
from datetime import date, datetime, timezone
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SUITE = ROOT / 'evaluation/campus_questions.json'
ABSTENTION = r"could not|cannot|can't|not (?:provided|available|specified|mentioned|enough|found|stated|published)|do not have|don't have|(?:do|does) not (?:contain|specify|state|provide|mention|include)|insufficient|no (?:information|details|approved)|notified.*(?:later|due course)|will be notified"


def normalized(text):
    return ' '.join(text.split())


def matches(pattern, text):
    return bool(re.search(pattern, normalized(text), re.IGNORECASE))


def load_snapshot():
    from backend import main
    with main.connect() as db:
        documents = [dict(row) for row in db.execute('SELECT id,title,status,sample,expires_on FROM documents')]
        pages = [dict(row) for row in db.execute('SELECT document_id,page,text FROM document_pages')]
    return {'documents': documents, 'pages': pages}


def score(case, response, snapshot):
    """Independent checks deliberately distinguish facts from citation syntax."""
    answer, sources = response.get('answer', ''), response.get('sources', [])
    documents = {doc['id']: doc for doc in snapshot['documents']}
    pages = {(page['document_id'], page['page']): page['text'] for page in snapshot['pages']}
    ids = [source.get('id') for source in sources]
    citations = [int(value) for value in re.findall(r'\[(\d+)\]', answer)]
    retrieval_only = response.get('mode') == 'retrieval-only'
    generated = response.get('mode') == 'gemini'
    checks = {}
    checks['hybrid_retrieval'] = response.get('retrieval') == 'hybrid-pgvector'
    checks['source_access'] = all(
        source.get('document_id') in documents
        and documents[source['document_id']]['status'] == 'approved'
        and (not documents[source['document_id']]['expires_on'] or documents[source['document_id']]['expires_on'] >= date.today().isoformat())
        and (source['document_id'], source.get('page')) in pages
        and bool(source.get('excerpt'))
        and normalized(source['excerpt']) in normalized(pages[(source['document_id'], source['page'])])
        for source in sources)
    checks['citation_identifiers'] = None if retrieval_only else len(set(ids)) == len(ids) and all(value in ids for value in citations)
    checks['no_forbidden_claims'] = None if not generated else not any(matches(pattern, answer) for pattern in case.get('forbidden', []))
    rank = None
    if case['expect'] == 'answer':
        relevant = [source for source in sources if source.get('document_id') == case['document_id'] and source.get('page') in case['pages']]
        rank = next((index for index, source in enumerate(sources, 1) if source in relevant), None)
        cited = [source for source in relevant if source['id'] in citations]
        evidence = '\n'.join(source['excerpt'] for source in cited)
        checks['retrieval_hit'] = bool(relevant)
        checks['retrieved_evidence'] = all(matches(pattern, '\n'.join(source['excerpt'] for source in relevant)) for pattern in case['evidence'])
        checks['generated_answer'] = None if retrieval_only else generated and response.get('fallback') is False
        checks['relevant_citation'] = bool(cited) if generated else None
        checks['expected_facts'] = all(matches(pattern, answer) for pattern in case['facts']) if generated else None
        checks['cited_evidence'] = all(matches(pattern, evidence) for pattern in case['evidence']) if generated else None
        if case.get('sample'):
            checks['sample_label'] = matches(r'sample|demo(?:nstration)?|illustrative|not official', answer) if generated else None
    else:
        checks['abstention'] = None if response.get('generation_error') else response.get('fallback') is True and matches(ABSTENTION, answer)
        checks['no_provider_failure'] = not response.get('generation_error')
    passed = all(value for value in checks.values() if value is not None)
    blocked = bool(response.get('generation_error', '').startswith('provider_')) if response.get('generation_error') else False
    blocked = blocked or bool(response.get('retrieval_error'))
    return {'passed': passed, 'status': 'blocked' if blocked else ('pass' if passed else 'fail'),
            'checks': checks, 'reciprocal_rank': 1/rank if rank else 0,
            'failures': [name for name, value in checks.items() if value is False],
            'not_evaluated': [name for name, value in checks.items() if value is None]}


def validate_references(suite, snapshot):
    pages = {(page['document_id'], page['page']): page['text'] for page in snapshot['pages']}
    documents = {document['id']: document for document in snapshot['documents']}
    for reference in suite['references']:
        identifier = reference['document_id']
        if identifier not in documents or documents[identifier]['status'] != reference['status']:
            raise ValueError('Reference status changed: ' + reference['title'])
        for page, digest in reference['page_sha256'].items():
            content = pages.get((identifier, int(page)))
            if content is None or hashlib.sha256(content.encode()).hexdigest() != digest:
                raise ValueError('Reference text changed; review the gold answers: ' + reference['title'])


def summarize(results):
    successful = [row for row in results if not row.get('request_error')]
    supported = [row for row in successful if row['expect'] == 'answer']
    unknown = [row for row in successful if row['expect'] == 'abstain']
    def rate(rows, key):
        assessed = [row for row in rows if row['evaluation']['checks'].get(key) is not None]
        return {'passed': sum(row['evaluation']['checks'][key] for row in assessed), 'total': len(assessed), 'not_evaluated': len(rows)-len(assessed)}
    latencies = sorted(row['seconds'] for row in successful)
    return {'cases': len(results), 'passed': sum(row.get('evaluation', {}).get('passed', False) for row in results),
            'retrieval_modes': dict(Counter(row.get('response', {}).get('retrieval', 'unknown') for row in successful)),
            'request_errors': len(results)-len(successful), 'retrieval_hit_at_4': rate(supported, 'retrieval_hit'),
            'blocked_cases': sum(row['evaluation'].get('status') == 'blocked' for row in successful),
            'hybrid_retrieval': rate(successful, 'hybrid_retrieval'),
            'retrieved_evidence': rate(supported, 'retrieved_evidence'),
            'expected_facts': rate(supported, 'expected_facts'), 'relevant_citations': rate(supported, 'relevant_citation'),
            'cited_evidence': rate(supported, 'cited_evidence'), 'generated_answers': rate(supported, 'generated_answer'),
            'abstention': rate(unknown, 'abstention'), 'source_access': rate(successful, 'source_access'),
            'valid_citation_identifiers': rate(successful, 'citation_identifiers'),
            'mean_reciprocal_rank': round(sum(row['evaluation']['reciprocal_rank'] for row in supported)/len(supported), 4) if supported else None,
            'latency_seconds': {'median': round(statistics.median(latencies), 2) if latencies else None,
                                'p95': round(latencies[min(len(latencies)-1, math.ceil(.95*len(latencies))-1)], 2) if latencies else None}}


def write_report(report, prefix):
    prefix.parent.mkdir(parents=True, exist_ok=True)
    report['summary'] = summarize(report['results'])
    prefix.with_suffix('.json').write_text(json.dumps(report, indent=2, ensure_ascii=False)+'\n', encoding='utf-8')
    summary = report['summary']
    lines = ['# Campus RAG evaluation', '', f"Run: {report['label']} | {report['started_at']} | {len(report['results'])}/{report['planned_cases']} cases completed", '',
             '**Source-derived benchmark; staff review is pending.** Regex fact and evidence checks are regression signals, not a semantic proof of every claim. Sample policies remain demonstrations. A single run does not measure provider reliability over time.', '',
             f"Overall: **{summary['passed']}/{summary['cases']} passed**. Request errors: {summary['request_errors']}.", '',
             f"Blocked cases: **{summary['blocked_cases']}**. Retrieval modes: {summary['retrieval_modes']}. Generated-answer checks marked not evaluated require a working provider; excerpt fallback is not scored as a generated answer.", '',
             '| Check | Passed / evaluated | Not evaluated |', '|---|---|---|']
    for name in ['hybrid_retrieval', 'retrieval_hit_at_4', 'retrieved_evidence', 'expected_facts', 'relevant_citations', 'cited_evidence', 'generated_answers', 'abstention', 'source_access', 'valid_citation_identifiers']:
        count = summary[name]
        lines.append(f"| {name} | {count['passed']} / {count['total']} | {count['not_evaluated']} |")
    lines += ['', f"MRR: {summary['mean_reciprocal_rank']}. Latency: {summary['latency_seconds']}.", '', '## Cases', '']
    for row in report['results']:
        lines += [f"### {row['id']} — {row['question']}", '',
                  f"Result: {row.get('evaluation', {}).get('status', 'error').upper()} | {row['seconds']:.2f}s | {row.get('response', {}).get('generation_model') or row.get('request_error', 'no generation')}", '']
        if row.get('response', {}).get('generation_error'):
            lines += ['Provider: '+row['response']['generation_error'], '']
        if row.get('response', {}).get('retrieval_error'):
            lines += ['Retrieval: '+row['response']['retrieval_error'], '']
        if row.get('evaluation', {}).get('failures'):
            lines += ['Failed checks: '+', '.join(row['evaluation']['failures']), '']
        lines += ['Expected: '+row['reference_answer'], '', row.get('response', {}).get('answer', 'No response.'), '']
        for source in row.get('response', {}).get('sources', []):
            lines.append(f"- [{source['id']}] {source['title']}, page {source['page']}")
        lines.append('')
    prefix.with_suffix('.md').write_text('\n'.join(lines), encoding='utf-8')


async def run(args):
    suite = json.loads(args.suite.read_text(encoding='utf-8'))
    snapshot = load_snapshot()
    validate_references(suite, snapshot)
    cases = [case for case in suite['cases'] if not args.case or case['id'] in args.case]
    if args.retrieval_only:
        cases = [case for case in cases if case['expect'] == 'answer']
    if not cases:
        raise ValueError('No matching cases')
    prefix = ROOT / 'evaluation/reports' / args.label
    if prefix.with_suffix('.json').exists():
        raise ValueError('Use a new label to preserve previous runs')
    if args.rescore:
        report = json.loads(args.rescore.read_text(encoding='utf-8'))
        if report.get('suite_sha256') != hashlib.sha256(args.suite.read_bytes()).hexdigest():
            raise ValueError('The question suite changed; keep the matching original suite to re-score this run')
        report['original_label'] = report['label']
        report['label'] = args.label
        report['scorer_version'] = 3
        report['rescored_at'] = datetime.now(timezone.utc).isoformat()
        by_id = {case['id']: case for case in cases}
        for row in report['results']:
            if not row.get('request_error'):
                row['evaluation'] = score(by_id[row['id']], row['response'], snapshot)
        write_report(report, prefix)
        print(json.dumps(report['summary'], indent=2))
        return report['summary']['passed'] == len(report['results'])
    if args.retrieval_only and not os.getenv('STAFF_ACCESS_TOKEN') and not args.staff_email:
        raise ValueError('Retrieval-only evaluation requires --staff-email or a current staff Supabase JWT in STAFF_ACCESS_TOKEN')
    semaphore = asyncio.Semaphore(args.concurrency)
    async with httpx.AsyncClient(timeout=70) as client:
        if args.retrieval_only and args.staff_email and not os.getenv('STAFF_ACCESS_TOKEN'):
            password = getpass.getpass('Staff password: ')
            login = await client.post(args.base_url+'/api/auth/login', headers={'Origin': os.environ.get('APP_ORIGIN', '')},
                                      json={'email': args.staff_email, 'password': password})
            del password
            if login.status_code != 200:
                raise ValueError('Staff sign-in failed; check the account, role and website origin')
        health = (await client.get(args.base_url+'/api/health')).json()
        report = {'label': args.label, 'scorer_version': 3, 'evaluation_mode': 'retrieval-only' if args.retrieval_only else 'full',
                  'started_at': datetime.now(timezone.utc).isoformat(), 'planned_cases': len(cases),
                  'suite_sha256': hashlib.sha256(args.suite.read_bytes()).hexdigest(), 'health': health,
                  'references': suite['references'], 'results': []}
        async def evaluate(case):
            async with semaphore:
                start = time.monotonic()
                row = {key: case[key] for key in ['id', 'question', 'expect', 'category', 'reference_answer']}
                try:
                    endpoint = '/api/admin/evaluation/retrieval' if args.retrieval_only else '/api/chat'
                    headers = ({'Authorization': 'Bearer ' + os.environ['STAFF_ACCESS_TOKEN']} if os.getenv('STAFF_ACCESS_TOKEN') else {'Origin': os.environ.get('APP_ORIGIN', '')}) if args.retrieval_only else {}
                    response = await client.post(args.base_url+endpoint, json={'question': case['question']}, headers=headers)
                    response.raise_for_status()
                    row['response'] = response.json()
                    row['evaluation'] = score(case, row['response'], snapshot)
                except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
                    row['request_error'] = type(exc).__name__
                row['seconds'] = round(time.monotonic()-start, 3)
                report['results'].append(row)
                report['results'].sort(key=lambda item: next(i for i,c in enumerate(cases) if c['id']==item['id']))
                write_report(report, prefix)
                print(json.dumps({'case': row['id'], 'passed': row.get('evaluation', {}).get('passed', False),
                                  'failures': row.get('evaluation', {}).get('failures', []), 'error': row.get('request_error'),
                                  'completed': len(report['results']), 'total': len(cases)}), flush=True)
        await asyncio.gather(*(evaluate(case) for case in cases))
        report['completed_at'] = datetime.now(timezone.utc).isoformat()
        write_report(report, prefix)
        print(json.dumps(report['summary'], indent=2), flush=True)
        return report['summary']['passed'] == len(cases)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--staff-email', help='Retrieval-only: sign in with a password prompt instead of an environment JWT')
    parser.add_argument('--suite', type=Path, default=DEFAULT_SUITE)
    parser.add_argument('--base-url', default='http://127.0.0.1:8000')
    parser.add_argument('--label', default=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
    parser.add_argument('--concurrency', type=int, choices=range(1, 4), default=2)
    parser.add_argument('--case', action='append')
    parser.add_argument('--retrieval-only', action='store_true', help='Check 24 answerable questions without generation calls or answer-history writes')
    parser.add_argument('--rescore', type=Path, help='Re-score stored raw responses without any inference calls')
    args = parser.parse_args()
    if not re.fullmatch(r'[a-zA-Z0-9_-]+', args.label):
        parser.error('Use only letters, numbers, underscores and hyphens in the label')
    if args.rescore and (args.case or args.retrieval_only):
        parser.error('--rescore must re-evaluate the complete recorded run')
    try:
        raise SystemExit(0 if asyncio.run(run(args)) else 1)
    except (ValueError, KeyError) as exc:
        parser.exit(2, str(exc)+'\n')
