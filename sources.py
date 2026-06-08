"""Multi-source collection layer (Phase 6, Unit 1) — gather many RSS feeds into
one normalized, deduplicated item list. No agent here; pure fetch + normalize.

Contract:
    input  = a list of Source(name, domain, url) configs
    output = a list of SourceItem(title, link, source, domain, published, text)

This is the second workflow's intake stage (the "brief" workflow). It reuses the
Phase 1 `fetch` module (one feedparser path) per source, then attaches each
source's provenance (name -> `source`, domain -> `domain`) onto every item. That
provenance comes ONLY from the config here — a downstream model can never rewrite
it (the same anti-fabrication rule the digest applies to title/link).

The per-URL fetch is injectable (`gather_sources(..., fetch=...)`) so offline
tests feed fixture RSS without a network, mirroring the `llm` seam philosophy.
A feed that fails to fetch/parse is logged and skipped — one bad source never
sinks the whole run (graceful degradation).

Cost cap (brief §3): at most `per_source_cap` (20) items are taken per source,
BEFORE cross-source dedup. The post-filter cap (<=8) lives in the orchestrator.

The SOURCES list is the starting feed config as data-in-code (brief §4); a YAML
or DB form is a Phase 7 ("sources as data") concern, deliberately not built yet.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass

from fetch import FeedItem, fetch_feed

log = logging.getLogger(__name__)

PER_SOURCE_CAP = 20  # brief §3 cost gate: take at most N items from each source

# The领域 buckets a Source.domain may take (brief §4 grouping). Kept as a tuple
# for documentation/validation; not enforced as an enum (a new bucket is just a
# new string), but the starting config below stays within these.
DOMAINS: tuple[str, ...] = ("科技", "金融", "政治", "商业")


@dataclass(frozen=True)
class Source:
    """One configured feed. `name` is a short publisher label (-> item.source);
    `domain` is the领域 bucket (-> item.domain). Both are provenance set HERE, not
    by any model."""

    name: str
    domain: str
    url: str


@dataclass(frozen=True)
class SourceItem:
    """A normalized item flowing into the brief workflow.

    title/link/published/text come from the feed entry; source/domain are stamped
    from the owning Source. Downstream agents may NOT rewrite any of these — they
    only add a summary and perspectives.
    """

    title: str
    link: str
    source: str   # = Source.name
    domain: str   # = Source.domain
    published: str
    text: str     # raw entry text (the feed's summary), for filtering/summarizing


# Starting feed config (brief §4): all RSS, one feedparser path. X/Twitter is
# absent (not fetchable now); a大佬 Substack/blog RSS slots in here later with the
# same parser. `name` labels are refined against each feed's real channel title.
SOURCES: list[Source] = [
    # 科技
    Source("Hacker News", "科技", "https://hnrss.org/frontpage?points=100"),
    Source(
        "GitHub Trending",
        "科技",
        "https://mshibanami.github.io/GitHubTrendingRSS/daily/all.xml",
    ),
    Source("GitHub Blog", "科技", "https://github.blog/feed/"),
    # 金融 (names match each feed's channel title, verified by a real fetch)
    Source("CNBC Finance", "金融", "https://www.cnbc.com/id/10000664/device/rss/rss.html"),
    Source("CNBC Business", "金融", "https://www.cnbc.com/id/10001147/device/rss/rss.html"),
    # 政治 (Politico's RSS 403s to a plain GET; replaced with The Guardian, verified
    # by a real fetch — Politico's anti-bot blocking made it unusable here.)
    Source("BBC Politics", "政治", "https://feeds.bbci.co.uk/news/politics/rss.xml"),
    Source("Guardian Politics", "政治", "https://www.theguardian.com/politics/rss"),
    # 商业
    Source("BBC Business", "商业", "https://feeds.bbci.co.uk/news/business/rss.xml"),
]


def _dedup_key(item: SourceItem) -> str:
    """Stable identity for cross-source dedup.

    Two sources (e.g. HN and GitHub Trending) often surface the same URL — key on
    the normalized link so a duplicate is dropped. When an item has no link, fall
    back to its normalized title (prefixed so a title can't collide with a link).
    """
    link = item.link.strip().rstrip("/").lower()
    if link:
        return link
    return "title::" + " ".join(item.title.split()).lower()


def _to_source_item(entry: FeedItem, source: Source) -> SourceItem:
    return SourceItem(
        title=entry.title,
        link=entry.link,
        source=source.name,
        domain=source.domain,
        published=entry.published,
        text=entry.summary,
    )


def gather_sources(
    sources: list[Source] | None = None,
    *,
    fetch: Callable[[str], list[FeedItem]] = fetch_feed,
    per_source_cap: int = PER_SOURCE_CAP,
) -> list[SourceItem]:
    """Fetch every source, normalize to SourceItems, dedup across sources.

    - Each source is fetched via `fetch` (default the real `fetch_feed`; tests
      inject a url->items fake). A source whose fetch/parse raises is logged and
      SKIPPED — the rest still return (graceful degradation).
    - At most `per_source_cap` items are kept per source (cost gate), applied
      BEFORE dedup so the cap is per the raw feed.
    - Duplicates (same link across sources) are removed, keeping the first
      occurrence; original order is otherwise preserved (deterministic).

    Returns the deduplicated list (possibly empty if every source failed/empty).
    """
    chosen = SOURCES if sources is None else sources
    seen: set[str] = set()
    result: list[SourceItem] = []
    for source in chosen:
        try:
            entries = fetch(source.url)
        except Exception as exc:  # noqa: BLE001 — any fetch/parse error degrades
            log.warning("source %r failed to fetch (%s); skipping", source.name, exc)
            continue
        for entry in entries[:per_source_cap]:
            item = _to_source_item(entry, source)
            key = _dedup_key(item)
            if key in seen:
                continue
            seen.add(key)
            result.append(item)
    log.info("gathered %d item(s) from %d source(s)", len(result), len(chosen))
    return result
