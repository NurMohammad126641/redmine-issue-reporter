"""Render the HTML email and the CSV attachments."""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
from jinja2 import Environment, PackageLoader, select_autoescape

from issue_reporter.issues import (
    NON_TRANSACTIONAL,
    TRANSACTIONAL,
    kpis,
    team_matrix,
    weekly_summary,
)
from issue_reporter.redmine import Resolution
from issue_reporter.settings import ReportRules

_env = Environment(
    loader=PackageLoader("issue_reporter", "templates"),
    autoescape=select_autoescape(enabled_extensions=("html", "j2")),
    trim_blocks=True,
    lstrip_blocks=True,
)

DETAIL_COLUMNS = ["week", "id", "link", "issue_type", "owner", "assignee", "team", "status",
                  "created_on"]


@dataclass
class Section:
    title: str
    columns: list[str]
    rows: list[list[dict[str, Any]]] = field(default_factory=list)
    totals: list[Any] = field(default_factory=list)


@dataclass
class Report:
    html: str
    attachments: list[Path]


def _cell(value: Any, css: str = "") -> dict[str, Any]:
    return {"value": value, "css": css}


def weekly_section(title: str, table: pd.DataFrame, threshold: int) -> Section:
    weeks = [c for c in table.columns if c not in ("owner", "issue_type", "total")]
    section = Section(title=title, columns=["Owner", "Issue type", *weeks, "Total"])
    if table.empty:
        return section
    for values in table.to_dict("records"):
        total = int(values["total"])
        hot = "hot" if total > threshold else ""
        cells = [_cell(values["owner"], "text"), _cell(values["issue_type"], f"text {hot}".strip())]
        cells += [_cell(int(values[w])) for w in weeks]
        cells.append(_cell(total, hot))
        section.rows.append(cells)
    week_totals = [int(table[w].sum()) for w in weeks]
    section.totals = ["Total", "", *week_totals, int(table["total"].sum())]
    return section


def team_section(matrix: pd.DataFrame) -> Section:
    teams = [c for c in matrix.columns if c not in ("issue_type", "total")]
    section = Section(
        title="Issue type by responsible team", columns=["Issue type", *teams, "Total"]
    )
    if matrix.empty:
        return section
    for values in matrix[matrix["issue_type"] != "total"].to_dict("records"):
        cells = [_cell(values["issue_type"], "text")]
        cells += [_cell(int(values[t])) for t in teams]
        cells.append(_cell(int(values["total"])))
        section.rows.append(cells)
    totals = matrix[matrix["issue_type"] == "total"].to_dict("records")
    if totals:
        section.totals = ["Total", *[int(totals[0][t]) for t in teams], int(totals[0]["total"])]
    return section


def resolved_section(found: Sequence[Resolution], day: date) -> Section:
    counts = Counter(r.resolved_by for r in found)
    section = Section(
        title=f"Resolved on {day.isoformat()}, by person", columns=["Resolved by", "Issues"]
    )
    for person, count in counts.most_common():
        section.rows.append([_cell(person, "text"), _cell(count)])
    if counts:
        section.totals = ["Total", sum(counts.values())]
    return section


def write_csvs(
    df: pd.DataFrame,
    matrix: pd.DataFrame,
    found: Sequence[Resolution],
    out_dir: Path,
    issue_url: str = "",
) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    base = issue_url.rstrip("/")
    detail = df.sort_values("created_on", ascending=False).assign(
        created_on=lambda d: d["created_on"].dt.strftime("%Y-%m-%d %H:%M"),
        link=lambda d: d["id"].map(lambda i: f"{base}/issues/{i}" if base else ""),
    )
    paths: list[Path] = []
    for category, name in (
        (TRANSACTIONAL, "transactional_issues.csv"),
        (NON_TRANSACTIONAL, "non_transactional_issues.csv"),
    ):
        path = out_dir / name
        detail.loc[detail["category"] == category, DETAIL_COLUMNS].to_csv(path, index=False)
        paths.append(path)

    team_path = out_dir / "responsible_team_matrix.csv"
    matrix.to_csv(team_path, index=False)
    paths.append(team_path)

    resolved_path = out_dir / "resolved_by.csv"
    resolved = pd.DataFrame(
        [asdict(r) for r in found], columns=["issue_id", "resolved_by", "resolved_on"]
    )
    resolved.to_csv(resolved_path, index=False)
    paths.append(resolved_path)
    return paths


def build_report(
    df: pd.DataFrame,
    found: Sequence[Resolution],
    rules: ReportRules,
    as_of: pd.Timestamp,
    out_dir: str | Path,
    issue_url: str = "",
) -> Report:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    matrix = team_matrix(df)
    threshold = rules.highlight_threshold
    sections = [
        weekly_section(
            "Transactional issues by owner and week", weekly_summary(df, TRANSACTIONAL), threshold
        ),
        weekly_section(
            "Non-transactional issues by owner and week",
            weekly_summary(df, NON_TRANSACTIONAL),
            threshold,
        ),
        team_section(matrix),
        resolved_section(found, as_of.date()),
    ]
    first = pd.Timestamp(df["week_start"].min()) if not df.empty else as_of
    html = _env.get_template("report.html.j2").render(
        title="Weekly Support Issue Report",
        period=f"{first.strftime('%d %b %Y')} to {as_of.strftime('%d %b %Y')}",
        generated=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        kpis=kpis(df),
        resolved_total=len(found),
        resolved_day=as_of.strftime("%d %b"),
        sections=sections,
        threshold=threshold,
    )
    html_path = out_dir / "report.html"
    html_path.write_text(html, encoding="utf-8")
    attachments = write_csvs(df, matrix, found, out_dir, issue_url)
    return Report(html=html, attachments=[html_path, *attachments])
