"""In-process synchronization operation progress tracking."""

from __future__ import annotations

import threading
import time
from datetime import datetime


_PROGRESS: dict[str, dict] = {}
_LOCK = threading.Lock()


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def percent(start: float, end: float, local_percent: float) -> float:
    value = float(start) + (
        float(end) - float(start)
    ) * max(0.0, min(100.0, float(local_percent))) / 100.0
    return round(max(0.0, min(100.0, value)), 1)


def update(
    operation_id: str | None,
    *,
    phase: str,
    message: str,
    percent: float | int | None = None,
    status: str = "running",
    current: int | None = None,
    total: int | None = None,
    detail: str | None = None,
    unit: str | None = None,
    table_name: str | None = None,
    file_name: str | None = None,
    rate: float | None = None,
    eta_seconds: float | None = None,
    phase_percent: float | None = None,
) -> None:
    if not operation_id:
        return
    now = _now()
    with _LOCK:
        existing = _PROGRESS.get(operation_id, {})
        phase_changed = existing.get("phase") != phase

        resolved_phase_percent = phase_percent
        if (
            resolved_phase_percent is None
            and current is not None
            and total is not None
            and total > 0
        ):
            resolved_phase_percent = current / total * 100

        def value_or_existing(value, key: str):
            if value is not None:
                return value
            return None if phase_changed else existing.get(key)

        _PROGRESS[operation_id] = {
            **existing,
            "operation_id": operation_id,
            "status": status,
            "phase": phase,
            "message": message,
            "detail": detail,
            "percent": round(
                max(
                    0,
                    min(
                        100,
                        float(percent if percent is not None else existing.get("percent", 0)),
                    ),
                ),
                1,
            ),
            "current": value_or_existing(current, "current"),
            "total": value_or_existing(total, "total"),
            "unit": value_or_existing(unit, "unit"),
            "table_name": value_or_existing(table_name, "table_name"),
            "file_name": value_or_existing(file_name, "file_name"),
            "rate": value_or_existing(rate, "rate"),
            "eta_seconds": value_or_existing(eta_seconds, "eta_seconds"),
            "phase_percent": (
                round(max(0.0, min(100.0, resolved_phase_percent)), 1)
                if resolved_phase_percent is not None
                else (None if phase_changed else existing.get("phase_percent"))
            ),
            "created_at": existing.get("created_at") or now,
            "updated_at": now,
        }


def fail(operation_id: str | None, *, phase: str, message: str) -> None:
    update(
        operation_id,
        phase=phase,
        message=message,
        status="failed",
    )


def get(operation_id: str) -> dict | None:
    with _LOCK:
        item = _PROGRESS.get(operation_id)
        return dict(item) if item else None


def _format_bytes(value: int | None) -> str:
    if value is None:
        return "-"
    units = ["B", "KB", "MB", "GB"]
    amount = float(value)
    unit = units[0]
    for unit in units:
        if amount < 1024 or unit == units[-1]:
            break
        amount /= 1024
    if unit == "B":
        return f"{int(amount)} {unit}"
    return f"{amount:.1f} {unit}"


def byte_callback(operation_id: str | None, start: float, end: float, phase: str, verb: str):
    started = time.perf_counter()
    last_update = [0.0]

    def report(done: int, total: int | None) -> None:
        now = time.perf_counter()
        if done != total and now - last_update[0] < 0.2:
            return
        last_update[0] = now
        local_percent = 0 if not total else (done / total) * 100
        elapsed = max(now - started, 0.000001)
        rate = done / elapsed
        eta = (total - done) / rate if total and rate > 0 else None
        update(
            operation_id,
            phase=phase,
            message=f"{verb} {_format_bytes(done)} / {_format_bytes(total)}",
            percent=percent(start, end, local_percent),
            current=done,
            total=total,
            unit="bytes",
            rate=round(rate, 2),
            eta_seconds=round(eta, 1) if eta is not None else None,
            phase_percent=local_percent if total else None,
        )

    return report
