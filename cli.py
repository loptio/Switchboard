"""Operator CLI — thin wiring only (no business logic).

Each subcommand delegates straight to the data layer / runner / scheduler:

    python cli.py run-once
    python cli.py add-schedule --cron "0 6 * * *" [--tz UTC] [--workflow news]
    python cli.py list-schedules
    python cli.py list-runs [--limit N]
    python cli.py scheduler          # start the long-running heartbeat (Ctrl-C to stop)
"""

from __future__ import annotations

import argparse
import sys

import db
import runner
import scheduler


def _run_once(args: argparse.Namespace) -> int:
    run = runner.run_once(trigger="manual")
    print(f"run {run.id}: {run.status}")
    if run.status == "failed":
        print(f"error: {run.error}", file=sys.stderr)
        return 1
    for out in db.list_outputs(run.id):
        print(f"  output {out.id} ({out.type}), {len(out.content)} chars")
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cli", description="Operator commands for the news workflow."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("run-once", help="run the workflow once now (manual trigger)").set_defaults(
        func=_run_once
    )

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

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
