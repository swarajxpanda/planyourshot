# Plan Your Shot

Turn a screenplay into a production-ready shot list with an AI assistant that has read the whole script.

- **Sign in with Google** — each account has its own private workspace.
- **Upload** screenplays (PDF, DOCX, TXT) — 2 per account on the free plan, up to 45,000 characters each.
  Rename, delete, re-download.
- **Screenplay assistant** — one conversation per script. *Discuss* mode answers questions and gives notes;
  *Edit* mode prepares find/replace changes that you review (accept all, some, or none) before anything touches
  the script. Every change is a new version; the original is always kept and any version can be restored.
  The assistant stays on topic: it only discusses this screenplay, its shot list and filmmaking craft for the
  project, and declines anything else with a fixed reply.
- **Shot list** — generated scene by scene as structured data (size, angle, movement, lens, description,
  composition, characters, props, intent). Edit inline, add, duplicate, delete, drag to reorder (within or
  across scenes), regenerate one shot or one scene, revise conversationally. CSV export is a Pro feature.
- **Fair-use limits** — per-plan quotas on assistant messages and generations, a per-minute request limit,
  one assistant request at a time per user, and rate limits on sign-in and uploads (see [Plans and limits](#plans-and-limits)).
- **Your data** — export everything (each screenplay's text, shot list as CSV, conversations as JSON) as a zip
  from the account menu on any plan, or delete the account and all of its data. Terms and Privacy Policy are
  public at `/terms` and `/privacy`.
- **Operations** — durable background jobs with retries, Postgres in production, optional Sentry, JSON request
  logs, health checks, and an `/admin` page for operators.

## Stack

| Layer | Choice |
| --- | --- |
| Backend | FastAPI, SQLAlchemy 2, Alembic (`src/planyourshot/web`) |
| Database | Postgres in production (psycopg 3); SQLite for local development and tests |
| Jobs | `jobs` table + worker process (`python -m planyourshot.web.worker`) |
| Auth | Google OAuth (Authlib), signed HTTP-only session cookie |
| LLM | LangChain — local Ollama by default (`LLM_PROVIDER=ollama`) or Gemini (`LLM_PROVIDER=gemini`), Pydantic structured output |
| Retrieval | Lexical (TF-IDF) retrieval over `data/knowledge` and over scenes for very long scripts |
| Frontend | React 18 + TypeScript + Vite (`frontend/`), plain CSS |

The original CLI pipeline (`loader`, `splitter`, `parse_scene`, `pipeline`, `knowledge_base`) is unchanged and
the web app builds on it: the scene-heading grammar comes from `splitter`, PDF clean-up from `loader`, and the
cinematography notes in `data/knowledge` are fed into shot generation.

## Setup

Requires Python 3.12+, [uv](https://docs.astral.sh/uv/), Node 20+, and [Ollama](https://ollama.com) running
with a model pulled (`ollama pull <model>`; set the same name as `LLM_MODEL`).

```bash
uv sync
cp .env.example .env          # then fill it in (see below)
cd frontend && npm install && npm run build && cd ..
uv run uvicorn planyourshot.web.app:app --port 8000
```

Open http://localhost:8000. Database migrations run on startup. With SQLite the job worker runs inside the
web process, so this one command is all you need locally.

### Environment variables

| Variable | Required | Notes |
| --- | --- | --- |
| `LLM_PROVIDER` | | `ollama` (default) or `gemini` |
| `LLM_MODEL` | yes | Model name exactly as in `ollama list` (or a Gemini model name) |
| `OLLAMA_BASE_URL` | | Default `http://localhost:11434` |
| `OLLAMA_NUM_CTX` | | Context window in tokens, default 16384. Raise it if your machine can take it |
| `GEMINI_API_KEY` | Gemini only | https://aistudio.google.com/apikey |
| `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` | yes | OAuth client, type *Web application* |
| `APP_BASE_URL` | yes in prod | Public URL, e.g. `https://planyourshot.example.com` |
| `SESSION_SECRET` | yes in prod | Long random string you generate yourself: `python -c "import secrets; print(secrets.token_urlsafe(48))"` |
| `DATABASE_URL` | yes in prod | Default `sqlite:///data/app.db`. Postgres: `postgresql+psycopg://user:pass@host/db`; `postgres://` and `postgresql://` are rewritten to psycopg |
| `DB_POOL_SIZE`, `DB_MAX_OVERFLOW` | | Postgres pool per process, defaults 5 and 5 (with `pool_pre_ping`) |
| `STORAGE_DIR` | | Default `data/uploads`. Shared by web and worker: one persistent volume |
| `MAX_UPLOAD_MB` | | Default 10 |
| `LLM_TIMEOUT_SECONDS` | | Per model call, default 120. A timeout is reported to the user as a friendly error |
| `LLM_MAX_OUTPUT_TOKENS` | | Longest model reply, default 2000 (`num_predict` for Ollama, `max_output_tokens` for Gemini) |
| `MAX_CONCURRENT_LLM_CALLS` | | Model calls in flight across all users, default 2 |
| `AUTH_RATE_LIMIT_PER_MINUTE` | | Requests per client IP to `/api/auth/*`, default 20 |
| `UPLOAD_RATE_LIMIT_PER_HOUR` | | Upload attempts per user, default 10 |
| `FREE_*`, `PRO_*` | | Override any plan limit, e.g. `FREE_MESSAGES_PER_DAY=30` — see [Plans and limits](#plans-and-limits) |
| `RUN_WORKER_IN_WEB` | | Run the job worker in a thread of the web process. Default on for SQLite, off for Postgres |
| `JOB_STALE_SECONDS` | | A running job with no heartbeat for this long is requeued, default 600 |
| `JOB_POLL_SECONDS`, `JOB_RETRY_DELAY_SECONDS` | | Idle poll interval (default 2) and retry back-off base (default 15; attempt n waits n × this) |
| `SENTRY_DSN` | | Backend error tracking; off when unset |
| `SENTRY_ENVIRONMENT`, `SENTRY_TRACES_SAMPLE_RATE` | | Defaults `production` (https) / `development`, and `0` (no tracing) |
| `VITE_SENTRY_DSN` | | Frontend error tracking, read at **build** time (`--build-arg VITE_SENTRY_DSN=…`); the backend also reads it to allow it in the CSP |
| `ADMIN_EMAILS` | | Comma-separated emails allowed into `/admin`; everyone else gets 404 |
| `LEGAL_DIR` | | Where `privacy.md` and `terms.md` live, default `legal/` |
| `AUTO_MIGRATE` | | Default `1`: web and worker run migrations on start (serialised by a Postgres advisory lock) |
| `FRONTEND_URL` | dev only | `http://localhost:5173` when using the Vite dev server |
| `AUTH_DEV_LOGIN` | dev only | `1` enables an email-only sign-in. The app refuses to start with it on over https |
| `RUN_JOBS_INLINE` | tests only | Run each job synchronously when queued |

### Google OAuth

1. Google Cloud Console → APIs & Services → Credentials → *Create credentials* → *OAuth client ID* → *Web application*.
2. Authorised redirect URI: `<APP_BASE_URL>/api/auth/callback` (locally `http://localhost:8000/api/auth/callback`).
3. Put the client ID and secret in `.env`. Configure the consent screen with the `email` and `profile` scopes.

### Development

```bash
# terminal 1 — API with reload
FRONTEND_URL=http://localhost:5173 uv run uvicorn planyourshot.web.app:app --reload
# terminal 2 — UI with hot reload (proxies /api to :8000)
cd frontend && npm run dev
```

Tests (no network or API key needed — the model is replaced with a test double; jobs run synchronously):

```bash
uv run pytest                                                           # SQLite
TEST_DATABASE_URL=postgresql://user:pass@localhost/pys_test uv run pytest  # Postgres
```

`TEST_DATABASE_URL` must point at a throwaway database: its `public` schema is dropped before every test.

### Migrations

```bash
uv run alembic upgrade head                              # apply (also runs on app start)
uv run alembic revision --autogenerate -m "describe it"  # after changing web/models.py
```

Set `AUTO_MIGRATE=0` to manage migrations yourself.

## Deployment

### Two processes

| Process | Command | Does |
| --- | --- | --- |
| web | `uvicorn planyourshot.web.app:app` (image: `web`) | API + built frontend. Inserts jobs; never runs them (unless `RUN_WORKER_IN_WEB=1`) |
| worker | `python -m planyourshot.web.worker` (image: `worker`) | Claims and runs jobs: upload processing and shot generation |

Both share the database and the uploads directory. Everything that must hold across processes lives in the
database: the job queue, the rate limiter, the one-model-call-per-user lock and the global model-call cap
(expiring leases, so a crashed process can't leave a user locked out). Run more workers to process more jobs
in parallel; claims are exclusive (`FOR UPDATE SKIP LOCKED` on Postgres).

**Jobs** — a job is retried up to 3 times when its handler crashes, with a growing back-off. A running job
whose heartbeat is older than `JOB_STALE_SECONDS` (its worker died) goes back to the queue on the same attempt
budget; after the last attempt it fails and the screenplay shows a message. Shot generation commits each scene
together with a checkpoint, so a retried job continues with the first unfinished scene without touching
finished ones. Deleting a screenplay cancels its jobs. On `SIGTERM` the worker finishes the model call in
progress and puts the job back without using an attempt.

### Docker Compose

`docker-compose.yml` runs Postgres, web and worker from one image with one uploads volume:

```bash
cp .env.example .env              # set APP_BASE_URL, SESSION_SECRET, Google OAuth, LLM settings
docker compose up -d --build
docker compose logs -f web worker
```

Compose sets `DATABASE_URL` to its Postgres service (`POSTGRES_PASSWORD` defaults to `pys` — set it in `.env`)
and `RUN_WORKER_IN_WEB=0`. Ollama on the host is reached at `http://host.docker.internal:11434`. Without
Compose the same image runs either role: `docker run … planyourshot web` / `docker run … planyourshot worker`
(or `migrate`). A single container with SQLite still works: `docker run -v pys-data:/data planyourshot`, with
`RUN_WORKER_IN_WEB=1`.

### Monitoring

- `GET /api/health` — `200` when the database answers, `503` otherwise.
- `GET /api/health/worker` — queue depth and the age of the oldest ready job; `503` once that is older than
  `JOB_STALE_SECONDS` (no worker is taking work).
- Request logs are JSON lines on stdout: method, path, status, duration, user id. Bodies are never logged.
- Sentry (backend `SENTRY_DSN`, frontend `VITE_SENTRY_DSN`) is off unless configured. Default PII is off,
  request bodies, cookies, query strings, stack variables and console breadcrumbs are dropped, and the user
  is reduced to an id.
- `/admin` (emails in `ADMIN_EMAILS`): model calls, failures and refusals per day, queue depth, failed jobs,
  top users, and enabling/disabling accounts.

### Security

- With an `https://` `APP_BASE_URL` the app refuses to start if `SESSION_SECRET` is unset, `AUTH_DEV_LOGIN`
  is on, or Google OAuth isn't configured; the session cookie is `Secure` and HSTS is sent.
- Every response carries a Content-Security-Policy (self, Google Fonts, Google profile images, Sentry when
  configured; `frame-ancestors 'none'`), `X-Content-Type-Options: nosniff`, `Referrer-Policy` and
  `X-Frame-Options: DENY`. `/api/docs` is exempt from the CSP because Swagger UI loads from a CDN.
- Behind a reverse proxy the image runs uvicorn with `--proxy-headers`; restrict `FORWARDED_ALLOW_IPS` to
  your proxy if it isn't the only way in.

### Checklist

1. A Postgres database; `DATABASE_URL` set (any of the accepted URL forms).
2. A persistent volume for `STORAGE_DIR`, mounted in **both** web and worker.
3. `APP_BASE_URL=https://…`, a long random `SESSION_SECRET`, Google OAuth client with the redirect URI
   `<APP_BASE_URL>/api/auth/callback`, `AUTH_DEV_LOGIN` unset.
4. LLM: `LLM_PROVIDER` / `LLM_MODEL` and either a reachable `OLLAMA_BASE_URL` or `GEMINI_API_KEY`.
5. At least one worker process running (`RUN_WORKER_IN_WEB=0` on web).
6. HTTPS in front (Fly.io, Render, Railway, Cloud Run, or a VM with Caddy/nginx).
7. Uptime checks on `/api/health` and `/api/health/worker`; optionally `SENTRY_DSN` / `VITE_SENTRY_DSN`.
8. `ADMIN_EMAILS` for the operators; review `legal/terms.md` and `legal/privacy.md`.
9. Backups of the database and the uploads volume.

## Plans and limits

Limits are defined per plan in `src/planyourshot/web/limits.py` (`limits_for(user)`). Every account is on
`free`; `pro` exists only as configuration, so a paid plan can later be switched on by setting `users.plan`.
There is no payment or upgrade flow — over a limit the API answers with a message stating the limit and when
it resets.

| Limit | Free | Pro | Override (`FREE_…` / `PRO_…`) |
| --- | --- | --- | --- |
| Screenplays per account | 2 | 20 | `_MAX_SCRIPTS` |
| Characters per screenplay | 45,000 | 400,000 | `_MAX_SCRIPT_CHARS` |
| Assistant messages (chat, edit, shot chat) per rolling 24 h | 15 | 200 | `_MESSAGES_PER_DAY` |
| Full shot-list generations per rolling 30 days | 2 | 30 | `_FULL_GENERATIONS_PER_MONTH` |
| Single-scene generations per rolling 24 h | 5 | 50 | `_SCENE_GENERATIONS_PER_DAY` |
| Single-shot regenerations per rolling 24 h | 10 | 100 | `_SHOT_REGENERATIONS_PER_DAY` |
| Model requests per minute | 5 | 20 | `_REQUESTS_PER_MINUTE` |
| CSV export | off | on | `_CSV_EXPORT` (`1`/`0`) |

- **Metering** — every model call writes a row to `usage_events` (kind, status, input/output characters,
  duration). A full shot-list generation writes one `shot_generate_full` event (what the monthly quota counts)
  plus one `shot_generate_scene` event per scene for cost tracking; those scene events are linked to the run
  and don't count against the single-scene quota. Quotas count `ok` and `refused` events; failed calls are
  free. A full run in which no scene succeeds is marked failed and gives the quota back.
- **Length** — checked after text extraction (the upload is marked failed with the reason) and on manual and
  applied AI edits. A screenplay that is already over the limit keeps its data and can still be edited, as long
  as the edit doesn't make it longer.
- **Responses** — over a quota or the per-minute limit: `429` with a `Retry-After` header. A feature the plan
  doesn't include: `403`. A disabled account (`users.disabled`): `403` on every signed-in route.
- **Concurrency** — one model call in flight per user (a second request gets `429` "The assistant is still
  working on your last request."), and `MAX_CONCURRENT_LLM_CALLS` across everyone and every process (expiring
  database leases). Shot-list generation takes
  a slot per scene, so a long generation doesn't starve other users. Full generations are capped at 150
  scenes per run.
- **Rate limits** on `/api/auth/*` (per IP) and uploads (per user) are stored in the database
  (`DatabaseRateLimiter` behind the `RateLimiter` interface in `web/ratelimit.py`), so every process shares them;
  a Redis implementation can be swapped in there. Behind a reverse proxy, run uvicorn with `--proxy-headers`
  so the client IP is real.
- **Staying on topic** — discuss, edit and shot-revision replies are structured output with an `on_topic`
  flag. When it is false the server discards the model's reply (and any edits or shot operations) and answers
  "I can only help with this screenplay and its shot list." Refused exchanges are stored, marked, and left out
  of the history sent to the model. The system prompt also treats the screenplay and user messages as content,
  never instructions.
- `GET /api/me/usage` returns the plan, every limit, and current counts. The UI uses it for the screenplay
  count, to hide CSV export, and for a quiet "12 of 15 messages today" note once usage passes 60%.

To try the Pro limits locally: `UPDATE users SET plan = 'pro' WHERE email = '…';`

## How it works

- **Processing** — the upload is validated (extension, signature, size), stored under a server-generated name
  in `STORAGE_DIR/<user>/`, and text is extracted by a background job (pypdf / python-docx). Scanned PDFs with
  no text layer fail with a clear message. Scenes are split deterministically from sluglines.
- **Context** — the model receives the full current screenplay when it fits; scripts beyond `MAX_CONTEXT_CHARS`
  (derived from `OLLAMA_NUM_CTX` for Ollama, about 43,000 characters at the default) fall back to a scene
  outline plus the scenes most relevant to the question.
- **Edits** — the model returns `{find, replace, reason}` operations. Each `find` is verified against the
  current text before it is shown; applying creates a new revision. Suggestions that no longer match (the text
  changed since) are refused rather than guessed.
- **Isolation** — every script route resolves the script through the signed-in user; another user's id returns 404.

## API

Interactive docs at `/api/docs` when the server is running.
