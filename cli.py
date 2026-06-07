"""Operator CLI — thin wiring only (no business logic).

Each subcommand delegates straight to the data layer / runner / scheduler:

    python cli.py run-once [--review]                # --review pauses for human approval
    python cli.py resume-run <run_id> --decision approve|redo [--feedback "..."]
    python cli.py checkpointer-setup                 # create checkpoint tables (run once)
    python cli.py add-schedule --cron "0 6 * * *" [--tz UTC] [--workflow news]
    python cli.py list-schedules
    python cli.py list-runs [--limit N]
    python cli.py scheduler          # start the long-running heartbeat (Ctrl-C to stop)
    python cli.py create-user --username admin     # control-plane login (prompts for password)
    python cli.py set-password --username admin     # reset that password (prompts)
"""

from __future__ import annotations

import argparse
import getpass
import sys

import db
import runner
import scheduler
from api.security import hash_password


def _print_outputs(run_id: str) -> None:
    for out in db.list_outputs(run_id):
        print(f"  output {out.id} ({out.type}), {len(out.content)} chars")


def _print_review(payload: dict | None) -> None:
    digest = (payload or {}).get("digest") or {}
    print("  digest for review:")
    for i, item in enumerate(digest.get("items", []), start=1):
        print(f"    {i}. {item.get('title', '')} — {item.get('one_line_summary', '')}")
    issues = (payload or {}).get("issues") or []
    if issues:
        print("  open issues:")
        for iss in issues:
            print(f"    - [{iss.get('kind', '')}] {iss.get('detail', '')}")


def _report_review(run, outcome) -> int:
    """Report a review/resume outcome (shared by run-once --review and resume-run)."""
    if outcome is None:  # the run failed
        print(f"run {run.id}: {run.status}")
        print(f"error: {run.error}", file=sys.stderr)
        return 1
    if outcome.status == "suspended":
        print(f"run {run.id}: awaiting_input")
        _print_review(outcome.payload)
        print(
            f'  resume: python cli.py resume-run {run.id} '
            f'--decision approve|redo [--feedback "..."]'
        )
        return 0
    print(f"run {run.id}: {run.status}")
    _print_outputs(run.id)
    return 0


def _run_once(args: argparse.Namespace) -> int:
    if getattr(args, "review", False):
        run, outcome = runner.run_review_once()
        return _report_review(run, outcome)
    run = runner.run_once(trigger="manual")
    print(f"run {run.id}: {run.status}")
    if run.status == "failed":
        print(f"error: {run.error}", file=sys.stderr)
        return 1
    _print_outputs(run.id)
    return 0


def _resume_run(args: argparse.Namespace) -> int:
    decision: dict = {"action": args.decision}
    if args.feedback:
        decision["feedback"] = args.feedback
    try:
        run, outcome = runner.resume_run(args.run_id, decision)
    except (LookupError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return _report_review(run, outcome)


def _checkpointer_setup(args: argparse.Namespace) -> int:
    import checkpoint

    checkpoint.run_setup()
    print("checkpoint tables ready (idempotent)")
    return 0


def _add_schedule(args: argparse.Namespace) -> int:
    s = scheduler.add_schedule(args.workflow, args.cron, tz=args.tz)
    print(f"schedule {s.id}: {s.cron} ({s.timezone}), next_run_at={s.next_run_at.isoformat()}")
    return 0


def _list_schedules(args: argparse.Namespace) -> int:
    for s in db.list_schedules():
        state = "on" if s.enabled else "off"
        nxt = s.next_run_at.isoformat() if s.next_run_at else "-"
        print(f"{s.id}  {s.cron}  {s.timezone}  [{state}]  next={nxt}")
    return 0


def _list_runs(args: argparse.Namespace) -> int:
    for r in db.list_runs(limit=args.limit):
        print(f"{r.id}  {r.workflow}  {r.status}  {r.trigger}  {r.created_at.isoformat()}")
    return 0


def _scheduler(args: argparse.Namespace) -> int:
    scheduler.main()
    return 0


def _prompt_password() -> str | None:
    """Read a password interactively (twice), keeping plaintext out of argv and
    shell history. Returns the password, or None if empty / mismatched."""
    pw = getpass.getpass("Password: ")
    if not pw:
        print("password must not be empty", file=sys.stderr)
        return None
    if pw != getpass.getpass("Confirm password: "):
        print("passwords do not match", file=sys.stderr)
        return None
    return pw


def _create_user(args: argparse.Namespace) -> int:
    pw = _prompt_password()
    if pw is None:
        return 1
    try:
        user = db.create_user(args.username, hash_password(pw))
    except ValueError as exc:  # username already exists
        print(f"error: {exc} (use set-password to change it)", file=sys.stderr)
        return 1
    print(f"created user {user.username} ({user.id})")
    return 0


def _set_password(args: argparse.Namespace) -> int:
    pw = _prompt_password()
    if pw is None:
        return 1
    try:
        db.set_user_password(args.username, hash_password(pw))
    except LookupError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"updated password for {args.username}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cli", description="Operator commands for the news workflow."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    ro = sub.add_parser("run-once", help="run the workflow once now (manual trigger)")
    ro.add_argument(
        "--review",
        action="store_true",
        help="human-in-the-loop: pause for approval before finishing",
    )
    ro.set_defaults(func=_run_once)

    rr = sub.add_parser("resume-run", help="resume a run awaiting human input")
    rr.add_argument("run_id")
    rr.add_argument("--decision", required=True, choices=["approve", "redo"])
    rr.add_argument("--feedback", default=None, help="feedback to use with --decision redo")
    rr.set_defaults(func=_resume_run)

    sub.add_parser(
        "checkpointer-setup",
        help="create the LangGraph checkpoint tables (run once, like a migration)",
    ).set_defaults(func=_checkpointer_setup)

    add = sub.add_parser("add-schedule", help="create a schedule")
    add.add_argument("--cron", required=True, help='5-field cron, e.g. "0 6 * * *"')
    add.add_argument("--tz", default="UTC", help="timezone (default UTC)")
    add.add_argument("--workflow", default="news")
    add.set_defaults(func=_add_schedule)

    sub.add_parser("list-schedules", help="list schedules").set_defaults(func=_list_schedules)

    runs = sub.add_parser("list-runs", help="list recent runs")
    runs.add_argument("--limit", type=int, default=20)
    runs.set_defaults(func=_list_runs)

    sub.add_parser(
        "scheduler", help="start the long-running scheduler (Ctrl-C to stop)"
    ).set_defaults(func=_scheduler)

    cu = sub.add_parser("create-user", help="create the control-plane login user")
    cu.add_argument("--username", required=True)
    cu.set_defaults(func=_create_user)

    sp = sub.add_parser("set-password", help="set an existing user's password")
    sp.add_argument("--username", required=True)
    sp.set_defaults(func=_set_password)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
