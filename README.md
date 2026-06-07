# News Digest (Phase 1)

One command = fetch one RSS feed → summarize it with a Claude agent → write a
local markdown digest and print it to the console.

This began as **Phase 1** of a larger agent system: a single, sequential
vertical slice (no database, scheduling, notifications, or web UI). **Phase 2 is
now in progress** — the data layer (Unit 1) adds a PostgreSQL source of truth
and the scheduler/runner (Unit 2) runs the workflow on schedule, and email
(Unit 3) pushes the digest by SMTP. Web UI and multi-provider models are still
upcoming.

## How it works

```
fetch (feedparser)  →  agent (Claude Agent SDK)  →  output (markdown + console)
        │                       │                          │
   feed URL → items      items → digest            digest → output/digest-YYYY-MM-DD.md
```

Three modules with clear boundaries (plus an entry point):

| Module        | Responsibility                                              |
| ------------- | ----------------------------------------------------------- |
| `fetch.py`    | Fetch/parse the RSS feed. Pure code (feedparser), no agent. |
| `agent.py`    | Summarize items via the Claude Agent SDK. Only Claude caller. |
| `output.py`   | Render markdown, write the file, print to console.          |
| `main.py`     | Wire `fetch → agent → output`.                               |
| `config.py`   | Read all knobs from env vars (with defaults).                |

## Requirements

- **Python ≥ 3.10**
- **Node.js** + the **Claude Code CLI** (`@anthropic-ai/claude-code`) — the
  Agent SDK delegates to it.

## Setup

```bash
# 1. From the project directory, create a virtualenv and install deps
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# 2. Install the Claude Code CLI (once, globally)
npm install -g @anthropic-ai/claude-code
```

### Authentication — subscription, not API key

The agent authenticates through your **Claude subscription** via the CLI. Log in
once:

```bash
claude        # then run /login and follow the browser flow
```

> ⚠️ **Do NOT set `ANTHROPIC_API_KEY`.** That would route through paid API
> billing instead of your subscription. This project never reads an API key.
> (If you have `ANTHROPIC_BASE_URL` exported, it's harmless — subscription auth
> still applies.)

## Configuration

Everything is configurable via env vars (copy `.env.example` to `.env`):

| Variable       | Default                          | Meaning                          |
| -------------- | -------------------------------- | -------------------------------- |
| `FEED_URL`     | `https://hnrss.org/frontpage`    | The single RSS source            |
| `DIGEST_COUNT` | `10`                             | How many latest items to include |
| `OUTPUT_DIR`   | `output`                         | Where digests are written        |
| `MODEL`        | `claude-opus-4-8`                | Claude model the agent uses      |

```bash
cp .env.example .env   # then edit as you like
```

To save subscription quota, set e.g. `MODEL=claude-sonnet-4-6`.

## Run

```bash
.venv/bin/python main.py
```

This fetches the feed, summarizes the top `DIGEST_COUNT` items, prints the
digest, and writes it to `output/digest-YYYY-MM-DD.md` (today's date).

A format example is checked in at [`output/sample-digest.md`](output/sample-digest.md).

## Test

Offline tests (no network, no agent/API): the fetch module parses a sample feed
into items, plus robustness helpers (config parsing, agent JSON validation,
markdown sanitization).

```bash
.venv/bin/python -m pytest
```

## Known limitations (Phase 1)

Intentionally minimal per the Phase 1 scope:
- One feed, run on demand — no scheduling, storage, or notifications.
- Item titles/summaries are sanitized for whitespace/newlines and bold, but not
  fully Markdown-escaped (public feeds rarely need it; over-escaping hurts
  readability of titles like `Show HN: foo [pdf]`).
- If the agent returns fewer items than requested, the digest reflects what was
  returned (graceful) rather than failing the whole run.

## Phase 2 — Database foundation (Unit 1)

Phase 2 makes the database the system's source of truth. **Unit 1 (this slice)**
adds the schema and a **data-access layer** — the only place the rest of the
system touches the DB. The scheduler (Unit 2) and email push (Unit 3) build on
this contract and are **not** part of Unit 1.

### Tables

| Table       | Purpose                                                              |
| ----------- | ------------------------------------------------------------------- |
| `runs`      | one execution of a workflow (status, trigger, timing, error)        |
| `outputs`   | an artifact of a run — the rendered digest (+ optional structured data) |
| `schedules` | a declarative cron schedule for a workflow                          |

All access goes through the `db` package — e.g. `from db import create_run,
save_output, update_run_status, list_due_schedules`. Callers get plain
dataclasses (`Run`, `Output`, `Schedule`); SQL never leaks out of `db/`.

### Database setup

Unit 1 targets **PostgreSQL** at runtime. Credentials come from the environment
only (never code/Git):

```bash
# In your .env (copy from .env.example) or your shell:
export DATABASE_URL='postgresql+psycopg://user:password@localhost:5432/agent'
```

Install deps (now includes the DB stack) and create the schema with migrations:

```bash
.venv/bin/pip install -r requirements.txt   # adds SQLAlchemy, alembic, psycopg
.venv/bin/python -m alembic upgrade head     # creates the tables
```

`alembic/env.py` reads `DATABASE_URL` from the environment — the URL is never
stored in `alembic.ini`.

### Tests (offline)

The data-layer tests run fully offline against in-memory SQLite — no PostgreSQL,
no network — so `pytest` works anywhere (same style as Phase 1):

```bash
.venv/bin/python -m pytest
```

To run the **same** suite against real PostgreSQL (dialect fidelity), point it
at a throwaway database:

```bash
export TEST_DATABASE_URL='postgresql+psycopg://user:password@localhost:5432/agent_test'
.venv/bin/python -m pytest tests/test_db.py
```

### Scope / notes (Unit 1)

- No scheduler or email yet — those are Units 2 and 3; Unit 1 is the DB contract
  they depend on. (An always-on local process and the "your computer must be on"
  caveat arrive with the scheduler; cloud hosting is Phase 3.)
- The data layer stores `next_run_at` for schedules but does not compute it from
  cron — that belongs to the scheduler (Unit 2). `list_due_schedules(now)`
  returns enabled schedules due by `next_run_at` (a NULL counts as due).
- Timestamps are stored and returned as UTC. SQLite drops timezones, so the
  layer normalizes every datetime to UTC before writing/comparing — pass
  timezone-aware UTC datetimes.

## Phase 2 — Scheduler & runner (Unit 2)

Unit 2 adds the **runner** (one full run of the workflow through the data layer)
and an **APScheduler** heartbeat that fires it on schedule. Email delivery is
implemented in Unit 3 (below): a run sends the digest by SMTP if configured, and
degrades gracefully otherwise.

```
fetch → summarize → render → write local file (Phase 1, kept)
                          → save Output + record Run (data layer)
                          → send_digest(...)   ← Unit 3 implements SMTP
```

- A run records a `Run` (pending → running → success/failed) and saves the digest
  as an `Output`. Pipeline failures are recorded as `failed` rather than crashing.
  The Phase 1 local markdown file is still written.
- The scheduler ticks every 60s and runs schedules whose `next_run_at` has
  arrived, then advances `next_run_at` to the next cron fire. The DB is the
  source of truth — add/remove schedules without restarting.

### Operator CLI (thin wiring)

```bash
.venv/bin/python cli.py run-once                          # run now (manual trigger)
.venv/bin/python cli.py add-schedule --cron "0 6 * * *"   # daily 06:00 UTC (--tz to change)
.venv/bin/python cli.py list-schedules
.venv/bin/python cli.py list-runs
.venv/bin/python cli.py scheduler                         # start the long-running heartbeat
```

`run-once` and `scheduler` need `DATABASE_URL` set and the agent authenticated
(Phase 1 — subscription, never `ANTHROPIC_API_KEY`).

### Running on a schedule (always-on)

`cli.py scheduler` is a **long-running local process**: your computer must stay
on (and awake) for scheduled runs to fire. That's expected for Phase 2 — cloud
hosting is Phase 3. If the process is off across one or more scheduled times, it
**catches up exactly once** on restart and advances to the next future fire (you
get one digest, not a backlog of N).

### Tests

Still fully offline — the Phase 1 pipeline, the clock, and SMTP are all
mocked/injected, so the scheduler is tested with mock time and never waits:

```bash
.venv/bin/python -m pytest
```

A pinned-SDK smoke test (`tests/test_sdk_smoke.py`) guards the `tools=[]`
one-shot contract against an accidental SDK upgrade (the Phase 1 regression).

## Phase 2 — Email push (Unit 3)

Unit 3 replaces the stub with a real SMTP sender: `send_digest(digest)` renders
the digest as a **multipart text + HTML** email and sends it. The runner calls it
after the digest is saved, so email never affects whether a run succeeds:

- **Not configured** (no `SMTP_*` set) → logs and skips; the digest is still
  saved. Email is opt-in.
- **Partially configured** (some `SMTP_*` set but a required one missing) → a
  loud `WARNING` (likely an env typo), then skips.
- **Configured but the send fails** → the runner logs it and the run still
  succeeds with its Output saved (graceful degradation).

### Configure SMTP (env only)

Set these in your `.env` (see `.env.example`). Credentials never go in code/Git.
For **Gmail**, create an **app-specific password** (not your account password).

| Variable        | Required | Default                 | Meaning                             |
| --------------- | -------- | ----------------------- | ----------------------------------- |
| `SMTP_HOST`     | ✅       | —                       | SMTP server, e.g. `smtp.gmail.com`  |
| `SMTP_PORT`     |          | `587`                   | `465` = implicit SSL; else STARTTLS |
| `SMTP_USERNAME` | ✅       | —                       | login user (Gmail: your address)    |
| `SMTP_PASSWORD` | ✅       | —                       | app-specific password               |
| `SMTP_TO`       | ✅       | —                       | recipient(s), comma-separated       |
| `SMTP_FROM`     |          | = `SMTP_USERNAME`       | sender address                      |
| `SMTP_SUBJECT`  |          | `News Digest — <today>` | subject override                    |

Then a run delivers the digest:

```bash
.venv/bin/python cli.py run-once     # fetch → summarize → save → email
```

The SMTP connection uses a 10s timeout so a hung server can't stall a run. Tests
stay offline (mock SMTP — no real connection or send):

```bash
.venv/bin/python -m pytest
```

## Phase 3 — Control-plane API (Unit 1)

Phase 3 adds the **control plane**: a logged-in REST API (FastAPI) over the
existing `db` layer to view runs/outputs, manage schedules, and trigger a run
(a browser UI is Unit 2). It runs as its **own process**, separate from the
worker, sharing only the database.

**The spine — control plane ↔ worker, through the DB (never a direct call):** the
web process imports **only `db`**; it never imports `runner`/`agent`/`scheduler`,
so the Claude Agent SDK never loads into the web tier (a test enforces this). A
manual trigger therefore cannot run the agent inside the request — instead:

```
POST /runs ─▶ web writes a *pending* Run (db.create_run) ─▶ 202 returned immediately
                                  │  (pending row in the DB)
                                  ▼
       scheduler heartbeat tick ─▶ claims it (atomic) ─▶ runs the pipeline ─▶ success + output
```

So **the worker (`cli.py scheduler`) must be running** for a manual trigger to
actually execute; the API only records intent. Scheduled runs are unchanged from
Phase 2.

### Endpoints (the OpenAPI contract)

Everything except `POST /auth/login` requires login; state-changing requests also
require the CSRF header (below). Browse the live contract at `/docs`.

| Method & path            | Purpose                                              |
| ------------------------ | ---------------------------------------------------- |
| `POST /auth/login`       | username + password → session cookie                 |
| `POST /auth/logout`      | clear the session                                    |
| `GET  /auth/me`          | current login state                                  |
| `GET  /runs`             | recent runs (filters: `status`, `workflow`, `limit`) |
| `GET  /runs/{id}`        | one run                                              |
| `GET  /runs/{id}/output` | a run's outputs (the digest)                         |
| `POST /runs`             | **manual trigger** → enqueue a pending run (202)     |
| `GET  /schedules`        | list schedules                                       |
| `POST /schedules`        | create (validates cron, primes `next_run_at`)        |
| `PATCH /schedules/{id}`  | enable/disable or change cron/tz                     |
| `DELETE /schedules/{id}` | delete                                               |

A running scheduler picks up schedule changes on its next tick — no restart (it
reads schedules from the DB each tick).

### Auth (session cookie + CSRF)

- **Password**: bcrypt via **passlib** — only the hash is stored, hashing is never
  hand-rolled. Set it with the CLI (below); plaintext never touches argv/Git.
- **Session**: a **signed** (itsdangerous), **HttpOnly**, **SameSite=Lax** cookie
  (Starlette `SessionMiddleware`) carrying the user id and a CSRF token. Add
  `Secure` in production via `COOKIE_SECURE=true`. It has a bounded lifetime
  (`SESSION_MAX_AGE`, default 12h, sliding) — the login expires; it is never
  permanent.
- **CSRF**: SameSite=Lax blocks the cookie on most cross-site writes; on top of
  that, every `POST`/`PATCH`/`DELETE` must echo the CSRF token in the
  **`X-CSRF-Token`** header. Login mirrors the token into a JS-readable
  `csrftoken` cookie for the SPA to read and send back; the server compares it
  (constant-time) against the authoritative copy in the signed session.

### Configuration (env only)

| Variable             | Required | Default       | Meaning                                     |
| -------------------- | -------- | ------------- | ------------------------------------------- |
| `SECRET_KEY`         | ✅       | —             | signs the session cookie (the one secret)   |
| `COOKIE_SECURE`      |          | `false`       | add `Secure` to cookies (set true on HTTPS) |
| `CORS_ALLOW_ORIGINS` |          | —             | comma-separated SPA origins (Unit 2)        |
| `SESSION_MAX_AGE`    |          | `43200` (12h) | session lifetime, in seconds                |

Generate a secret: `python -c "import secrets; print(secrets.token_urlsafe(48))"`.
The API also needs `DATABASE_URL` (same as Phase 2). `.env` is gitignored.

### Create the login user

```bash
.venv/bin/python -m alembic upgrade head               # adds the users table (migration 0002)
.venv/bin/python cli.py create-user --username admin   # prompts for the password
# later, to reset: cli.py set-password --username admin
```

### Run the API

```bash
.venv/bin/pip install -r requirements.txt    # adds FastAPI, uvicorn, passlib, …
export SECRET_KEY='…'                         # see above (or put it in .env)
.venv/bin/uvicorn api.app:app --reload        # http://127.0.0.1:8000  (/docs = OpenAPI)
```

For manual triggers to execute, also run the worker in another terminal:

```bash
.venv/bin/python cli.py scheduler             # claims pending runs + fires schedules
```

### Tests (offline)

Fully offline — FastAPI `TestClient` over in-memory SQLite, no network/SDK/SMTP:

```bash
.venv/bin/python -m pytest
```

A subprocess test (`tests/test_api_no_sdk.py`) enforces the spine: building the
web app must **not** import the Agent SDK.

### Scope / notes (Unit 1)

- **In**: FastAPI app, single-user auth, the endpoints above, the `users` table +
  migration, the manual-trigger handoff, offline tests. **Out**: the React
  frontend (Unit 2), cloud hosting, multi-user/roles.
- **Single worker, local.** The atomic claim is multi-worker-safe, but the only
  worker is `cli.py scheduler`. Avoid running `cli.py run-once` *while* the
  scheduler drains the same DB — `run-once` executes inline, so a concurrent
  claim could double-run that one row. Use `POST /runs` (the handoff) to trigger.
- Migrations target PostgreSQL (as in Phase 2); offline tests build the schema on
  SQLite via `metadata.create_all`. Run `alembic upgrade head` against real
  Postgres in your deploy/review environment.

## Phase 3 — Frontend (Unit 2)

A minimal, responsive **React control-plane UI** under [`frontend/`](frontend/)
that consumes the Unit 1 API over HTTP — it never touches backend code. Vite +
React + TypeScript; plain CSS Modules (no heavy component library).

**Pages:** login; a **runs dashboard** (recent runs with status badges, a **Run
now** button, and live status polling); **run detail** (the digest rendered as
markdown); and **schedules** (list / create / enable·disable / edit cron·tz /
delete).

### How it connects (cookies + CSRF)

The Vite dev server **proxies `/api` to the API** (`http://localhost:8000`), so
the browser only sees one origin and the session + `csrftoken` cookies are
same-origin — no CORS needed in dev. A single `apiFetch` wrapper sends
`credentials:"include"` on every call, echoes the `csrftoken` cookie in the
`X-CSRF-Token` header on writes, sends 401s back to the login page, and refreshes
the CSRF token via `/auth/me` and retries once on a 403.

### Run it (needs the API + worker running)

```bash
# 1. Backend API (terminal 1) and worker (terminal 2), per "Phase 3 — Unit 1":
uvicorn api.app:app                 # the API on :8000
python cli.py scheduler             # the worker — required for triggers to execute
#    (and create a login user once: python cli.py create-user --username admin)

# 2. Frontend (terminal 3):
cd frontend
npm install
cp .env.example .env                # defaults are fine for local
npm run dev                         # http://localhost:5173
```

Open http://localhost:5173, sign in, and click **Run now** — a pending run
appears and the dashboard polls until the worker finishes it (status → success),
then the digest is viewable. If a run stays pending for ~90s, the UI hints that
the worker may not be running.

### Config (Vite env)

| Variable          | Default                 | Meaning                                            |
| ----------------- | ----------------------- | -------------------------------------------------- |
| `VITE_API_BASE`   | `/api`                  | API base path (dev: proxied; prod: the API origin) |
| `VITE_API_TARGET` | `http://localhost:8000` | where `npm run dev` proxies `/api`                 |

For a deployed build, set `VITE_API_BASE` to the API's real origin (cross-origin;
the backend's `CORS_ALLOW_ORIGINS` must list the frontend origin).

### Tests / build

```bash
cd frontend
npm test            # Vitest + React Testing Library (offline; fetch mocked)
npm run build       # type-check + production build
```

The frontend does not touch the backend; the backend's test suite is unaffected.

## Phase 5 — Multi-agent orchestration (Unit 1)

Phase 5 upgrades the single "summarize" step into a **multi-agent subprocess**: a
deterministic **orchestrator** coordinates a **summarizer agent** and a
**verifier agent** that checks the digest **against the source items**, feeding a
critique back for a bounded number of redos. The orchestrator returns the **same
`Digest`** as before, so the runner and everything downstream (render / store /
email) are unchanged.

```
fetch → orchestrator.build_digest ─────────────────────────┐ → render → store → email
            │                                              │   (all unchanged)
            ├─ summarizer agent → Digest (strict-validated)│
            └─ verifier agent  → Critique (vs SOURCE) ──────┘
               pass → done · fail → feed back & redo (≤2) · cap → accept last + log
```

**The orchestrator is plain code, not an LLM deciding the flow** (a meta-agent is
a later phase) — so it's predictable, testable, and the SDK cost is bounded.

### The two agent contracts (the "glue")

| Agent             | Input                                   | Output (validated)                                   |
| ----------------- | --------------------------------------- | ---------------------------------------------------- |
| `summarize_agent` | source items (+ reviewer feedback on a redo) | `Digest` — `parse_digest`: exact count, non-empty summaries; **title/link taken verbatim from the source by position** (the model's echo isn't trusted, so fabricated links are impossible by construction) |
| `verify_agent`    | candidate `Digest` **+ the source items** | `Critique{passed, issues[]}` — `parse_critique`: `passed` a real bool; a failing review must carry ≥1 actionable issue |

Every agent reply is **validated against its schema**; a violation raises
`AgentContractError` and **dirty data never flows downstream**. Deterministic
checks (link/title real, item count) live in code; the verifier LLM does the one
thing code can't — judging whether each summary is **faithful to its source**.

### Control flow & bounded redo

- summarize → verify → **pass** ends the loop and returns the digest.
- **fail** (with issues) → the critique is fed back and the summarizer redoes,
  capped at **`max_redos=2`** (1 draft + 2 redos).
- **cap reached, still failing** → accept the **last** schema-valid digest and log
  the open issues (an LLM reviewer can be wrong or never satisfied; the cap is the
  backstop — never an infinite loop or unbounded spend).
- **verifier malformed** → re-verify the same digest once, then accept the current
  digest (degrades to summarizer-only quality; never masked as a pass).
- **summarizer never yields valid output** → the run fails (no digest is shipped).

Cost ceiling per run: ≤ `max_redos+1` summarizer calls and ≤ `(max_redos+1)×2`
verifier calls.

### The model seam (swap models without touching the orchestrator)

`llm.py` is now the **only** module that imports the Agent SDK — a single
`complete(prompt, *, system_prompt, model)`. Both agents call it (and take it as
an injectable parameter), so swapping models or adding routing later means editing
only `llm.py`; the orchestrator is untouched. The `agent.summarize` Phase 1 path
(`main.py`) still works, unchanged, through the same seam.

### Tests (offline)

Fully offline — the LLM is mocked at two layers: orchestrator tests inject fake
agents (scripted per attempt: pass, redo→pass, cap→accept-last, malformed verifier,
dirty summarizer) and assert call counts are **bounded**; agent tests inject a fake
`llm` and exercise the strict parsers. No network, no SDK, no API key.

```bash
.venv/bin/python -m pytest        # the whole suite, incl. tests/test_orchestrator.py + test_agents.py
```

The real end-to-end (real SDK, real review/redo) is the local acceptance step, as
in Phases 2/3: `.venv/bin/python cli.py run-once` and watch the run log.

## Project docs & backlog

System design and phase plans live in [`docs/`](docs/):

- [架构与建造蓝图](docs/Agent系统·架构与建造蓝图_1.md) — the north-star
  architecture (single source of truth)
- [Phase 1 简报](docs/Phase1·任务简报-单agent新闻简报_1.md) ·
  [Phase 2 简报](docs/Phase2·任务简报-DB+调度+邮件推送.md) ·
  [Phase 3 · Unit 1 简报](docs/Phase3·Unit1任务简报-后端API+认证.md) ·
  [Phase 3 · Unit 2 简报](docs/Phase3·Unit2任务简报-React前端.md) ·
  [Phase 5 · Unit 1 简报](docs/Phase5·Unit1任务简报-多agent编排.md) — per-phase task briefs

Deferred, tracked-but-not-now items are in [`BACKLOG.md`](BACKLOG.md).
