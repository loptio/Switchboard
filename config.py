"""Configuration — all knobs come from env vars (with defaults), never hardcoded.

Loaded from a local .env if present (see .env.example). No secrets live here:
auth is handled by the Claude Code CLI subscription, not an API key.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()  # populate os.environ from .env if it exists

DEFAULT_FEED_URL = "https://hnrss.org/frontpage"
DEFAULT_COUNT = 10
DEFAULT_OUTPUT_DIR = "output"
DEFAULT_MODEL = "claude-opus-4-8"


@dataclass(frozen=True)
class Config:
    feed_url: str
    count: int
    output_dir: Path
    model: str


def load_config() -> Config:
    return Config(
        feed_url=os.getenv("FEED_URL", DEFAULT_FEED_URL),
        count=int(os.getenv("DIGEST_COUNT", str(DEFAULT_COUNT))),
        output_dir=Path(os.getenv("OUTPUT_DIR", DEFAULT_OUTPUT_DIR)),
        model=os.getenv("MODEL", DEFAULT_MODEL),
    )
