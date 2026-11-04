# Campus RAG evaluation

This is a source-derived **draft benchmark**, not a staff-approved college policy test. It contains 30 questions: 15 real-notice cases, 7 sample-policy cases, 4 missing-information cases, 1 archived-content case, and 3 instruction-override cases. Twenty-four questions are answerable from the pinned sources; six require abstention. Staff access now uses Supabase Auth with private application roles.

`campus_questions.json` stores expected facts, forbidden claims, document/page identifiers and SHA-256 hashes of the full reference pages. The runner rejects changed reference text or status. Review and update the gold answers when sources change; do not just replace hashes to make the check pass. Sample-source answers must be clearly labelled demonstrations. No approval state or document text is changed by this tool.

## Run

Start the application, then run from the project directory:

```powershell
# Full 30-question live run. Calls embeddings and generation and writes normal
# chat-history rows, just like questions asked through the website.
.\.venv\Scripts\python.exe -m backend.evaluation --label full-check-1

# Read-only check of the 24 answerable questions. Uses the exact production
# retriever through an staff-session-protected endpoint. It makes no generation
# calls and does not write chat history. It still calls the embedding provider.
.\.venv\Scripts\python.exe -m backend.evaluation --label retrieval-check-1 --retrieval-only --staff-email campus-admin@example.com

# Recheck one question. Use a new label for each run.
.\.venv\Scripts\python.exe -m backend.evaluation --label fee-check-1 --case fee-regular

# Re-score stored responses using the matching suite, without inference calls.
.\.venv\Scripts\python.exe -m backend.evaluation --label rescored-1 --suite evaluation/campus_questions.v1.json --rescore evaluation/reports/baseline.json
```

Provider credentials load from `backend/.env`; no keys or passwords are written to reports. Retrieval-only mode prompts for the staff password when `--staff-email` is used and keeps session cookies only in the running client. Alternatively set a current staff Supabase JWT in the process environment as `STAFF_ACCESS_TOKEN`; it expires and is not a replacement shared secret. Full public-chat runs need no staff login. `--concurrency 1` is available for a slower run; the default is 2. `--base-url` defaults to `http://127.0.0.1:8000`. Markdown and raw JSON reports are checkpointed in `evaluation/reports`. Existing labels cannot be overwritten. Exit code 0 means all selected checks passed; 1 includes failures or blocked cases; 2 indicates an invalid fixture/configuration.

## Read the results

- **hybrid_retrieval:** confirms that pgvector + embeddings actually ran. A configured health mode alone is not proof. PostgreSQL keyword fallback does not pass this check.
- **retrieval_hit_at_4 / MRR:** measure the expected document/page among the returned passages.
- **retrieved_evidence:** required evidence patterns must occur in the returned passages, not merely somewhere in the right document.
- **source_access:** every returned excerpt must belong to a current approved page; archived, expired, unknown and fabricated passages fail.
- **expected_facts:** check declared answer patterns, only for generated answers.
- **relevant_citations / cited_evidence:** a generated answer must cite the expected document/page and the cited passages must contain the required evidence.
- **abstention:** unknown questions must produce an insufficient-evidence answer rather than an invented fact. Provider failures do not count as correct abstention.
- **sample_label / forbidden claims:** check demonstration labels and explicit attack markers or prohibited claims.

Generation and semantic-provider failures are reported separately. A billing block does **not** count as a fact-accuracy measurement; those checks say `not evaluated`. Retrieval-only runs also mark generation checks not evaluated. Request errors count against the overall score. Latency is wall time through the endpoint, with median and nearest-rank p95.

The checks are deliberately conservative regression signals. Regex matches do not verify every claim, qualifier, omitted detail or citation placement. A staff reviewer should read every reference answer, compare the actual answer to the cited original page, inspect failures and unknown-question responses, and record their sign-off separately. A single live run does not establish reliability across later provider outages or policy changes.

## Current result

The fresh `reports/acceptance-final.md` run passed **30/30 automated cases** with the current key. All 30 requests used actual hybrid pgvector retrieval, all 24 answerable cases passed evidence/fact/citation checks, and all six missing-information cases abstained. There were no provider failures or request errors. Median endpoint latency was 12.92 seconds and p95 was 14.54 seconds. This is one automated source-pinned run; human staff review remains pending.

Earlier reports are retained: `baseline-final`/`retrieval-final` used keyword fallback while Gemini reported depleted credits (402); `changed-key-preflight` reported access denied (403). Those are historical observations, superseded by the current successful live checks. `release-evaluation` was the first working-provider run; `release-final` passed 29/30 before the support-flag correction. See `CHANGES.md` for the preserved suite and scorer history. Reports are never overwritten to hide failures.
