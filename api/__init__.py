"""Control-plane API package (Phase 3, Unit 1).

A FastAPI app that exposes a logged-in REST control plane over the existing `db`
data layer. CONTRACT: this package imports ONLY `db` (and stdlib/3rd-party web
libs) — never `runner`/`agent`/`scheduler`/`mailer`/`fetch` — so the Claude
Agent SDK never loads into the web process. Manual triggers are handed to the
worker via the DB (a pending Run), never executed here. See README, "Phase 3".

The app lives in `api.app` (import it explicitly); this __init__ stays empty so
lightweight modules like `api.security` can be imported without pulling in
FastAPI (the operator CLI reuses `api.security` for password hashing).
"""
