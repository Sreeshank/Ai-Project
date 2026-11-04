# Campus Compass

A campus information assistant with a Next.js/TypeScript frontend and a Python/FastAPI backend.

[Open the live website](https://campus-compass-web.onrender.com)

Hosted on Render. Free-tier services may take a moment to wake after inactivity.

## What works now

- Student questions retrieve matching passages with document/page citations. Optional Gemini embeddings and pgvector add semantic search, combined with keyword results using reciprocal rank fusion.
- Source details and original-document links, a searchable knowledge library, and a sample department directory.
- Optional Gemini answer generation; source excerpts remain available if generation fails.
- Supabase staff sign-in and role-protected administration: upload PDF, DOCX, TXT, or Markdown; approve or archive documents.
- Pending, archived, and expired documents are excluded from public retrieval and downloads.
- Document state, original files, reviewed pages, revisions, conversations and feedback persist in Supabase PostgreSQL when `DOCUMENT_BACKEND=postgres`; SQLite remains available for a local demo.
- Three clearly marked sample documents make the app usable without credentials.

**The configured deployment stores originals and document records in private Supabase PostgreSQL tables, alongside pgvector retrieval. Original file bytes use PostgreSQL `bytea`; this does not use a Supabase Storage bucket. Supabase Auth verifies staff identities; private application roles distinguish staff from administrators. The shared admin token is no longer accepted.** The app does not claim that keyword matches or model responses are verified answers. Staff must approve real source content and evaluate generated claims before a college rollout.

## Run on Windows

Requires Node.js 20.9+ and Python 3.11+.

```powershell
npm.cmd install
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend/requirements.lock.txt
```

Start both services with:

```powershell
.\scripts\start.ps1
```

Or use two terminals:

```powershell
# Terminal 1, repository root
.\.venv\Scripts\python.exe -m uvicorn backend.main:app --host 127.0.0.1 --port 8000

# Terminal 2, repository root
npm.cmd run dev
```

Open http://127.0.0.1:3000. API documentation: http://127.0.0.1:8000/docs.
The frontend proxies `/api` to the backend; there are no browser-side model secrets.

If virtual-environment creation reports an ensurepip error, the existing Python pip can install into it:

```powershell
python -m pip --python .venv install -r backend/requirements.txt
```

## Configure administration and optional AI

Copy `backend/.env.example` to `backend/.env` only when setting up a new installation. Preserve the existing configured file. Set `SUPABASE_URL`, `SUPABASE_PUBLISHABLE_KEY` (or legacy `SUPABASE_ANON_KEY`), and `APP_ORIGIN` to the exact website origin, such as `http://127.0.0.1:3000`. Staff account management requires `DOCUMENT_BACKEND=postgres` and the existing backend database connection. No service-role key is needed for the implemented flow.

Apply the private staff schema, create a confirmed identity in Supabase **Authentication → Users → Add user**, then assign the first administrator:

```powershell
.\.venv\Scripts\python.exe -m backend.manage_staff migrate
.\.venv\Scripts\python.exe -m backend.manage_staff bootstrap --email campus-admin@example.com
```

Use a real staff email for institutional use. `campus-admin@example.com` is the local demonstration account; its password is the one set when creating it in Supabase. This is separate from the database password. Bootstrap refuses to run once an active administrator exists. Restart the backend and open **Staff access** to sign in with email and password.

Staff can upload, review, approve, replace and archive documents. Administrators can additionally grant roles and enable/disable accounts under **Staff account**. Create and confirm additional identities in Supabase Authentication first, then grant their email access in the app. Unassigned, disabled, unconfirmed and anonymous accounts have no staff access. At least one active administrator must remain. Email messages and public sign-up are not initiated by this app. Roles come from `campus.staff_members`, never editable user metadata or client claims.

Sessions use host-only HttpOnly, SameSite=Lax cookies, with Secure enabled for HTTPS origins. Tokens are never returned to browser JavaScript or local storage. The backend verifies tokens through Supabase Auth and reads the current role on every protected request; revoked roles apply on the next request. Expired access tokens refresh server-side. Mutations using cookies require the exact `Origin`. Staff responses are private/no-store. Supabase outages fail closed; the app does not fall back to a shared token. Both roles can change their own password. Supabase Authentication also handles operator-managed password resets and identity lifecycle. The student-only SQLite demo remains usable without Auth credentials.

For generated answers, also set `GEMINI_API_KEY` and `GEMINI_MODEL` to an available model in your account. The backend uses the [Gemini Interactions API](https://ai.google.dev/gemini-api/docs/interactions-overview) with structured JSON output and `store: false`. Restart the backend. With no key, the app returns excerpts rather than pretending to generate an AI answer. If the provider fails or returns invalid citation identifiers, the app falls back to excerpts. Valid citation identifiers alone do not prove that every generated claim is supported; human evaluation is still required.

Each question is independent in this version; the visible conversation is not sent as model history. Generated answers are not streamed. Chat transcripts in the browser reset on reload. Submitted questions and answers are stored in the configured document database (`campus.answers` in PostgreSQL, or `backend/data/campus.db` for the SQLite demo).

All bundled documents, department names, and locations are **demonstration content**. Replace them before using the system for real student guidance. Archive each sample document from the administrator interface. Contacts are deliberately not invented.

## Upload workflow

1. Sign in through **Staff access**.
2. Upload a source, title, department, and optional expiry date.
3. Open Review: compare each full page with the original image. Tables, headings and lists are rendered separately from search passages.
4. For a bad scan, use Run OCR again. Correct any mistakes in Edit, confirm you checked all pages, and Save review before approving the document.
5. Ask a question and inspect its source citations.
6. Archive superseded documents so they stop appearing in new searches.

Limits: 10 MB per file, 300 pages per PDF. Text PDFs use their text layer; scans use local RapidOCR. OCR models download from the official RapidOCR model source on first use and are cached under `backend/data/ocr-models`. English recognition is configured; other scripts and handwriting need manual checking. DOCX paragraphs and tables are converted into a normalized text source, referenced as page 1 rather than guessing Word page numbers. Extraction runs in a thread pool so it does not block chat requests. Upload/reprocess requests still wait for processing; a durable background job queue is a follow-up for large collections.

## Checks

```powershell
npm.cmd run build
.\.venv\Scripts\python.exe -m unittest discover -s backend/tests -v
```

Tests use a temporary database and cover retrieval, citations, abstention, authentication, approval, archival, expiry, uploads, and feedback. They do not call an external AI provider.

## Layout

```text
app/                       Next.js interface and styles
backend/main.py            API, ingestion, approval, hybrid retrieval, Gemini
backend/auth.py            Supabase identity checks, cookies, private staff roles
backend/manage_staff.py    Role migration and first-administrator bootstrap
backend/semantic.py        Gemini embeddings, pgvector adapter, rank fusion
backend/indexing.py        Durable indexing queue and stale-result checks
backend/manage_vectors.py Database migration and reindex commands
backend/database.py       Supabase/private schema and local database connections
backend/manage_storage.py Verified migration of SQLite documents and history
backend/generation.py     Grounded answers, model failover and diagnostics
backend/tests/             Isolated API workflow tests
backend/.env.example       Server configuration template
supabase/migrations/       Legacy hosting schema and active semantic index schema
scripts/start.ps1          Start the local frontend and backend
```

## Next milestones

1. Complete human staff review of the 30-question source-derived RAG evaluation. The latest live automated run passed 30/30; see `evaluation/README.md`.
2. Extend local OCR with language selection and a durable background ingestion queue for larger collections.
3. Replace sample department contacts with verified college details.
4. Exercise recovery on a separate Supabase project and validate Docker/HTTPS on the deployment host. Staff authentication and document replacement/versioning are implemented.

College rollout still requires approved source content, real staff identities, privacy/retention configuration and operational monitoring.

## References

- Next.js: https://nextjs.org/docs/app/getting-started/installation
- FastAPI uploads: https://fastapi.tiangolo.com/tutorial/request-forms-and-files/
- Supabase hybrid retrieval: https://supabase.com/docs/guides/ai/hybrid-search
- Gemini generation: https://ai.google.dev/api/generate-content
- Local OCR: https://github.com/RapidAI/RapidOCR
- PDF page rendering: https://pypdfium2.readthedocs.io/en/stable/python_api.html

## Page review and corrections

Full extracted pages are stored separately from overlapping retrieval chunks. A saved review rebuilds the document search index and returns an active document to pending approval; archived documents stay archived. OCR, legacy, and fallback extraction must be reviewed before approval. Original source bytes are preserved, and earlier page text is saved to `document_revisions` before re-extraction or editing. Page images are available only through authenticated administrator endpoints. OCR recognition scores are diagnostic signals, not accuracy guarantees.

## Semantic embeddings and pgvector

The embedding path uses `gemini-embedding-001`, 768 dimensions, retrieval-specific document/query tasks and normalized vectors. It is independent of the answer-generation model: a Gemini generation outage does not necessarily affect embeddings. The vector table in `public` is a **derived index**; the authoritative documents live in private schema `campus` when `DOCUMENT_BACKEND=postgres`, or SQLite for the local demo.

1. In Supabase, open **Connect → Session pooler** (or use a direct PostgreSQL connection if reachable). The HTTPS project API URL is not a database URI.
2. Set these **backend-only** values in `backend/.env`:

   ```dotenv
   DATABASE_URL=postgresql://postgres.PROJECT:PASSWORD@POOLER_HOST:5432/postgres?sslmode=require
   EMBEDDING_MODEL=gemini-embedding-001
   ```

   Replace placeholders with the dashboard URI. URL-encode special characters in the database password. Keep `GEMINI_API_KEY` as configured. Do not place database credentials in `.env.example` or `NEXT_PUBLIC_*` variables.
3. Install the updated Python requirements, then apply **002** (it is self-contained; **001** is an unused legacy hosting schema):

   ```powershell
   .\.venv\Scripts\python.exe -m backend.manage_vectors migrate
   .\.venv\Scripts\python.exe -m backend.manage_vectors reindex
   ```

4. Restart both services using `scripts/start.ps1`. The backend drains the durable queue. Check progress:

   ```powershell
   .\.venv\Scripts\python.exe -m backend.manage_vectors status
   ```

The migration enables pgvector, creates a private `campus_semantic_chunks` table with a cosine HNSW index, and enables RLS without anonymous policies. Use a backend database role authorized to manage this table; the normal Supabase project database connection works. The migration does not copy originals or change the legacy `campus_documents` tables. For Supabase connections, keep `sslmode=require` in the URI.

Uploads, reviewed edits, re-extraction, approval and archival enqueue index changes in the same database transaction. The worker indexes approved, unexpired text and caches embeddings in the configured database to avoid paying for identical content again. Replacements are atomic in PostgreSQL. Failed jobs retry with backoff and survive restarts. A crash after a remote write is safe to retry. Archive/review actions remain immediate in the authoritative database: chat rejects stale remote revisions and rechecks approval/expiry before returning any source. PostgreSQL is eventually consistent; new documents may use keyword retrieval until indexing finishes.

Questions embed as retrieval queries, retrieve up to 24 vector candidates, discard cosine similarity below 0.5, validate them locally, then combine their ranks with the top 20 keyword passages. Chat returns up to four citations. If PostgreSQL or embeddings fail, keyword search remains available. Chat's `retrieval` field reports `hybrid-pgvector`, or the active keyword engine (`postgres-fts` / `sqlite-fts5`, with `-fallback` when the semantic provider failed). This retrieval field is independent of `mode` (the answer generator). Similarity is not proof that a passage answers the question.

`GET /api/admin/search/status` and `POST /api/admin/search/reindex` require the administrator header, like document management. No credentials are returned. Health reports configuration and queue counts; a configured URI does not confirm PostgreSQL is reachable. The real pgvector integration test runs only when a separate `TEST_DATABASE_URL` is supplied; it applies migration 002, creates a temporary test document, checks cosine search/model filtering and atomic rollback, then removes that fixture.

References: [Gemini embeddings](https://ai.google.dev/gemini-api/docs/embeddings), [pgvector](https://github.com/pgvector/pgvector), [Supabase hybrid search](https://supabase.com/docs/guides/ai/hybrid-search).

## Supabase document repository

The production configuration uses `DOCUMENT_BACKEND=postgres`. All originals, full pages, review revisions, status, expiry, sample flags, filenames, chunks, indexing jobs, embedding cache, answers and feedback are stored in **Supabase PostgreSQL**, in the private `campus` schema. Original files use `campus.documents.original` (`bytea`) and are served through the existing approval-checked API routes. They are **not objects in Supabase Storage**. The 10 MB upload limit remains enforced. Tables enable RLS and have no browser grants; staff token checks and source access rules continue in FastAPI. The app does not silently switch back to an old SQLite copy if PostgreSQL fails.

Migration procedure (stop the running backend first so the snapshot is consistent):

```powershell
# Preserve a SQLite backup before this command.
.\.venv\Scripts\python.exe -m backend.manage_storage
```

This applies migrations `003_document_repository.sql` and `004_document_versions.sql`, copies records in one PostgreSQL transaction, compares every imported field (including exact original bytes and revision payloads), updates the chunk sequence, and queues indexing. It preserves identifiers, page references, approval state, and original files. Existing cloud rows that disagree cause rollback; it never overwrites newer cloud data. It does not delete the SQLite source. After success, set `DOCUMENT_BACKEND=postgres` in `backend/.env` and restart. **Do not rerun an old SQLite migration after making new cloud edits**; keep that file as a backup rather than an active source. In Supabase’s Table Editor, select schema `campus` to inspect the records. The existing `public.campus_semantic_chunks` remains the vector index. Migration 001 is still unused.

Health includes `document_storage: supabase-postgres`. The local SQLite copy no longer receives uploads, chat records or reviews once PostgreSQL is selected. OCR model files remain local. The preserved pre-migration backup is in `work/backups/campus-before-cloud-storage.sqlite`.

## Gemini answer reliability

`GEMINI_MODEL=gemini-3.5-flash-lite` is the configured primary model that passed the live grounded-answer check. `GEMINI_FALLBACK_MODELS=gemini-3.6-flash` provides an alternate. The earlier `gemini-3.8-flash` repeatedly timed out or returned high-demand errors. Each attempt has a 12-second timeout, up to three distinct configured models, and a short delay between attempts. Transient errors, timeouts and unavailable models can fail over. Access errors stop immediately. Invalid JSON or citations always fall back to excerpts, so model failover cannot bypass the citation check. No sources means no generation call.

Successful chat responses include `generation_model`; excerpt fallback includes a safe `generation_error` code such as `provider_busy`, `provider_timeout`, or `invalid_answer_or_citations`. Provider response bodies, database URIs and credentials are never returned. Provider outages can still cause excerpt fallback. Automated tests mock those failures; live checks separately verify generation, retrieval and citations.

## Repeatable RAG evaluation

The benchmark in `evaluation/campus_questions.json` includes 30 source-pinned campus questions, missing-information and archived-document checks, sample labels and user instruction-override attempts. Run `python -m backend.evaluation --label UNIQUE_LABEL` for a full live evaluation, or add `--retrieval-only` for the 24 answerable questions without generation or chat-history writes. The latter calls `/api/admin/evaluation/retrieval` with a current Supabase staff session (CLI: `--staff-email` with a password prompt, or a short-lived `STAFF_ACCESS_TOKEN`). Reports separate keyword fallback from actual hybrid retrieval and provider outages from answer quality. Full usage, limitations and the historical provider failures are recorded in `evaluation/README.md`. The fresh `evaluation/reports/acceptance-final.md` run passed all 30 automated cases with actual hybrid retrieval, 24 grounded answers and six abstentions. Human staff review remains pending. A non-zero exit code is intentional when a provider is blocked; it is not a passing quality gate.

`generation_error=provider_billing_required` identifies a Gemini HTTP 402 response. `retrieval_error=embedding_billing_required` identifies the same failure for query embeddings. Other safe retrieval codes distinguish access, timeouts, rate limits and vector-database errors. Provider bodies and credentials are not returned.

## Document operations and deployment

The staff workspace now shows observed provider availability (not just configured credentials), search indexing progress, failed-job retries, and expired/soon-expiring documents. **Check AI services** performs small live checks; status otherwise describes the latest observed request since restart. Refresh does not call the AI provider. Department and status filters help navigate the collection.

Use **Replace file** to upload a pending successor. The earlier approved file stays public until the successor is reviewed and approved, at which point all predecessors are archived atomically. Original identifiers remain distinct so old citations cannot silently point to new file contents. Earlier versions and original files remain accessible only through authenticated history tools. Saved metadata changes return active documents to pending review; archive state is preserved. **Versions & history** also exposes earlier extracted-text revisions.

Migration `004_document_versions.sql` adds the private replacement relationships. For SQLite demos the table is created on initialization. Existing source data is preserved. See `deploy/README.md` for the backup/empty-target restore commands, request limits, structured error logs, Docker services and HTTPS reverse-proxy configuration. Deployment files are prepared; no server/domain has been provisioned and the Docker images have not been tested on this Windows machine. The current key now passes live generation/embedding checks; evaluation reports preserve earlier provider failures separately.


## Hosting on Render

The selected hosting configuration is `render.yaml`: a Next.js Node web service and a FastAPI Docker web service, both using Supabase. See `deploy/README.md` for secret configuration, frontend/backend URL wiring and free-tier limitations. Both services are deployed on Render. Open the live website linked above. Future deployments require access to the Render workspace.
