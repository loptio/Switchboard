"""Offline tests for the multi-source collection layer (no network, no SDK).

The per-URL fetch is injected (a url -> FeedItems map over fixture RSS), so these
exercise normalization, the per-source cap, cross-source dedup, and graceful
degradation when a source fails — all deterministically.
"""

from pathlib import Path

import sources
from fetch import parse_feed
from sources import DOMAINS, SOURCES, Source, SourceItem, gather_sources

FIX = Path(__file__).parent / "fixtures"

TECH = Source("Tech", "科技", "https://feeds/tech")
BIZ = Source("Biz", "商业", "https://feeds/biz")


def _items(name):
    return parse_feed((FIX / name).read_bytes())


def _fetch_map(mapping, *, down=()):
    """A fake fetch: return fixture items per url, or raise for a 'down' url."""
    def _fetch(url):
        if url in down:
            raise RuntimeError("source down")
        return mapping[url]

    return _fetch


def test_normalizes_with_source_and_domain_provenance():
    fetch = _fetch_map({TECH.url: _items("feed_tech.xml")})
    items = gather_sources([TECH], fetch=fetch)

    assert len(items) == 2
    assert all(isinstance(it, SourceItem) for it in items)
    first = items[0]
    # provenance stamped from the Source config
    assert first.source == "Tech" and first.domain == "科技"
    # entry fields carried from the feed
    assert first.title == "Tech One"
    assert first.link == "https://t.example/1"
    assert first.text == "About tech one."
    assert first.published == "Mon, 01 Jan 2026 10:00:00 GMT"


def test_per_source_cap_truncates_before_dedup():
    fetch = _fetch_map({TECH.url: _items("feed_tech.xml")})
    items = gather_sources([TECH], fetch=fetch, per_source_cap=1)

    assert [it.title for it in items] == ["Tech One"]  # only the first kept


def test_cross_source_dedup_keeps_first_occurrence():
    fetch = _fetch_map(
        {TECH.url: _items("feed_tech.xml"), BIZ.url: _items("feed_biz.xml")}
    )
    items = gather_sources([TECH, BIZ], fetch=fetch)

    titles = [it.title for it in items]
    # Biz's "Shared Story" has the same link as Tech One (trailing slash) -> dropped.
    assert titles == ["Tech One", "Tech Two", "Biz One"]
    # the survivor is Tech's, with Tech provenance (first occurrence wins)
    shared = [it for it in items if it.link.rstrip("/") == "https://t.example/1"]
    assert len(shared) == 1 and shared[0].source == "Tech"


def test_failing_source_is_skipped_gracefully(caplog):
    import logging

    fetch = _fetch_map(
        {TECH.url: _items("feed_tech.xml"), BIZ.url: _items("feed_biz.xml")},
        down=(TECH.url,),
    )
    with caplog.at_level(logging.WARNING, logger="sources"):
        items = gather_sources([TECH, BIZ], fetch=fetch)

    # Tech failed; Biz still came through.
    assert [it.source for it in items] == ["Biz", "Biz"]
    assert any("failed to fetch" in r.getMessage() for r in caplog.records)


def test_all_sources_failed_returns_empty():
    fetch = _fetch_map({TECH.url: [], BIZ.url: []}, down=(TECH.url, BIZ.url))
    assert gather_sources([TECH, BIZ], fetch=fetch) == []


def test_empty_feeds_return_empty():
    fetch = _fetch_map({TECH.url: []})
    assert gather_sources([TECH], fetch=fetch) == []


def test_default_sources_config_is_sane():
    # The starting config (brief §4): 8 RSS sources, all within the领域 buckets.
    assert len(SOURCES) == 8
    assert all(s.url.startswith("http") for s in SOURCES)
    assert all(s.domain in DOMAINS for s in SOURCES)
    assert all(s.name for s in SOURCES)
    # gather_sources() with no args uses the module SOURCES (here via injected fetch).
    fetch = _fetch_map({s.url: [] for s in SOURCES})
    assert gather_sources(fetch=fetch) == []
