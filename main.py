"""Entry point — wire fetch -> agent -> output. One command = one digest.

    python main.py
"""

from __future__ import annotations

from datetime import date

from agent import summarize
from config import load_config
from fetch import fetch_feed
from output import render_markdown, write_digest


def main() -> int:
    cfg = load_config()

    print(f"Fetching: {cfg.feed_url}")
    items = fetch_feed(cfg.feed_url)
    print(f"Fetched {len(items)} items; summarizing top {cfg.count} via {cfg.model} ...")

    digest = summarize(items, cfg.count, cfg.model)

    today = date.today()
    markdown = render_markdown(digest, cfg.feed_url, today)
    path = write_digest(markdown, cfg.output_dir, today)

    print("\n" + "=" * 60)
    print(markdown)
    print("=" * 60)
    print(f"Wrote: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
