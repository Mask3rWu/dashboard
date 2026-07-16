"""In-process synchronization operation progress tracking."""

from __future__ import annotations

import threading
from datetime import datetime


_PROGRESS: dict[str, dict] = {}
_LOCK = threading.Lock()


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def percent(start: float, end: float, local_percent: float) -> int:
    value = float(start) + (
        float(end) - float(start)
    ) * max(0.0, min(100.0, float(local_percent))) / 100.0
    return int(round(max(0.0, min(100.0, value))))


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
) -> None:
    if not operation_id:
        return
    now = _now()
    with _LOCK:
        existing = _PROGRESS.get(operation_id, {})
        _PROGRESS[operation_id] = {
            **existing,
            "operation_id": operation_id,
            "status": status,
            "phase": phase,
            "message": message,
            "detail": detail,
            "percent": int(
                max(
                    0,
                    min(
                        100,
                        round(percent if percent is not None else existing.get("percent", 0)),
                    ),
                )
            ),
            "current": current if current is not None else existing.get("current"),
            "total": total if total is not None else existing.get("total"),
            "created_at": existing.get("created_at") or now,
            "updated_at": now,
        }


def fail(operation_id: str | None, *, phase: str, message: str) -> None:
    update(
        operation_id,
        phase=phase,
        message=message,
        status="failed",
        percent=100,
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
    def report(done: int, total: int | None) -> None:
        local_percent = 0 if not total else (done / total) * 100
        update(
            operation_id,
            phase=phase,
            message=f"{verb} {_format_bytes(done)} / {_format_bytes(total)}",
            percent=percent(start, end, local_percent),
            current=done,
            total=total,
        )

    return report
