"""Configuration — all knobs come from env vars (with defaults), never hardcoded.

Loaded from a local .env if present (see .env.example). No secrets live here:
auth is handled by the Claude Code CLI subscription, not an API key.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

# Load ONLY this project's .env — not a parent dir's. load_dotenv() with no
# argument walks upward, which could pull in unrelated or sensitive vars from a
# parent .env (this repo sits under a dir whose .env holds other API keys, and
# this project must never use ANTHROPIC_API_KEY).
load_dotenv(Path(__file__).parent / ".env")

DEFAULT_FEED_URL = "https://hnrss.org/frontpage"
DEFAULT_COUNT = 10
DEFAULT_OUTPUT_DIR = "output"
DEFAULT_MODEL = "claude-opus-4-8"
# Language for model-GENERATED text (digest one_line_summary, brief summary +
# perspective takes). Provenance (title/link/source/domain) is never translated.
# Default Simplified Chinese; switch to e.g. "繁體中文" / "English" via OUTPUT_LANGUAGE.
DEFAULT_LANGUAGE = "简体中文"
# Coding family intake = a workspace directory + a task string. As of Phase 10b-1 a
# coding run carries its OWN task + workspace (Run.coding_task / Run.coding_workspace,
# fed per-run from the web/CLI); these Config values are the FALLBACK the worker uses
# when a Run leaves them unset, preserving the 10a global-task behaviour. The agent is
# confined to whichever directory is in effect.
DEFAULT_CODING_WORKSPACE = "coding_workspace"


@dataclass(frozen=True)
class Config:
    feed_url: str
    count: int
    output_dir: Path
    model: str
    output_language: str = DEFAULT_LANGUAGE
    coding_task: str = ""
    coding_workspace: Path = Path(DEFAULT_CODING_WORKSPACE)
    # Phase 10c: opt-in automatic coder↔reviewer dialogue (default off → pre-10c path).
    coding_auto_review: bool = False


def _bool_env(name: str, default: bool = False) -> bool:
    raw = (os.getenv(name) or "").strip().lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on")


def _positive_int(name: str, default: int) -> int:
    """Read a positive int from env; clear errors instead of raw tracebacks."""
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        raise ValueError(f"{name} must be an integer, got {raw!r}")
    if value < 1:
        raise ValueError(f"{name} must be >= 1, got {value}")
    return value


def load_config() -> Config:
    return Config(
        feed_url=os.getenv("FEED_URL", DEFAULT_FEED_URL),
        count=_positive_int("DIGEST_COUNT", DEFAULT_COUNT),
        output_dir=Path(os.getenv("OUTPUT_DIR", DEFAULT_OUTPUT_DIR)),
        model=os.getenv("MODEL", DEFAULT_MODEL),
        output_language=os.getenv("OUTPUT_LANGUAGE", DEFAULT_LANGUAGE),
        coding_task=os.getenv("CODING_TASK", ""),
        coding_workspace=Path(os.getenv("CODING_WORKSPACE", DEFAULT_CODING_WORKSPACE)),
        coding_auto_review=_bool_env("CODING_AUTO_REVIEW", False),
    )
