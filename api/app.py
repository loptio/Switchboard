"""FastAPI app factory for the control-plane API.

CONTRACT: imports only `db` + web libs — never runner/agent/scheduler — so the
Claude Agent SDK never loads here (a test enforces this). Manual triggers are
handed to the worker via the DB; nothing in this process runs an agent.

Run it locally with either form:
    uvicorn api.app:app --reload                 # lazy `app`, builds from env
    uvicorn --factory api.app:create_app         # explicit factory
SECRET_KEY must be set in the environment for both (see .env.example).
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.sessions import SessionMiddleware

from api.routers import auth, runs
from api.settings import APISettings, load_settings


def create_app(settings: APISettings | None = None) -> FastAPI:
    """Build the app. Pass `settings` in tests; otherwise it loads from env."""
    settings = settings or load_settings()
    app = FastAPI(title="Agent Control Plane", version="0.1.0")
    app.state.settings = settings

    # Session cookie: signed (itsdangerous), HttpOnly (always, by Starlette),
    # SameSite=Lax, Secure in prod, and a bounded max_age (sliding idle timeout).
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.secret_key,
        session_cookie=settings.session_cookie,
        max_age=settings.session_max_age,
        same_site="lax",
        https_only=settings.cookie_secure,
    )
    # CORS only matters for a browser SPA on another origin (Unit 2). Credentialed
    # requests require explicit origins (never "*"); empty => no CORS headers.
    if settings.cors_allow_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(settings.cors_allow_origins),
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    @app.get("/healthz", tags=["meta"])
    def healthz() -> dict:
        return {"status": "ok"}

    app.include_router(auth.router)
    app.include_router(runs.router)
    return app


def __getattr__(name: str):
    # PEP 562: build `app` lazily so `uvicorn api.app:app` works (reads env at
    # access time) while `from api.app import create_app` stays side-effect-free
    # for tests (which must not require SECRET_KEY just to import).
    if name == "app":
        return create_app()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
