from __future__ import annotations

import importlib.util
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "tools" / "benchmarks" / "benchmark_sync_import.py"
SPEC = importlib.util.spec_from_file_location("benchmark_sync_import", SCRIPT_PATH)
benchmark = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(benchmark)


def test_tsv_cell_uses_mysql_load_data_escaping():
    assert benchmark.tsv_cell(None) == r"\N"
    assert benchmark.tsv_cell("a\\b\tc\nd\re\0f\x1a") == r"a\\b\tc\nd\re\0f\Z"


def test_target_size_row_estimate_is_positive_and_monotonic():
    small = benchmark.rows_for_target_bytes(1024, 8)
    large = benchmark.rows_for_target_bytes(4096, 8)

    assert small > 0
    assert large > small


def test_generated_rows_match_declared_columns():
    assert len(benchmark.row_values(0, 12)) == len(benchmark.benchmark_columns(12))


def test_parser_requires_explicit_write_confirmation():
    args = benchmark.build_parser().parse_args([])

    assert args.confirm_write is False


def test_preflight_mode_is_read_only():
    args = benchmark.build_parser().parse_args(["--preflight-only", "--target-gb", "1"])

    assert args.preflight_only is True
    assert args.confirm_write is False


def test_capacity_disk_preflight_reports_required_margins(monkeypatch, tmp_path):
    usage = type("Usage", (), {"free": 2500})()
    monkeypatch.setattr(benchmark.shutil, "disk_usage", lambda _path: usage)

    result = benchmark.capacity_disk_preflight(1000, str(tmp_path))

    assert result["checks"][0]["required_bytes"] == 1200
    assert result["checks"][0]["ok"] is True
    assert result["checks"][1]["required_bytes"] == 3000
    assert result["checks"][1]["ok"] is False
    assert result["ok"] is False
