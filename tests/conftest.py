from __future__ import annotations

import json
from importlib import resources
from pathlib import Path
from typing import Any

import pytest

from issue_reporter.settings import ReportRules, load_rules

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def rules_path() -> str:
    return str(ROOT / "config" / "report.yaml")


@pytest.fixture
def rules(rules_path: str) -> ReportRules:
    return load_rules(rules_path)


@pytest.fixture
def snapshot() -> dict[str, Any]:
    sample = resources.files("issue_reporter") / "data" / "sample_snapshot.json"
    return json.loads(sample.read_text(encoding="utf-8"))
