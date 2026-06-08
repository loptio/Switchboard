"""Offline tests for config loading (no network)."""

from config import DEFAULT_LANGUAGE, load_config


def test_output_language_defaults_to_simplified_chinese(monkeypatch):
    monkeypatch.delenv("OUTPUT_LANGUAGE", raising=False)
    assert DEFAULT_LANGUAGE == "简体中文"
    assert load_config().output_language == "简体中文"


def test_output_language_env_override(monkeypatch):
    monkeypatch.setenv("OUTPUT_LANGUAGE", "繁體中文")
    assert load_config().output_language == "繁體中文"
