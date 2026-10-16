"""Report rules (YAML) and connection settings (environment variables)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml


class SettingsError(ValueError):
    """Raised when required settings are missing or invalid."""


@dataclass(frozen=True)
class ReportRules:
    """What goes into the report and how it is grouped. Safe to commit: no secrets."""

    assignee_filter: str = ""
    weeks: int = 8
    highlight_threshold: int = 50
    issue_type_field: str = "Issue Type"
    team_field: str = "Responsible Team"
    resolved_status: str = "Resolved"
    transactional_keywords: list[str] = field(default_factory=list)
    owners: dict[str, list[str]] = field(default_factory=dict)

    def owner_of(self) -> dict[str, str]:
        """Map each issue type to the group that owns it."""
        return {t: owner for owner, types in self.owners.items() for t in types}


def load_rules(path: str | Path) -> ReportRules:
    path = Path(path)
    if not path.is_file():
        raise SettingsError(f"Rules file not found: {path}")
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    fields = raw.get("custom_fields") or {}
    rules = ReportRules(
        assignee_filter=str(raw.get("assignee_filter") or ""),
        weeks=int(raw.get("weeks", 8)),
        highlight_threshold=int(raw.get("highlight_threshold", 50)),
        issue_type_field=fields.get("issue_type", "Issue Type"),
        team_field=fields.get("responsible_team", "Responsible Team"),
        resolved_status=raw.get("resolved_status", "Resolved"),
        transactional_keywords=list(raw.get("transactional_keywords") or []),
        owners={k: list(v or []) for k, v in (raw.get("owners") or {}).items()},
    )
    if rules.weeks < 1:
        raise SettingsError("weeks must be at least 1")
    return rules


@dataclass(frozen=True)
class RedmineSettings:
    url: str
    api_key: str
    verify_ssl: bool = True
    max_issues: int = 2000
    project: str = ""

    @classmethod
    def from_env(cls) -> RedmineSettings:
        url = os.getenv("REDMINE_URL", "").strip()
        api_key = os.getenv("REDMINE_API_KEY", "").strip()
        missing = [n for n, v in (("REDMINE_URL", url), ("REDMINE_API_KEY", api_key)) if not v]
        if missing:
            raise SettingsError(f"Missing environment variables: {', '.join(missing)}")
        return cls(
            url=url,
            api_key=api_key,
            verify_ssl=os.getenv("REDMINE_VERIFY_SSL", "true").lower() not in {"0", "false", "no"},
            max_issues=int(os.getenv("REDMINE_MAX_ISSUES", "2000")),
            project=os.getenv("REDMINE_PROJECT", "").strip(),
        )


@dataclass(frozen=True)
class MailSettings:
    host: str
    port: int
    user: str
    password: str
    sender: str
    recipients: list[str]

    @classmethod
    def from_env(cls) -> MailSettings:
        host = os.getenv("SMTP_HOST", "").strip()
        recipients = [r.strip() for r in os.getenv("REPORT_EMAIL_TO", "").split(",") if r.strip()]
        if not host or not recipients:
            raise SettingsError("SMTP_HOST and REPORT_EMAIL_TO must be set to send the report")
        user = os.getenv("SMTP_USER", "")
        return cls(
            host=host,
            port=int(os.getenv("SMTP_PORT", "587")),
            user=user,
            password=os.getenv("SMTP_PASSWORD", ""),
            sender=os.getenv("REPORT_EMAIL_FROM") or user,
            recipients=recipients,
        )
