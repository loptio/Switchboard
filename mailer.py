"""Email push — Unit 3: deliver a digest by SMTP.

`send_digest(digest)` renders the digest as a multipart text+HTML email and sends
it via SMTP. Configuration is entirely from environment variables (never code or
Git); see .env.example. The signature and the runner's graceful-degradation
wrapper are unchanged from Unit 2:

  - not configured at all   -> log.info + skip (no email, no error)
  - partially configured    -> log.warning (likely an env typo) + skip
  - configured but send fails -> raise (the runner logs it; the Run still
                                 succeeds and the Output is still saved)

The email body is rendered here from the Digest (a different medium than the
on-disk markdown file); it is not a re-implementation of output.render_markdown,
which the single-argument send_digest signature could not feed anyway.
"""

from __future__ import annotations

import html
import logging
import os
import smtplib
from dataclasses import dataclass
from datetime import date
from email.message import EmailMessage
from pathlib import Path

from dotenv import load_dotenv

from agent import Digest

# Load ONLY this project's .env (same rule as config.py / db.settings).
load_dotenv(Path(__file__).parent / ".env")

log = logging.getLogger(__name__)

SMTP_TIMEOUT_SECONDS = 10  # don't let a hung server drag the whole run down
_DEFAULT_PORT = 587
_SSL_PORT = 465

# Every recognized SMTP_* key, and the subset required to actually send.
_ALL_KEYS = (
    "SMTP_HOST", "SMTP_PORT", "SMTP_USERNAME", "SMTP_PASSWORD",
    "SMTP_FROM", "SMTP_TO", "SMTP_SUBJECT",
)
_REQUIRED_KEYS = ("SMTP_HOST", "SMTP_USERNAME", "SMTP_PASSWORD", "SMTP_TO")


@dataclass(frozen=True)
class _SmtpConfig:
    host: str
    port: int
    username: str
    password: str
    sender: str
    recipients: list[str]
    subject: str | None


def _env(key: str) -> str:
    return (os.getenv(key) or "").strip()


def _present(key: str) -> bool:
    return bool(_env(key))


def _config_from_env() -> _SmtpConfig:
    """Build the config (all required keys are present by the time this runs)."""
    port_raw = _env("SMTP_PORT")
    try:
        port = int(port_raw) if port_raw else _DEFAULT_PORT
    except ValueError:
        raise RuntimeError(f"SMTP_PORT must be an integer, got {port_raw!r}")
    username = _env("SMTP_USERNAME")
    recipients = [r.strip() for r in _env("SMTP_TO").split(",") if r.strip()]
    return _SmtpConfig(
        host=_env("SMTP_HOST"),
        port=port,
        username=username,
        password=_env("SMTP_PASSWORD"),
        sender=_env("SMTP_FROM") or username,
        recipients=recipients,
        subject=_env("SMTP_SUBJECT") or None,
    )


def _collapse(text: str) -> str:
    """Collapse whitespace/newlines so a value stays on one line."""
    return " ".join((text or "").split())


def _render_text(digest: Digest, day: date) -> str:
    lines = [f"News Digest — {day.isoformat()}", ""]
    if not digest.items:
        lines.append("(no items)")
    for i, item in enumerate(digest.items, start=1):
        lines.append(f"{i}. {_collapse(item.title)}")
        lines.append(f"   {_collapse(item.one_line_summary)}")
        link = _collapse(item.link)
        lines.append(f"   {link}" if link else "   (no link)")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _render_html(digest: Digest, day: date) -> str:
    heading = html.escape(f"News Digest — {day.isoformat()}")
    if not digest.items:
        return f"<h2>{heading}</h2>\n<p>(no items)</p>\n"
    rows = []
    for item in digest.items:
        title = html.escape(_collapse(item.title))
        summary = html.escape(_collapse(item.one_line_summary))
        link = _collapse(item.link)
        if link:
            href = html.escape(link, quote=True)  # escape URL too (dirty feeds)
            title_html = f'<a href="{href}">{title}</a>'
        else:
            title_html = title
        rows.append(f"  <li><strong>{title_html}</strong><br>{summary}</li>")
    return f"<h2>{heading}</h2>\n<ol>\n" + "\n".join(rows) + "\n</ol>\n"


def _build_message(digest: Digest, cfg: _SmtpConfig, day: date) -> EmailMessage:
    msg = EmailMessage()
    msg["Subject"] = cfg.subject or f"News Digest — {day.isoformat()}"
    msg["From"] = cfg.sender
    msg["To"] = ", ".join(cfg.recipients)
    msg.set_content(_render_text(digest, day))
    msg.add_alternative(_render_html(digest, day), subtype="html")
    return msg


def _send(msg: EmailMessage, cfg: _SmtpConfig) -> None:
    if cfg.port == _SSL_PORT:
        server = smtplib.SMTP_SSL(cfg.host, cfg.port, timeout=SMTP_TIMEOUT_SECONDS)
    else:
        server = smtplib.SMTP(cfg.host, cfg.port, timeout=SMTP_TIMEOUT_SECONDS)
    with server:
        if cfg.port != _SSL_PORT:
            server.starttls()
        server.login(cfg.username, cfg.password)
        server.send_message(msg)


def send_digest(digest: Digest) -> None:
    """Deliver a digest by email. See module docstring for the config contract."""
    missing = [k for k in _REQUIRED_KEYS if not _present(k)]
    if missing:
        if any(_present(k) for k in _ALL_KEYS):
            # Some SMTP_* vars are set but a required one is missing — almost
            # always an env typo. Be loud rather than silently dropping email.
            log.warning(
                "SMTP partially configured; missing %s — skipping email "
                "(check your .env for typos)",
                ", ".join(missing),
            )
        else:
            log.info("SMTP not configured; skipping email delivery")
        return

    cfg = _config_from_env()
    msg = _build_message(digest, cfg, date.today())
    _send(msg, cfg)
    log.info("sent digest email to %s", ", ".join(cfg.recipients))
