# Deployment preparation

The local application is configured against Supabase PostgreSQL. Staff sign-in uses Supabase Auth and private staff/admin roles; the shared token is no longer accepted. This folder prepares a single-server deployment and does not publish it or purchase hosting.

## Database and secrets

Apply migrations 002, 003, 004 and 005 for a new PostgreSQL repository. For the existing migrated project, 004 and 005 are additive; do not reimport the old SQLite backup. Keep `DATABASE_URL` (with SSL), `GEMINI_API_KEY`, the model settings, `SUPABASE_URL`, `SUPABASE_PUBLISHABLE_KEY` and `APP_ORIGIN` in `backend/.env`. Docker excludes `.env` and document data from image build contexts. The backend receives secrets at runtime. No browser `NEXT_PUBLIC_*` secret is needed.

## HTTPS hosting

Requires Docker Engine with Compose, a server and a domain you control pointing to it. Ports 80 and 443 must reach Caddy. In a shell at the project root:

```powershell
$env:CAMPUS_DOMAIN='campus.example.edu'
docker compose -f deploy/compose.yaml build
docker compose -f deploy/compose.yaml up -d
docker compose -f deploy/compose.yaml logs --tail 100
```

Replace the example domain. Compose sets backend `APP_ORIGIN` to `https://CAMPUS_DOMAIN` so cookie security and CSRF checks match the deployed website. Caddy terminates HTTPS and routes `/api/*` to FastAPI, with all other requests going to Next.js. Only Caddy publishes host ports. Keep backend/frontend ports private: Uvicorn trusts proxy headers only within this deployment's private container network. The Linux Docker build enables `BUILD_STANDALONE=1` for standalone output (the Windows local build uses the regular Node output); static assets are copied into the production image. Caddy persists certificate state in named volumes. Verify DNS, HTTPS and the actual container build on the deployment host before treating a deployment as complete. The current Windows development machine has not built or launched these Docker images.

The key previously reported Gemini project access denied (403); the latest live checks now succeed. Successful container startup alone does not prove provider access. Staff can use **System status → Check AI services** to check actual inference access. A status of `not checked` is intentionally distinct from working service access.

## Backups and recovery

```powershell
.\.venv\Scripts\python.exe -m backend.backup create
.\.venv\Scripts\python.exe -m backend.backup verify --file work/backups/YOUR-BACKUP.json.gz
```

The document export excludes Supabase Auth identities, live sessions and `campus.staff_members` role grants. Maintain Auth recovery separately with Supabase. For a different project, recreate/confirm identities, bootstrap one administrator with `backend.manage_staff`, and regrant staff roles after document restoration. It includes original bytes, full pages, text revisions, replacement relationships, approval/expiry metadata, chat records, feedback and indexing/cache state. The PostgreSQL snapshot is repeatable-read, with a SHA-256 integrity check and gzip compression. It does not include environment secrets. Backups contain source documents and chat data; store them in a protected off-server location with your institution's retention policy. The checksum detects accidental damage; it is not encryption or a signature.

Recovery uses a **separate empty database** with migrations applied and its connection configured in `backend/.env`:

```powershell
.\.venv\Scripts\python.exe -m backend.backup restore --file PATH-TO-BACKUP.json.gz
```

Restore refuses a nonempty target, inserts records in one transaction and queues reindexing. It never clears an existing database. Review source counts and byte hashes, restart the backend, then verify source downloads and retrieval. The local automated suite tests a backup/restore round trip, exact original bytes and refusal to restore over existing records. The cloud export is also verified directly. A restore to a new Supabase project should be exercised as an institutional recovery drill; no live production restore was performed here.

For containers, run `docker compose -f deploy/compose.yaml exec backend python -m backend.backup create`. Copy the backup out of the container/volume to an independent storage location. Set a daily backup schedule in your deployment's scheduler; no recurring automation is configured by these files.

## Request limits and logs

Chat defaults to 30 requests per minute per observed client IP; admin endpoints default to 120. Login/password endpoints default to 10 per minute via `AUTH_REQUESTS_PER_MINUTE`; session checks and logout use the admin bucket. Configure `CHAT_REQUESTS_PER_MINUTE`, `ADMIN_REQUESTS_PER_MINUTE`, or `RATE_LIMIT_ENABLED` on the backend. Rejected requests return 429 and `Retry-After`. Counters are bounded and in memory for this single-process deployment; multiple replicas need a shared limiter before scaling. Reverse proxies must supply the real client IP as configured above.

Responses include an `X-Request-ID`. Errors log a request ID, path, status and/or exception class without request bodies, query strings, credentials or provider error bodies. Caddy filters request/response headers and request URIs out of both access and runtime logs, including the custom administrator header. Backend path-only error logs retain request routing context. Inspect `docker compose logs backend` and `docker compose logs gateway`; integrate those streams with your hosting provider's monitoring and alerts. No external monitoring account is required or provisioned. Database failures return a generic 503; provider failures preserve source excerpts with safe diagnostic codes.

## Verification

Run the Python suite and `npm run build`, then check the actual deployment domain: public document listing/downloads, staff rejection without a session and rejection of the old token, provider checks, indexing state, replacement approval/archival, and a sample question. Test staff and administrator permissions independently, disabling access, sign-out, expiry/refresh and rejection of cross-site mutations. Create confirmed Auth identities through Supabase, bootstrap only the first administrator, and grant subsequent roles in the staff workspace. See the root README for exact commands.

References: [Next.js standalone output](https://nextjs.org/docs/app/api-reference/config/next-config-js/output), [Caddy reverse proxy](https://caddyserver.com/docs/caddyfile/directives/reverse_proxy), [Supabase Auth users](https://supabase.com/docs/guides/auth/users), [Supabase server-side sessions](https://supabase.com/docs/guides/auth/server-side/advanced-guide), [Caddy log filters](https://caddyserver.com/docs/caddyfile/directives/log#filter).


## Render frontend and backend (selected deployment)

The root `render.yaml` creates two web services from the same GitHub repository:

| Service | Runtime | Build / start |
| --- | --- | --- |
| `campus-compass-web` | Node.js 22 | `npm ci --include=dev && npm run build`; Next.js binds `0.0.0.0:$PORT` |
| `campus-compass-api` | Docker | `deploy/Dockerfile.backend`; FastAPI binds port 8000 |

The frontend's `BACKEND_URL` references the backend's Render `RENDER_EXTERNAL_URL`. Next.js bakes this into its `/api` rewrite during the build. Public HTTPS is used because Render free services do not provide private-network ingress. Render supplies TLS; the Compose/Caddy deployment is not used for this setup.

Create a Blueprint in Render from `https://github.com/Sreeshank/Ai-Project`, using `render.yaml`. Supply the backend's `DATABASE_URL`, `GEMINI_API_KEY`, `SUPABASE_URL`, `SUPABASE_PUBLISHABLE_KEY` and `APP_ORIGIN` privately when prompted. For CLI-assisted deployment, store a Render API key as `RENDER_API_KEY` in local `backend/.env`; it must never be committed or sent to either application's runtime environment.

Set backend `APP_ORIGIN` to the **actual frontend Render HTTPS URL**, not the backend address. You may initially use `https://setup.invalid` to keep login blocked while Render allocates the URLs; update it before testing sign-in. This is a manual value to avoid a circular dependency between the services. The frontend requires no database, model or Auth secrets. Cookies remain on the frontend hostname through the API proxy. Both staff sign-in and mutations require the exact configured frontend origin.

Reuse the existing Supabase database and confirmed staff identities; do not import the old SQLite database. Source originals, roles and durable indexing jobs remain there. The API image runs as the nonroot campus user.

Both services explicitly select the free tier for a demo. Each sleeps when idle, the indexing worker pauses while the API sleeps, and OCR cache files disappear on restart. First requests can be slow. OCR may exceed free-instance memory; paid always-on hosting requires a separate decision. Backups written inside the container must be exported off-server.

After creation, check both Render deploys, backend `/api/health`, frontend `/api/health` through its proxy, sign-in, one source download, one grounded answer and indexing. If the API URL changes, rebuild the frontend; if the frontend URL changes, update backend `APP_ORIGIN`. A successful local Next.js build does not validate Render's remote Docker build.

The earlier Vercel project is not used by this selected deployment. Its optional configuration remains in the repository; no Vercel project is deleted.

References: [Render Next.js](https://render.com/docs/deploy-nextjs-app), [Blueprint configuration](https://render.com/docs/blueprint-spec), [free-service limits](https://render.com/docs/free).
