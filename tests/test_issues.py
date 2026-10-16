import pandas as pd

from issue_reporter.issues import (
    NON_TRANSACTIONAL,
    TRANSACTIONAL,
    kpis,
    parse_issue,
    team_matrix,
    to_frame,
    week_columns,
    weekly_summary,
)
from issue_reporter.settings import ReportRules

AS_OF = pd.Timestamp("2026-10-16T09:00:00Z")


def raw(issue_id, created, issue_type, team="Core", assignee="Tech Ops", status="New"):
    return {
        "id": issue_id,
        "subject": f"issue {issue_id}",
        "status": {"name": status},
        "assigned_to": {"name": assignee},
        "created_on": created,
        "custom_fields": [
            {"name": "Issue Type", "value": issue_type},
            {"name": "Responsible Team", "value": team},
        ],
    }


def rules(**overrides) -> ReportRules:
    base = {
        "assignee_filter": "Tech Ops",
        "weeks": 2,
        "transactional_keywords": ["money out"],
        "owners": {"Payments Ops": ["Wallet - Money out"]},
    }
    base.update(overrides)
    return ReportRules(**base)


def test_parse_issue_resolves_option_labels():
    issue = parse_issue(
        raw(1, "2026-10-15T10:00:00Z", "7", team="3"),
        rules(),
        labels={"Issue Type": {"7": "Wallet - Money out"}, "Responsible Team": {"3": "Core"}},
    )

    assert issue.issue_type == "Wallet - Money out"
    assert issue.team == "Core"
    assert issue.assignee == "Tech Ops"


def test_missing_custom_field_becomes_na():
    item = raw(1, "2026-10-15T10:00:00Z", "App crash")
    item["custom_fields"] = []

    issue = parse_issue(item, rules())

    assert issue.issue_type == "N/A" and issue.team == "N/A"


def frame():
    r = rules()
    issues = [
        parse_issue(raw(1, "2026-10-15T10:00:00Z", "Wallet - Money out"), r),
        parse_issue(raw(2, "2026-10-14T10:00:00Z", "Wallet - Money out"), r),
        parse_issue(raw(3, "2026-10-07T10:00:00Z", "App crash", team="Mobile"), r),
        parse_issue(raw(4, "2026-10-07T11:00:00Z", "App crash", assignee="Support"), r),
        parse_issue(raw(5, "2026-09-01T10:00:00Z", "App crash"), r),  # outside the 2-week window
    ]
    return to_frame(issues, r, AS_OF)


def test_frame_filters_assignee_and_window_and_labels_weeks():
    df = frame()

    assert sorted(df["id"]) == [1, 2, 3]
    assert week_columns(df) == ["05 Oct", "12 Oct"]
    assert set(df["category"]) == {TRANSACTIONAL, NON_TRANSACTIONAL}
    assert df.loc[df["id"] == 3, "owner"].item() == "Unassigned"


def test_weekly_summary_counts_and_totals():
    df = frame()

    trx = weekly_summary(df, TRANSACTIONAL)

    assert list(trx.columns) == ["owner", "issue_type", "05 Oct", "12 Oct", "total"]
    row = trx.iloc[0]
    assert (row["owner"], row["05 Oct"], row["12 Oct"], row["total"]) == (
        "Payments Ops",
        0,
        2,
        2,
    )


def test_team_matrix_has_totals():
    matrix = team_matrix(frame())

    totals = matrix[matrix["issue_type"] == "total"].iloc[0]
    assert totals["total"] == 3


def test_kpis():
    assert kpis(frame()) == {"total": 3, "transactional": 2, "non_transactional": 1, "open": 3}
