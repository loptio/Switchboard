"""Basic test for the fetch module: a sample RSS string parses into items.

Offline — exercises parse_feed only, no network and no agent.
"""

from pathlib import Path

from fetch import FeedItem, parse_feed

FIXTURE = Path(__file__).parent / "fixtures" / "sample_feed.xml"


def _items() -> list[FeedItem]:
    return parse_feed(FIXTURE.read_bytes())


def test_parses_all_items():
    items = _items()
    assert len(items) == 3
    assert all(isinstance(it, FeedItem) for it in items)


def test_first_item_fields():
    first = _items()[0]
    assert first.title == "First Post"
    assert first.link == "https://example.com/first"
    assert first.summary == "Summary of the first post."
    assert first.published == "Mon, 01 Jan 2026 10:00:00 GMT"


def test_titles_in_order():
    titles = [it.title for it in _items()]
    assert titles == ["First Post", "Second Post", "Third Post"]
