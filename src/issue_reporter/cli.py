"""Command line entry point.

    issue-report demo                 build a report from the bundled synthetic data
    issue-report fetch                download issues (and today's resolutions) from Redmine
    issue-report build                turn a downloaded snapshot into report.html + CSVs
    issue-report send                 email the last built report
    issue-report run                  fetch + build + send, for a scheduler (cron, Task Scheduler)
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from collections.abc import Sequence
from datetime import datetime, timezone
from importlib import resources
from pathlib import Path
from typing import Any

import pandas as pd
from dotenv import load_dotenv

from issue_reporter import mailer
from issue_reporter.issues import parse_issue, to_frame
from issue_reporter.redmine import RedmineClient, resolutions
from issue_reporter.report import Report, build_report
from issue_reporter.settings import MailSettings, RedmineSettings, SettingsError, load_rules

SAMPLE = resources.files("issue_reporter") / "data" / "sample_snapshot.json"
DEFAULT_RULES = "config/report.yaml"
DEFAULT_SNAPSHOT = "data/snapshot.json"
DEFAULT_OUT = "report"


def fetch_snapshot(settings: RedmineSettings, rules_path: str) -> dict[str, Any]:
    """Download everything the report needs into one JSON-serialisable snapshot."""
    rules = load_rules(rules_path)
    client = RedmineClient(settings.url, settings.api_key, verify_ssl=settings.verify_ssl)
    filters = {"project_id": settings.project} if settings.project else {}
    issues = list(client.iter_issues(max_issues=settings.max_issues, **filters))
    status_id = client.status_id(rules.resolved_status)
    today = datetime.now(timezone.utc).date()
    resolved = client.resolved_on(today, rules.resolved_status) if status_id else []
    labels = {
        rules.issue_type_field: client.custom_field_labels(rules.issue_type_field),
        rules.team_field: client.custom_field_labels(rules.team_field),
    }
    return {
        "as_of": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "redmine_url": settings.url,
        "resolved_status_id": status_id,
        "labels": labels,
        "issues": issues,
        "resolved_issues": resolved,
    }


def build_from_snapshot(snapshot: dict[str, Any], rules_path: str, out_dir: str) -> Report:
    rules = load_rules(rules_path)
    as_of = pd.Timestamp(snapshot["as_of"])
    if as_of.tzinfo is None:
        as_of = as_of.tz_localize("UTC")
    labels = snapshot.get("labels") or {}
    issues = [parse_issue(raw, rules, labels) for raw in snapshot["issues"]]
    df = to_frame(issues, rules, as_of)
    status_id = snapshot.get("resolved_status_id")
    found = (
        resolutions(snapshot.get("resolved_issues", []), int(status_id), as_of.date())
        if status_id
        else []
    )
    return build_report(df, found, rules, as_of, out_dir, snapshot.get("redmine_url", ""))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="issue-report", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--rules", default=DEFAULT_RULES, help="report rules (YAML)")
    sub = parser.add_subparsers(dest="command", required=True)

    demo = sub.add_parser("demo", help="build a report from the bundled synthetic data")
    demo.add_argument("--out", default=DEFAULT_OUT)

    fetch = sub.add_parser("fetch", help="download a snapshot from Redmine")
    fetch.add_argument("--snapshot", default=DEFAULT_SNAPSHOT)

    build = sub.add_parser("build", help="build report.html and CSVs from a snapshot")
    build.add_argument("--snapshot", default=DEFAULT_SNAPSHOT)
    build.add_argument("--out", default=DEFAULT_OUT)

    send = sub.add_parser("send", help="email the built report")
    send.add_argument("--out", default=DEFAULT_OUT)

    run = sub.add_parser("run", help="fetch, build and send")
    run.add_argument("--snapshot", default=DEFAULT_SNAPSHOT)
    run.add_argument("--out", default=DEFAULT_OUT)
    return parser


def _send(out_dir: str) -> None:
    folder = Path(out_dir)
    html = (folder / "report.html").read_text(encoding="utf-8")
    subject = f"Weekly Support Issue Report - {datetime.now().strftime('%d %b %Y')}"
    settings = MailSettings.from_env()
    message = mailer.build_message(settings, subject, html, sorted(folder.glob("*.csv")))
    mailer.send(settings, message)
    print(f"Report sent to {', '.join(settings.recipients)}")


def main(argv: Sequence[str] | None = None) -> int:
    load_dotenv()
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO").upper(),
                        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    args = _parser().parse_args(argv)

    try:
        if args.command == "demo":
            snapshot = json.loads(SAMPLE.read_text(encoding="utf-8"))
            report = build_from_snapshot(snapshot, args.rules, args.out)
            print("Built " + ", ".join(p.name for p in report.attachments) + f" in {args.out}/")
            return 0

        if args.command in ("fetch", "run"):
            snapshot = fetch_snapshot(RedmineSettings.from_env(), args.rules)
            path = Path(args.snapshot)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"Saved {len(snapshot['issues'])} issues to {path}")

        if args.command in ("build", "run"):
            snapshot = json.loads(Path(args.snapshot).read_text(encoding="utf-8"))
            report = build_from_snapshot(snapshot, args.rules, args.out)
            print("Built " + ", ".join(p.name for p in report.attachments) + f" in {args.out}/")

        if args.command in ("send", "run"):
            _send(args.out)
    except SettingsError as exc:
        print(f"Settings error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
