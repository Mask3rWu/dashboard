from __future__ import annotations

import json
from pathlib import Path


def api_route_contract(app) -> list[dict[str, object]]:
    contract = []
    for route in app.routes:
        path = getattr(route, "path", "")
        if not path.startswith("/api/"):
            continue
        methods = sorted(
            method
            for method in getattr(route, "methods", set())
            if method not in {"HEAD", "OPTIONS"}
        )
        contract.append({"methods": methods, "path": path})
    return contract


def load_contract(name: str) -> list[dict[str, object]]:
    path = Path(__file__).parent / "contracts" / name
    return json.loads(path.read_text(encoding="utf-8"))


def assert_schema(model, expected_fields: list[str], expected_defaults: dict[str, object] | None = None):
    fields = model.model_fields
    assert list(fields) == expected_fields
    for name, value in (expected_defaults or {}).items():
        assert not fields[name].is_required()
        assert fields[name].default == value
