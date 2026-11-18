"""The committed OpenAPI document must match the code (CI fails when it is stale)."""

import json
from pathlib import Path

from app.main import app

OPENAPI_FILE = Path(__file__).resolve().parents[2] / "docs" / "openapi.json"


def test_openapi_is_committed_and_current():
    assert OPENAPI_FILE.exists(), "Run: python -m app.cli export-openapi"
    committed = json.loads(OPENAPI_FILE.read_text(encoding="utf-8"))
    assert committed == json.loads(
        json.dumps(app.openapi())
    ), "docs/openapi.json is stale. Run: python -m app.cli export-openapi"


def test_every_operation_is_documented():
    for path, methods in app.openapi()["paths"].items():
        for method, op in methods.items():
            assert op.get("summary"), f"{method.upper()} {path} has no summary"
            assert op.get("tags"), f"{method.upper()} {path} has no tags"
