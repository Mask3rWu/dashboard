from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

import pytest


TEST_ROOT = Path(tempfile.mkdtemp(prefix="flight_analyzer_tests_"))
TEST_DATA_DIR = TEST_ROOT / "data"
TEST_SERVER_DATA_DIR = TEST_ROOT / "server_data"
TEST_CONFIG_PATH = TEST_ROOT / "missing-flight-analyzer.ini"

# backend.database fixes these paths at import time, so configure isolation before
# any application module is collected.
os.environ["DATA_DIR"] = str(TEST_DATA_DIR)
os.environ["SERVER_DATA_DIR"] = str(TEST_SERVER_DATA_DIR)
os.environ["FLIGHT_ANALYZER_CONFIG"] = str(TEST_CONFIG_PATH)
os.environ["BUILTIN_MODEL_SEEDS_ENABLED"] = "0"
os.environ["SERVER_BUILTIN_MODEL_SEEDS_ENABLED"] = "0"


@pytest.fixture(scope="session")
def isolated_data_dir() -> Path:
    return TEST_DATA_DIR


def pytest_sessionfinish(session, exitstatus):
    shutil.rmtree(TEST_ROOT, ignore_errors=True)
