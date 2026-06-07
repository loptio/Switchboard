# News Digest (Phase 1)

One command = fetch one RSS feed → summarize it with a Claude agent → write a
local markdown digest and print it to the console.

This began as **Phase 1** of a larger agent system: a single, sequential
vertical slice (no database, scheduling, notifications, or web UI). **Phase 2 is
now in progress** — the data layer (Unit 1) adds a PostgreSQL source of truth
and the scheduler/runner (Unit 2) runs the workflow on schedule. Email delivery
(Unit 3), web UI, and multi-provider models are still upcoming.

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
and an **APScheduler** heartbeat that fires it on schedule. Email is a stubbed
call point until Unit 3 — a successful run currently just logs "email not wired
yet".

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

## Project docs & backlog

System design and phase plans live in [`docs/`](docs/):

- [架构与建造蓝图](docs/Agent系统·架构与建造蓝图_1.md) — the north-star
  architecture (single source of truth)
- [Phase 1 简报](docs/Phase1·任务简报-单agent新闻简报_1.md) ·
  [Phase 2 简报](docs/Phase2·任务简报-DB+调度+邮件推送.md) — per-phase task briefs

Deferred, tracked-but-not-now items are in [`BACKLOG.md`](BACKLOG.md).
