"""Fetch module — deterministic RSS fetching/parsing. No agent here.

Contract (Phase 1 brief §4):
    input  = feed URL
    output = list of items, each {title, link, summary, published}

`parse_feed` is split from `fetch_feed` so parsing is testable offline.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.request import Request, urlopen

import feedparser


@dataclass(frozen=True)
class FeedItem:
    """One feed entry. The fields the brief's module contract requires."""

    title: str
    link: str
    summary: str
    published: str


def _clean(text: str) -> str:
    return (text or "").strip()


def parse_feed(content: str | bytes) -> list[FeedItem]:
    """Parse raw RSS/Atom content into FeedItems. Pure — no network, no agent."""
    parsed = feedparser.parse(content)
    items: list[FeedItem] = []
    for entry in parsed.entries:
        items.append(
            FeedItem(
                title=_clean(entry.get("title", "")),
                link=_clean(entry.get("link", "")),
                summary=_clean(entry.get("summary", "")),
                # RSS uses <pubDate> (-> "published"); Atom uses <updated>.
                # Short-circuit so we only probe "updated" when "published" is
                # absent (avoids feedparser's updated->published deprecation warning).
                published=_clean(entry.get("published", "") or entry.get("updated", "")),
            )
        )
    return items


def fetch_feed(url: str) -> list[FeedItem]:
    """Download a feed URL and parse it into FeedItems."""
    request = Request(url, headers={"User-Agent": "news-digest/0.1 (Phase 1)"})
    with urlopen(request, timeout=30) as response:
        content = response.read()
    return parse_feed(content)
