from datetime import date

import pytest
import requests

from issue_reporter.redmine import RedmineClient, resolutions


class FakeResponse:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} error")

    def json(self):
        return self._payload


class FakeSession:
    """Serves canned Redmine responses and records every request."""

    def __init__(self, routes):
        self.routes = routes
        self.headers = {}
        self.calls = []

    def get(self, url, params=None, timeout=None, verify=None):
        self.calls.append((url, dict(params or {}), verify))
        path = url.split("example.com/", 1)[1]
        handler = self.routes[path]
        return handler(params or {}) if callable(handler) else handler


def client(routes):
    session = FakeSession(routes)
    return RedmineClient("https://redmine.example.com/", "test-key", session=session,
                         page_size=2, pause_seconds=0), session


def test_paginates_until_total_count_and_sends_key_in_header():
    pages = {0: [{"id": 1}, {"id": 2}], 2: [{"id": 3}]}

    def issues(params):
        return FakeResponse({"issues": pages[params["offset"]], "total_count": 3})

    c, session = client({"issues.json": issues})

    got = [i["id"] for i in c.iter_issues(max_issues=10)]

    assert got == [1, 2, 3]
    assert len(session.calls) == 2
    assert session.headers["X-Redmine-API-Key"] == "test-key"
    assert all(verify is True for _, _, verify in session.calls)


def test_respects_max_issues():
    def issues(params):
        start, limit = params["offset"], params["limit"]
        page = [{"id": start + k + 1} for k in range(limit)]
        return FakeResponse({"issues": page, "total_count": 99})

    c, _ = client({"issues.json": issues})

    assert len(list(c.iter_issues(max_issues=3))) == 3


def test_status_id_lookup_is_case_insensitive():
    c, _ = client({"issue_statuses.json": FakeResponse(
        {"issue_statuses": [{"id": 1, "name": "New"}, {"id": 3, "name": "Resolved"}]}
    )})

    assert c.status_id("resolved") == 3
    assert c.status_id("Missing") is None


def test_custom_field_labels_without_admin_rights_fall_back_to_raw_values():
    c, _ = client({"custom_fields.json": FakeResponse({}, status=403)})

    assert c.custom_field_labels("Issue Type") == {}


def test_custom_field_labels():
    payload = {"custom_fields": [{"name": "Responsible Team", "possible_values": [
        {"value": "3", "label": "Core Platform "}, {"value": "4", "label": "Mobile App"}]}]}
    c, _ = client({"custom_fields.json": FakeResponse(payload)})

    assert c.custom_field_labels("Responsible Team") == {"3": "Core Platform", "4": "Mobile App"}


@pytest.mark.parametrize(
    ("journal_day", "new_status", "expected"),
    [("2026-10-16", "3", 1), ("2026-10-15", "3", 0), ("2026-10-16", "5", 0)],
)
def test_resolutions_from_journals(journal_day, new_status, expected):
    issues = [{
        "id": 7,
        "journals": [{
            "user": {"name": "Agent A"},
            "created_on": f"{journal_day}T08:00:00Z",
            "details": [{"property": "attr", "name": "status_id", "new_value": new_status}],
        }],
    }]

    found = resolutions(issues, status_id=3, day=date(2026, 10, 16))

    assert len(found) == expected
    if expected:
        assert found[0].resolved_by == "Agent A"
