"""Output module — render a digest/brief to markdown and write a file.

Contracts:
    Phase 1 (digest): a Digest -> output/digest-YYYY-MM-DD.md
    Phase 6 (brief) : a Brief  -> output/brief-YYYY-MM-DD.md

The renderers differ (a brief carries per-item perspectives a digest has not), but
both share one file-writing helper so the brief reuses the existing output pipeline
rather than rebuilding it.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from agent import Digest
from brief_agent import Brief
from coding_agent import CodingResult


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


def render_brief_markdown(brief: Brief) -> str:
    """Render a Brief into a markdown string (per item: summary + perspectives)."""
    lines = [f"# Brief — {brief.date}", ""]
    if not brief.items:
        lines.append("_No items._")
    for i, item in enumerate(brief.items, start=1):
        lines.append(f"{i}. **{_inline(item.title)}** — _{_inline(item.source)} · {_inline(item.domain)}_")
        link = _inline(item.link)
        lines.append(f"   <{link}>" if link else "   (no link)")
        lines.append(f"   {_inline(item.summary)}")
        for p in item.perspectives:
            lines.append(f"   - **{_inline(p.stance)}**: {_inline(p.take)}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def render_coding_markdown(result: CodingResult) -> str:
    """Render a CodingResult into markdown: a summary, the changed-file list, the status
    (so a stopped-at-limit run is visible — hardening #3), and the unified diff in a
    fenced block (what the human reviews in U2)."""
    lines = [
        "# Coding run",
        "",
        f"**Status:** {result.status}",
        "",
        "## Summary",
        "",
        _inline(result.summary) if result.summary else "_(no summary)_",
        "",
        "## Changed files",
        "",
    ]
    if result.changed_files:
        lines.extend(f"- `{f}`" for f in result.changed_files)
    else:
        lines.append("_No files changed._")
    lines.extend(["", "## Diff", "", "```diff", result.diff.rstrip("\n") if result.diff else "", "```"])
    return "\n".join(lines).rstrip() + "\n"


def _write(markdown: str, output_dir: Path, name: str) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / name
    path.write_text(markdown, encoding="utf-8")
    return path


def write_digest(markdown: str, output_dir: Path, day: date) -> Path:
    """Write the markdown to output_dir/digest-YYYY-MM-DD.md and return the path."""
    return _write(markdown, output_dir, f"digest-{day.isoformat()}.md")


def write_brief(markdown: str, output_dir: Path, day: date) -> Path:
    """Write the markdown to output_dir/brief-YYYY-MM-DD.md and return the path."""
    return _write(markdown, output_dir, f"brief-{day.isoformat()}.md")


def write_coding(markdown: str, output_dir: Path, day: date) -> Path:
    """Write the markdown to output_dir/coding-YYYY-MM-DD.md and return the path."""
    return _write(markdown, output_dir, f"coding-{day.isoformat()}.md")
