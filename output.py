"""Output module — render the digest to markdown, write a file, print to console.

Contract (Phase 1 brief §4):
    input  = a Digest
    output = a markdown file output/digest-YYYY-MM-DD.md (+ console print)
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from agent import Digest


def _inline(text: str) -> str:
    """Collapse internal whitespace/newlines so a value stays on one markdown line.

    A newline inside a title or summary would otherwise break the list item or
    its indentation.
    """
    return " ".join(text.split())


def render_markdown(digest: Digest, feed_url: str, day: date) -> str:
    """Render a Digest into a markdown string."""
    lines = [
        f"# News Digest — {day.isoformat()}",
        "",
        f"Source: {feed_url}",
        "",
    ]
    if not digest.items:
        lines.append("_No items._")
    for i, item in enumerate(digest.items, start=1):
        lines.append(f"{i}. **{_inline(item.title)}**")
        lines.append(f"   {_inline(item.one_line_summary)}")
        link = _inline(item.link)
        lines.append(f"   <{link}>" if link else "   (no link)")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def write_digest(markdown: str, output_dir: Path, day: date) -> Path:
    """Write the markdown to output_dir/digest-YYYY-MM-DD.md and return the path."""
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"digest-{day.isoformat()}.md"
    path.write_text(markdown, encoding="utf-8")
    return path
