"""A small, well-behaved client for the parts of the Redmine REST API this report needs.

Compared with the original script, this version:

* sends the API key in a header and never prints it,
* verifies TLS certificates by default (``REDMINE_VERIFY_SSL=false`` is an explicit opt-out),
* retries transient errors (429/5xx) with backoff instead of stopping at the first failure,
* paginates with Redmine's ``total_count`` instead of a fixed issue count.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date
from typing import Any, Protocol

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

log = logging.getLogger(__name__)


class HttpSession(Protocol):
    headers: Any

    def get(self, url: str, **kwargs: Any) -> Any: ...


def session_with_retries(total: int = 4, backoff: float = 0.5) -> requests.Session:
    retry = Retry(
        total=total,
        backoff_factor=backoff,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=("GET",),
    )
    session = requests.Session()
    session.mount("https://", HTTPAdapter(max_retries=retry))
    session.mount("http://", HTTPAdapter(max_retries=retry))
    return session


@dataclass(frozen=True)
class Resolution:
    issue_id: int
    resolved_by: str
    resolved_on: str


class RedmineClient:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        *,
        verify_ssl: bool = True,
        session: HttpSession | None = None,
        page_size: int = 100,
        pause_seconds: float = 0.2,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.verify_ssl = verify_ssl
        self.page_size = page_size
        self.pause_seconds = pause_seconds
        self.session = session or session_with_retries()
        self.session.headers.update({"X-Redmine-API-Key": api_key, "Accept": "application/json"})

    def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        url = f"{self.base_url}/{path.lstrip('/')}"
        response = self.session.get(url, params=params, timeout=30, verify=self.verify_ssl)
        response.raise_for_status()
        return response.json()

    def iter_issues(self, max_issues: int = 2000, **filters: Any) -> Iterator[dict[str, Any]]:
        """Yield raw issues (any status), newest first, following Redmine's pagination."""
        offset = 0
        while offset < max_issues:
            limit = min(self.page_size, max_issues - offset)
            params = {"status_id": "*", "sort": "created_on:desc", "limit": limit, "offset": offset}
            params.update(filters)
            data = self._get("issues.json", params)
            batch = data.get("issues", [])
            log.info("Fetched issues %d-%d", offset, offset + len(batch) - 1)
            yield from batch
            offset += len(batch)
            if not batch or offset >= int(data.get("total_count", 0)):
                break
            time.sleep(self.pause_seconds)

    def status_id(self, name: str) -> int | None:
        for status in self._get("issue_statuses.json").get("issue_statuses", []):
            if status.get("name", "").lower() == name.lower():
                return int(status["id"])
        return None

    def custom_field_labels(self, field_name: str) -> dict[str, str]:
        """Map stored option values to labels for a key/value list custom field.

        ``custom_fields.json`` needs admin rights; without them the raw values are used.
        """
        try:
            fields = self._get("custom_fields.json").get("custom_fields", [])
        except requests.HTTPError as exc:
            log.warning("Cannot read custom field definitions (%s); using raw values", exc)
            return {}
        for item in fields:
            if item.get("name") == field_name:
                return {
                    str(v.get("value")): str(v.get("label", v.get("value"))).strip()
                    for v in item.get("possible_values", [])
                }
        return {}

    def issue_with_journals(self, issue_id: int) -> dict[str, Any]:
        return self._get(f"issues/{issue_id}.json", {"include": "journals"}).get("issue", {})

    def resolved_on(self, day: date, resolved_status: str) -> list[dict[str, Any]]:
        """Issues moved to ``resolved_status`` on ``day``, each with its journals."""
        status_id = self.status_id(resolved_status)
        if status_id is None:
            log.warning("Status %r not found; skipping resolved-by section", resolved_status)
            return []
        candidates = self.iter_issues(status_id=status_id, updated_on=f">={day.isoformat()}")
        return [self.issue_with_journals(int(issue["id"])) for issue in candidates]


def resolutions(raw_issues: list[dict[str, Any]], status_id: int, day: date) -> list[Resolution]:
    """Find who moved each issue to ``status_id`` on ``day``, using the issue journals."""
    found: list[Resolution] = []
    for issue in raw_issues:
        for journal in issue.get("journals", []):
            if not str(journal.get("created_on", "")).startswith(day.isoformat()):
                continue
            changed = any(
                d.get("property") == "attr"
                and d.get("name") == "status_id"
                and str(d.get("new_value")) == str(status_id)
                for d in journal.get("details", [])
            )
            if changed:
                user = (journal.get("user") or {}).get("name", "Unknown")
                found.append(Resolution(int(issue["id"]), user, journal["created_on"]))
    return found
