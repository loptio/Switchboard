# Mac-as-server (deploy/)

Run Switchboard as always-on launchd **user agents** — "踢一脚走人、从手机看".
No sudo, no Docker for the app itself (PostgreSQL stays in its own container).

```bash
deploy/install.sh      # install/reload both services (idempotent)
deploy/uninstall.sh    # stop + remove both
```

| Service | What | Where |
| --- | --- | --- |
| `com.switchboard.worker` | `cli.py scheduler` — cron schedules + pending-run/resume drain, wrapped in `caffeinate -s` | logs → `~/Library/Logs/switchboard/worker.log` |
| `com.switchboard.api` | uvicorn `api.app:app` | `http://127.0.0.1:8400` (loopback only) |

Both are `KeepAlive` (relaunch on crash) and `RunAtLoad` (start at login). They run
in your GUI session, so the Claude CLI's **subscription auth** (~/.claude + Keychain)
keeps working — never set `ANTHROPIC_API_KEY`. `.env` is read from the project dir
(config.py); `PATH` includes homebrew so the worker can spawn the `claude` CLI.

## Prerequisites

- The PostgreSQL container is up (`docker start agent-pg`) — note Docker Desktop
  itself must be set to start at login, or the worker will log DB errors until it is.
- `claude` CLI logged in (subscription), `.env` filled in.

## Sleep

`caffeinate -s` keeps the Mac awake **while on AC power**. On battery, macOS may
still sleep (schedules then fire late — the scheduler catches up on wake; an
overdue schedule fires once, not N times). To also forbid battery sleep:
`sudo pmset -b sleep 0` (your call — it burns the battery).

## Remote access (phone / tablet)

Keep the API loopback-only. For remote use, install [Tailscale](https://tailscale.com)
and serve over your tailnet:

```bash
tailscale serve --bg 8400          # exposes the API at https://<mac-name>.<tailnet>.ts.net
```

The React frontend is still dev-served when needed (`cd frontend && npm run dev`);
point it at the service port with `VITE_API_TARGET=http://localhost:8400`. Serving a
built frontend bundle from the API process is a future small item.

## Operate

```bash
launchctl print gui/$(id -u)/com.switchboard.worker | head -20   # status
tail -f ~/Library/Logs/switchboard/worker.log                     # live logs
launchctl kickstart -k gui/$(id -u)/com.switchboard.worker        # restart
```
