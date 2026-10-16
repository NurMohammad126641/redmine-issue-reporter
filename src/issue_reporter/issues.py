"""Turn raw Redmine issues into a tidy table and the summaries the report shows."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass
from typing import Any

import pandas as pd

from issue_reporter.settings import ReportRules

TRANSACTIONAL = "Transactional"
NON_TRANSACTIONAL = "Non-Transactional"


@dataclass(frozen=True)
class Issue:
    id: int
    subject: str
    status: str
    issue_type: str
    assignee: str
    team: str
    created_on: str


def parse_issue(
    raw: Mapping[str, Any],
    rules: ReportRules,
    labels: Mapping[str, Mapping[str, str]] | None = None,
) -> Issue:
    """Flatten one Redmine issue, resolving option codes to labels where we know them."""
    labels = labels or {}
    custom = {f.get("name"): f.get("value") for f in raw.get("custom_fields", [])}

    def field(name: str) -> str:
        value = custom.get(name)
        if value in (None, ""):
            return "N/A"
        return labels.get(name, {}).get(str(value), str(value)).strip()

    return Issue(
        id=int(raw["id"]),
        subject=str(raw.get("subject", "")),
        status=(raw.get("status") or {}).get("name", "N/A"),
        issue_type=field(rules.issue_type_field),
        assignee=(raw.get("assigned_to") or {}).get("name", "N/A"),
        team=field(rules.team_field),
        created_on=str(raw["created_on"]),
    )


def to_frame(issues: Iterable[Issue], rules: ReportRules, as_of: pd.Timestamp) -> pd.DataFrame:
    """One row per issue in the reporting window, with week, category and owner columns."""
    columns = list(Issue.__dataclass_fields__)
    df = pd.DataFrame([asdict(i) for i in issues], columns=columns)
    df["created_on"] = pd.to_datetime(df["created_on"], utc=True)

    if rules.assignee_filter:
        wanted = rules.assignee_filter.strip().lower()
        df = df[df["assignee"].str.strip().str.lower() == wanted]

    # Weeks run Monday to Sunday; only the last `rules.weeks` weeks up to `as_of` are kept.
    naive = df["created_on"].dt.tz_convert(None)
    df = df.assign(week_start=naive.dt.to_period("W-SUN").dt.start_time)
    as_of_naive = as_of.tz_convert(None) if as_of.tzinfo is not None else as_of
    last_week = as_of_naive.to_period("W-SUN").start_time
    first_week = last_week - pd.Timedelta(weeks=rules.weeks - 1)
    df = df[(df["week_start"] >= first_week) & (df["week_start"] <= last_week)].copy()

    keywords = [k.lower() for k in rules.transactional_keywords]
    is_trx = df["issue_type"].str.lower().map(lambda t: any(k in t for k in keywords))
    df["category"] = is_trx.map({True: TRANSACTIONAL, False: NON_TRANSACTIONAL})
    df["owner"] = df["issue_type"].map(rules.owner_of()).fillna("Unassigned")
    df["week"] = df["week_start"].dt.strftime("%d %b")
    return df.reset_index(drop=True)


def week_columns(df: pd.DataFrame) -> list[str]:
    """Week labels in chronological order."""
    ordered = df.drop_duplicates("week_start").sort_values("week_start")
    return ordered["week"].tolist()


def weekly_summary(df: pd.DataFrame, category: str) -> pd.DataFrame:
    """Issues per owner and issue type, one column per week, plus a total."""
    weeks = week_columns(df)
    subset = df[df["category"] == category]
    if subset.empty:
        return pd.DataFrame(columns=["owner", "issue_type", *weeks, "total"])
    table = subset.pivot_table(
        index=["owner", "issue_type"], columns="week", values="id", aggfunc="count", fill_value=0
    )
    table = table.reindex(columns=weeks, fill_value=0)
    table["total"] = table.sum(axis=1)
    table = table.reset_index().sort_values(["owner", "total"], ascending=[True, False])
    table.columns.name = None
    return table.reset_index(drop=True)


def team_matrix(df: pd.DataFrame) -> pd.DataFrame:
    """Issue type by responsible team, with row and column totals."""
    if df.empty:
        return pd.DataFrame(columns=["issue_type", "total"])
    matrix = pd.crosstab(df["issue_type"], df["team"], margins=True, margins_name="total")
    matrix = matrix.reset_index()
    matrix.columns.name = None
    return matrix


def kpis(df: pd.DataFrame) -> dict[str, int]:
    return {
        "total": int(len(df)),
        "transactional": int((df["category"] == TRANSACTIONAL).sum()),
        "non_transactional": int((df["category"] == NON_TRANSACTIONAL).sum()),
        "open": int((~df["status"].isin(["Resolved", "Closed", "Rejected"])).sum()),
    }
