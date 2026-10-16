from pathlib import Path

from issue_reporter.cli import build_from_snapshot, main
from issue_reporter.mailer import build_message
from issue_reporter.settings import MailSettings


def test_demo_snapshot_builds_full_report(snapshot, rules_path: str, tmp_path: Path):
    report = build_from_snapshot(snapshot, rules_path, str(tmp_path))

    names = {p.name for p in report.attachments}
    assert names == {
        "report.html",
        "transactional_issues.csv",
        "non_transactional_issues.csv",
        "responsible_team_matrix.csv",
        "resolved_by.csv",
    }
    html = report.html
    assert "Transactional issues by owner and week" in html
    assert "Issue type by responsible team" in html
    assert "Resolved on 2026-10-16" in html
    # The synthetic incident week pushes mobile recharge failures over the threshold.
    assert 'class="text hot">Mobile recharge failed' in html


def test_csv_detail_has_links_and_only_its_category(snapshot, rules_path: str, tmp_path: Path):
    build_from_snapshot(snapshot, rules_path, str(tmp_path))

    lines = (tmp_path / "transactional_issues.csv").read_text(encoding="utf-8").splitlines()
    assert lines[0] == "week,id,link,issue_type,owner,assignee,team,status,created_on"
    assert "https://redmine.example.com/issues/" in lines[1]
    assert all("App crash" not in line for line in lines)


def test_report_escapes_html_in_issue_data(snapshot, rules_path: str, tmp_path: Path):
    snapshot["issues"][0]["assigned_to"]["name"] = "Tech Ops"
    snapshot["issues"][0]["custom_fields"][0]["value"] = "<script>alert(1)</script>"

    report = build_from_snapshot(snapshot, rules_path, str(tmp_path))

    assert "<script>" not in report.html


def test_cli_demo_command(rules_path: str, tmp_path: Path):
    assert main(["--rules", rules_path, "demo", "--out", str(tmp_path)]) == 0
    assert (tmp_path / "report.html").exists()


def test_cli_fetch_without_settings_is_a_clear_error(rules_path: str, monkeypatch, capsys):
    monkeypatch.delenv("REDMINE_URL", raising=False)
    monkeypatch.delenv("REDMINE_API_KEY", raising=False)

    assert main(["--rules", rules_path, "fetch"]) == 2
    assert "REDMINE_URL" in capsys.readouterr().err


def test_email_attaches_only_csv_files(snapshot, rules_path: str, tmp_path: Path):
    report = build_from_snapshot(snapshot, rules_path, str(tmp_path))
    settings = MailSettings("smtp.example.com", 587, "", "", "reports@example.com",
                            ["team@example.com"])

    msg = build_message(settings, "Weekly report", report.html, report.attachments)

    attached = sorted(part.get_filename() for part in msg.iter_attachments())
    assert attached == sorted(p.name for p in report.attachments if p.suffix == ".csv")
    assert msg.get_body(preferencelist=("html",)) is not None
