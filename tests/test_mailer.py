"""Offline tests for the mailer (mock SMTP — no real connection, no real send)."""

import logging
import smtplib

import pytest

import mailer
from agent import Digest, DigestItem
from brief_agent import Brief, BriefItem, Perspective

DIGEST = Digest(
    [
        DigestItem("Title A", "https://e/a", "Summary A"),
        # title/summary with HTML metacharacters, URL with an ampersand
        DigestItem("B & <C>", "https://e/b?q=1&x=2", "Sum <b>2</b>"),
    ]
)

BRIEF = Brief(
    date="2026-06-08",
    items=[
        BriefItem(
            "Title A", "https://e/a?q=1&x=2", "Src & Co", "科技", "Sum <b>1</b>",
            [Perspective("商业", "biz take"), Perspective("技术", "tech take")],
        )
    ],
)

_ALL_KEYS = (
    "SMTP_HOST", "SMTP_PORT", "SMTP_USERNAME", "SMTP_PASSWORD",
    "SMTP_FROM", "SMTP_TO", "SMTP_SUBJECT",
)


class FakeSMTP:
    """Records the SMTP conversation instead of making a connection."""

    last: "FakeSMTP | None" = None

    def __init__(self, host, port, timeout=None):
        self.host, self.port, self.timeout = host, port, timeout
        self.calls: list = []
        self.messages: list = []
        FakeSMTP.last = self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.calls.append("quit")
        return False

    def starttls(self):
        self.calls.append("starttls")

    def login(self, user, password):
        self.calls.append(("login", user, password))

    def send_message(self, msg):
        self.messages.append(msg)
        self.calls.append("send")


@pytest.fixture
def smtp_env(monkeypatch):
    """A complete, valid SMTP config (587/STARTTLS), SMTP_FROM/SUBJECT unset."""
    monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("SMTP_PORT", "587")
    monkeypatch.setenv("SMTP_USERNAME", "me@example.com")
    monkeypatch.setenv("SMTP_PASSWORD", "app-pw")
    monkeypatch.setenv("SMTP_TO", "you@example.com")
    monkeypatch.delenv("SMTP_FROM", raising=False)
    monkeypatch.delenv("SMTP_SUBJECT", raising=False)
    FakeSMTP.last = None


@pytest.fixture
def no_smtp_env(monkeypatch):
    for key in _ALL_KEYS:
        monkeypatch.delenv(key, raising=False)


def _block_connect(monkeypatch):
    """Make any attempt to connect fail the test loudly, and report if tried."""
    tried = []
    monkeypatch.setattr(smtplib, "SMTP", lambda *a, **k: tried.append("SMTP"))
    monkeypatch.setattr(smtplib, "SMTP_SSL", lambda *a, **k: tried.append("SMTP_SSL"))
    return tried


# --- sending ---------------------------------------------------------------

def test_sends_multipart_text_and_html(smtp_env, monkeypatch):
    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)

    mailer.send_digest(DIGEST)

    s = FakeSMTP.last
    assert s is not None
    assert (s.host, s.port) == ("smtp.example.com", 587)
    assert s.timeout == mailer.SMTP_TIMEOUT_SECONDS
    assert "starttls" in s.calls
    assert ("login", "me@example.com", "app-pw") in s.calls
    assert "send" in s.calls

    msg = s.messages[0]
    assert msg["To"] == "you@example.com"
    assert msg["From"] == "me@example.com"  # defaulted from username
    assert "News Digest" in msg["Subject"]
    assert msg.get_content_type() == "multipart/alternative"

    text = msg.get_body(preferencelist=("plain",)).get_content()
    assert "Title A" in text and "Summary A" in text and "https://e/a" in text

    html_body = msg.get_body(preferencelist=("html",)).get_content()
    assert "<a href=" in html_body and "Title A" in html_body


def test_html_escapes_text_and_href(smtp_env, monkeypatch):
    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)

    mailer.send_digest(DIGEST)

    html_body = FakeSMTP.last.messages[0].get_body(
        preferencelist=("html",)
    ).get_content()
    # title "B & <C>" must be escaped, not raw
    assert "&amp;" in html_body and "&lt;C&gt;" in html_body
    assert "<C>" not in html_body
    # the ampersand inside the href URL is escaped too
    assert "q=1&amp;x=2" in html_body
    assert "q=1&x=2" not in html_body


def test_custom_subject_and_from_and_recipients(smtp_env, monkeypatch):
    monkeypatch.setenv("SMTP_SUBJECT", "My Digest")
    monkeypatch.setenv("SMTP_FROM", "robot@example.com")
    monkeypatch.setenv("SMTP_TO", "a@x.com, b@y.com")
    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)

    mailer.send_digest(DIGEST)

    msg = FakeSMTP.last.messages[0]
    assert msg["Subject"] == "My Digest"
    assert msg["From"] == "robot@example.com"
    assert msg["To"] == "a@x.com, b@y.com"


def test_port_465_uses_ssl_without_starttls(smtp_env, monkeypatch):
    monkeypatch.setenv("SMTP_PORT", "465")
    monkeypatch.setattr(smtplib, "SMTP_SSL", FakeSMTP)
    plain = []
    monkeypatch.setattr(smtplib, "SMTP", lambda *a, **k: plain.append(1))

    mailer.send_digest(DIGEST)

    assert FakeSMTP.last is not None and FakeSMTP.last.port == 465
    assert "starttls" not in FakeSMTP.last.calls  # implicit SSL, no STARTTLS
    assert plain == []  # plain SMTP was not used


# --- skip / warn semantics -------------------------------------------------

def test_skips_silently_when_fully_unconfigured(no_smtp_env, monkeypatch, caplog):
    tried = _block_connect(monkeypatch)

    with caplog.at_level(logging.INFO, logger="mailer"):
        mailer.send_digest(DIGEST)  # must neither raise nor connect

    assert tried == []
    msgs = [r.getMessage() for r in caplog.records]
    assert any("not configured" in m for m in msgs)
    assert not any(r.levelno >= logging.WARNING for r in caplog.records)


def test_warns_loudly_when_partially_configured(no_smtp_env, monkeypatch, caplog):
    # only SMTP_HOST set; required USERNAME/PASSWORD/TO missing -> likely a typo
    monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
    tried = _block_connect(monkeypatch)

    with caplog.at_level(logging.WARNING, logger="mailer"):
        mailer.send_digest(DIGEST)

    assert tried == []  # did not attempt to send
    assert any(
        r.levelno == logging.WARNING and "partially configured" in r.getMessage()
        for r in caplog.records
    )


def test_send_failure_propagates(smtp_env, monkeypatch):
    class BoomSMTP(FakeSMTP):
        def send_message(self, msg):
            raise smtplib.SMTPException("boom")

    monkeypatch.setattr(smtplib, "SMTP", BoomSMTP)

    with pytest.raises(smtplib.SMTPException):
        mailer.send_digest(DIGEST)


# --- brief delivery (Phase 6) ----------------------------------------------

def test_send_brief_multipart_with_perspectives(smtp_env, monkeypatch):
    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)

    mailer.send_brief(BRIEF)

    msg = FakeSMTP.last.messages[0]
    assert "Brief" in msg["Subject"]
    assert msg.get_content_type() == "multipart/alternative"

    text = msg.get_body(preferencelist=("plain",)).get_content()
    assert "Title A" in text and "商业" in text and "biz take" in text

    html_body = msg.get_body(preferencelist=("html",)).get_content()
    assert "<a href=" in html_body and "技术" in html_body
    # HTML metacharacters escaped (source "&", summary "<b>", href ampersand)
    assert "&amp;" in html_body and "<b>" not in html_body


def test_send_brief_skips_when_unconfigured(no_smtp_env, monkeypatch):
    tried = _block_connect(monkeypatch)
    mailer.send_brief(BRIEF)  # must neither raise nor connect
    assert tried == []
