# News Digest (Phase 1)

One command = fetch one RSS feed → summarize it with a Claude agent → write a
local markdown digest and print it to the console.

This is **Phase 1** of a larger agent system: a single, sequential vertical
slice. No database, scheduling, notifications, web UI, or multi-provider models
— those are later phases by design.

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

The fetch module has a basic offline test (parses a sample feed into items):

```bash
.venv/bin/python -m pytest
```
