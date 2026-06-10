# Switchboard

A **self-hosted personal AI agent workflow platform**: run, schedule, monitor,
and human-review multi-agent workflows from a web control plane, with a
PostgreSQL source of truth and a worker that executes LangGraph-compiled
workflow definitions.

> Naming: the project is **Switchboard**; the Python package/folder is still
> `news_digest` (its Phase 1 origin — rename is backlogged). The architecture
> north star and per-phase status live in
> [`docs/Switchboard·架构与建造蓝图_v2.md`](docs/Switchboard·架构与建造蓝图_v2.md);
> each phase also has a task brief under [`docs/`](docs/). **Status is tracked
> there, not here.**

## Architecture (three planes)

```
you (any device)
   │  login (session cookie + CSRF)
   ▼
Control plane   React + Vite UI  ──REST/OpenAPI──▶  FastAPI backend
   │  reads/writes definitions, schedules, statuses, outputs   (never runs agents)
   ▼
Data plane      PostgreSQL — runs / outputs / schedules / users /
                workflow_defs / agent_defs (+ LangGraph checkpoints, same DB)
   ▲
   │  claims pending runs, writes back status + outputs
Worker plane    APScheduler worker → runner → generic orchestrator
                (workflow defs compiled to LangGraph graphs)
                ├─ model calls via the llm.py seam (tools=[], Claude Agent SDK)
                └─ coding runs via the coding_agent.py seam (real tools, sandboxed)
```

The web app never executes agents: it writes definitions/schedules into the DB;
the worker claims pending runs and writes results back. That decoupling is the
system's spine.

## What it does today

- **Workflows as data** — `WorkflowDef`/`AgentDef` live in the DB (code built-ins
  as fallback), validated by a two-guard layer, edited from the web synthesizer
  UI. A generic compiler turns a definition into a LangGraph `StateGraph`.
- **Three real workflow families**:
  - `digest` — single-feed news digest (summarizer + verifier with bounded redo).
  - `brief` — multi-source RSS → filter → summary + three perspectives → assembly
    (output language configurable, default Simplified Chinese).
  - `coding` — a commandable coding agent: per-run task + workspace (a git repo),
    Read/Write/Edit/**Bash** inside an OS sandbox (Seatbelt: filesystem confined
    to the workspace, network denied), command audit trail, git-aware diff,
    `.git` integrity guard, and worker secrets scrubbed from the agent's
    environment.
- **Human-in-the-loop** — runs can pause for review (`awaiting_input`) and resume
  from a checkpoint after approve/redo, from the CLI or the web UI. Coding runs
  show the diff + the commands that ran before you approve.
- **Scheduling + email** — cron schedules drive runs; digests can be delivered
  by SMTP.

## Requirements

- Python ≥ 3.10, Node.js (for the frontend and the Claude Code CLI)
- PostgreSQL 16 (Docker is fine)
- The Claude Code CLI: `npm install -g @anthropic-ai/claude-code`

### Authentication — subscription, not API key

Agents authenticate through your **Claude subscription** via the CLI. Log in
once with `claude` → `/login`.

> ⚠️ **Do NOT set `ANTHROPIC_API_KEY`.** That would route through paid API
> billing instead of your subscription. This project never reads an API key.

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

cp .env.example .env                          # then fill in values
.venv/bin/python -m alembic upgrade head      # create the tables
.venv/bin/python cli.py checkpointer-setup    # LangGraph checkpoint tables (once)
.venv/bin/python cli.py create-user --username you   # control-plane login

cd frontend && npm install                    # frontend deps
```

Configuration is env-vars only (`.env`, gitignored — secrets never live in code
or git). See [.env.example](.env.example) for the full list: feed/model/output
knobs, `DATABASE_URL`, `SECRET_KEY` (session signing), SMTP delivery, and the
coding workflow's `CODING_TASK`/`CODING_WORKSPACE` defaults.

## Run

```bash
# One-shot workflows (manual trigger)
.venv/bin/python cli.py run-once --workflow digest          # or brief
.venv/bin/python cli.py run-once --workflow digest --review # pause for approval
.venv/bin/python cli.py resume-run <run-id> --decision approve|redo [--feedback ...]
.venv/bin/python cli.py run-once --workflow coding --task "..." --workspace /path/to/repo

# The long-running pieces
.venv/bin/python cli.py scheduler              # worker: schedules + pending runs
.venv/bin/uvicorn api.app:app                  # API on :8000 (/docs = OpenAPI)
cd frontend && npm run dev                     # UI on :5173 (proxies to :8000)

# Schedules
.venv/bin/python cli.py add-schedule --cron "0 6 * * *" --tz UTC --workflow brief
.venv/bin/python cli.py list-schedules
.venv/bin/python cli.py list-runs
```

### Run as a service (macOS)

`deploy/install.sh` installs the worker + API as always-on launchd user agents
(no sudo; survives reboots; `caffeinate` keeps the Mac awake on AC). See
[deploy/README.md](deploy/README.md) for sleep caveats and Tailscale remote access.

## Tests

Everything is offline and deterministic — no network, no real model calls, no
PostgreSQL needed (in-memory SQLite for the data layer; an injectable fake for
the agent seams):

```bash
.venv/bin/python -m pytest        # backend suite
cd frontend && npm test           # frontend (vitest)
```

To run the same backend suite against a real PostgreSQL for dialect fidelity,
set `TEST_DATABASE_URL` to a throwaway database.

## Security model (coding workflow)

Layered, in order of defense:

1. **OS sandbox** (borrowed: the Claude Code CLI's Seatbelt/sandbox-exec) —
   filesystem confined to the per-run workspace, network denied, per-command
   timeouts. This is also the primary `.git` write defense.
2. **Worker-side guards** — workspace confinement checks on file tools, a
   `.git` integrity snapshot/diff/restore (version-independent backstop; a
   tampered run is neutralized and refused), and a clean-tree precondition.
3. **Secret hygiene** — worker secrets (key/token/password-shaped env vars and
   `DATABASE_URL`) are scrubbed from the agent subprocess environment for the
   duration of the run; subscription auth is unaffected.
4. **Human review** — coding runs can pause for diff + command review before
   their changes are accepted; rejected runs are restored via git.

## Repository layout

| Area | Files |
| --- | --- |
| Workflow engine | `engine.py`, `engine_fanout.py`, `workflows.py`, `agentdefs.py`, `components.py`, `manifest.py`, `defs_validate.py`, `defs_resolve.py` |
| Agent seams | `llm.py` (tools=[] model calls), `coding_agent.py` (the only agent-loop SDK caller) |
| Orchestrators | `orchestrator.py` (digest), `brief_orchestrator.py`, `coding_orchestrator.py` |
| Worker | `scheduler.py`, `runner.py`, `checkpoint.py`, `workspace.py`, `mailer.py` |
| Data | `db/` (only place SQL lives), `alembic/` migrations |
| Control plane | `api/` (FastAPI), `frontend/` (React + Vite) |
| Workflow IO | `fetch.py`, `sources.py`, `output.py` |
| Entry points | `cli.py`, `main.py` (legacy Phase 1 path) |

## Project history

Built incrementally (Phase 0 → 10) with per-phase task briefs in `docs/`; each
phase kept the previous tests byte-for-byte green and gated merges on hands-on
end-to-end verification. The blueprint records decisions and current phase
status.
