"""Pure unit tests for the robustness helpers (no network, no agent/API)."""

from datetime import date

import pytest

from agent import Digest, DigestItem, _parse_json_array
from config import _positive_int
from output import _inline, render_markdown


# --- config: DIGEST_COUNT parsing -----------------------------------------

def test_positive_int_default_when_unset(monkeypatch):
    monkeypatch.delenv("DIGEST_COUNT", raising=False)
    assert _positive_int("DIGEST_COUNT", 10) == 10


def test_positive_int_default_when_empty(monkeypatch):
    monkeypatch.setenv("DIGEST_COUNT", "")
    assert _positive_int("DIGEST_COUNT", 10) == 10


def test_positive_int_valid(monkeypatch):
    monkeypatch.setenv("DIGEST_COUNT", "5")
    assert _positive_int("DIGEST_COUNT", 10) == 5


def test_positive_int_rejects_non_integer(monkeypatch):
    monkeypatch.setenv("DIGEST_COUNT", "abc")
    with pytest.raises(ValueError):
        _positive_int("DIGEST_COUNT", 10)


@pytest.mark.parametrize("bad", ["0", "-3"])
def test_positive_int_rejects_below_one(monkeypatch, bad):
    monkeypatch.setenv("DIGEST_COUNT", bad)
    with pytest.raises(ValueError):
        _positive_int("DIGEST_COUNT", 10)


# --- agent: JSON array extraction/validation ------------------------------

def test_parse_plain_array():
    assert _parse_json_array('[{"a": 1}]') == [{"a": 1}]


def test_parse_tolerates_fences_and_prose():
    text = 'Here you go:\n```json\n[{"a": 1}, {"b": 2}]\n```\nDone.'
    assert _parse_json_array(text) == [{"a": 1}, {"b": 2}]


def test_parse_rejects_non_array():
    with pytest.raises(ValueError):
        _parse_json_array('{"a": 1}')


def test_parse_rejects_array_of_non_objects():
    with pytest.raises(ValueError):
        _parse_json_array("[1, 2, 3]")


# --- output: markdown sanitization ----------------------------------------

def test_inline_collapses_whitespace():
    assert _inline("a\n  b\tc") == "a b c"


def test_render_keeps_each_item_on_one_line():
    digest = Digest([DigestItem("Break\ning", "https://e.com/x", "sum\nmary")])
    md = render_markdown(digest, "https://feed", date(2026, 1, 1))
    assert "Break\ning" not in md
    assert "1. **Break ing**" in md
    assert "   sum mary" in md


def test_render_missing_link_uses_placeholder():
    digest = Digest([DigestItem("T", "", "s")])
    md = render_markdown(digest, "https://feed", date(2026, 1, 1))
    assert "<>" not in md
    assert "(no link)" in md
