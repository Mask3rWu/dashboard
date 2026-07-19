"""Structured phase metrics shared by local and server sync paths."""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Iterator


logger = logging.getLogger("flight_analyzer.sync.metrics")


def _process_memory_bytes() -> tuple[int | None, int | None]:
    if sys.platform == "win32":
        try:
            import ctypes
            from ctypes import wintypes

            class ProcessMemoryCounters(ctypes.Structure):
                _fields_ = [
                    ("cb", wintypes.DWORD),
                    ("PageFaultCount", wintypes.DWORD),
                    ("PeakWorkingSetSize", ctypes.c_size_t),
                    ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t),
                    ("PeakPagefileUsage", ctypes.c_size_t),
                ]

            counters = ProcessMemoryCounters()
            counters.cb = ctypes.sizeof(counters)
            process = ctypes.windll.kernel32.GetCurrentProcess()
            if ctypes.windll.psapi.GetProcessMemoryInfo(
                process, ctypes.byref(counters), counters.cb
            ):
                return int(counters.WorkingSetSize), int(counters.PeakWorkingSetSize)
        except (AttributeError, OSError, TypeError, ValueError):
            return None, None
    try:
        import resource

        peak = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
        if sys.platform != "darwin":
            peak *= 1024
        return None, peak
    except (ImportError, OSError, ValueError):
        return None, None


def path_size(path: str | None) -> int:
    if not path or not os.path.exists(path):
        return 0
    if os.path.isfile(path):
        return os.path.getsize(path)
    total = 0
    for root, _, files in os.walk(path):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(root, name))
            except OSError:
                continue
    return total


class SyncMetrics:
    def __init__(self, operation: str, operation_id: str | None = None):
        self.operation = operation
        self.operation_id = operation_id
        self.started_at = datetime.now().isoformat(timespec="milliseconds")
        self._started = time.perf_counter()
        self._phases: list[dict[str, Any]] = []
        self._peak_temp_bytes = 0
        current, peak = _process_memory_bytes()
        self._start_memory_bytes = current
        self._peak_memory_bytes = peak

    @contextmanager
    def phase(self, code: str, **values: Any) -> Iterator[dict[str, Any]]:
        started = time.perf_counter()
        item: dict[str, Any] = {
            "phase": code,
            "status": "running",
            **{key: value for key, value in values.items() if value is not None},
        }
        try:
            yield item
        except Exception as exc:
            item["status"] = "failed"
            item["error"] = str(exc)
            raise
        else:
            item["status"] = "completed"
        finally:
            item["duration_seconds"] = round(time.perf_counter() - started, 6)
            self._phases.append(item)
            _, peak = _process_memory_bytes()
            if peak is not None:
                self._peak_memory_bytes = max(self._peak_memory_bytes or 0, peak)

    def sample_temp(self, *paths: str | None) -> int:
        value = sum(path_size(path) for path in paths)
        self._peak_temp_bytes = max(self._peak_temp_bytes, value)
        return value

    def record_phase(
        self,
        code: str,
        duration_seconds: float,
        *,
        status: str = "completed",
        **values: Any,
    ) -> None:
        self._phases.append(
            {
                "phase": code,
                "status": status,
                **{key: value for key, value in values.items() if value is not None},
                "duration_seconds": round(duration_seconds, 6),
            }
        )

    def result(self, status: str = "completed") -> dict[str, Any]:
        current, peak = _process_memory_bytes()
        if peak is not None:
            self._peak_memory_bytes = max(self._peak_memory_bytes or 0, peak)
        payload = {
            "operation": self.operation,
            "operation_id": self.operation_id,
            "status": status,
            "started_at": self.started_at,
            "duration_seconds": round(time.perf_counter() - self._started, 6),
            "start_memory_bytes": self._start_memory_bytes,
            "end_memory_bytes": current,
            "peak_memory_bytes": self._peak_memory_bytes,
            "peak_temp_bytes": self._peak_temp_bytes,
            "phases": list(self._phases),
        }
        logger.info(json.dumps(payload, ensure_ascii=False, default=str))
        return payload
