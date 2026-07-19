"""Persistent server-side sync operation progress."""

from __future__ import annotations

import json
import threading
import time
from datetime import timedelta
from typing import Any

from backend import server_database as db


_RETENTION = timedelta(days=7)
_LOCK = threading.Lock()
_LAST_UPDATE: dict[str, tuple[str, float]] = {}


def start(operation_id: str, operation_type: str, message: str) -> None:
    now = db.utcnow()
    with db.get_engine().begin() as conn:
        conn.execute(
            db.text("DELETE FROM sync_operations WHERE expires_at < :now"),
            {"now": now},
        )
        conn.execute(
            db.text(
                """INSERT INTO sync_operations
                     (operation_id, operation_type, status, phase, message,
                      created_at, updated_at, expires_at)
                   VALUES
                     (:operation_id, :operation_type, 'running', 'server_prepare', :message,
                      :now, :now, :expires_at)
                   ON DUPLICATE KEY UPDATE
                     operation_type=VALUES(operation_type), status='running',
                     phase='server_prepare', message=VALUES(message),
                     current=NULL, total=NULL, unit=NULL, table_name=NULL,
                     file_name=NULL, rate=NULL, eta_seconds=NULL, metrics_json=NULL,
                     updated_at=VALUES(updated_at), expires_at=VALUES(expires_at)"""
            ),
            {
                "operation_id": operation_id,
                "operation_type": operation_type,
                "message": message,
                "now": now,
                "expires_at": now + _RETENTION,
            },
        )


def update(
    operation_id: str | None,
    *,
    phase: str,
    message: str,
    force: bool = False,
    **values: Any,
) -> None:
    if not operation_id:
        return
    monotonic_now = time.monotonic()
    with _LOCK:
        previous_phase, previous_at = _LAST_UPDATE.get(operation_id, ("", 0.0))
        if not force and phase == previous_phase and monotonic_now - previous_at < 0.2:
            return
        _LAST_UPDATE[operation_id] = (phase, monotonic_now)
    now = db.utcnow()
    allowed = {
        "status",
        "current",
        "total",
        "unit",
        "table_name",
        "file_name",
        "rate",
        "eta_seconds",
    }
    payload = {key: values.get(key) for key in allowed}
    payload.update(
        {
            "operation_id": operation_id,
            "phase": phase,
            "message": message,
            "updated_at": now,
            "expires_at": now + _RETENTION,
        }
    )
    with db.get_engine().begin() as conn:
        conn.execute(
            db.text(
                """UPDATE sync_operations
                   SET status=COALESCE(:status, status), phase=:phase, message=:message,
                       current=:current, total=:total, unit=:unit,
                       table_name=:table_name, file_name=:file_name,
                       rate=:rate, eta_seconds=:eta_seconds,
                       updated_at=:updated_at, expires_at=:expires_at
                   WHERE operation_id=:operation_id"""
            ),
            payload,
        )


def finish(
    operation_id: str | None,
    *,
    status: str,
    phase: str,
    message: str,
    metrics: dict[str, Any] | None = None,
) -> None:
    if not operation_id:
        return
    now = db.utcnow()
    with db.get_engine().begin() as conn:
        conn.execute(
            db.text(
                """UPDATE sync_operations
                   SET status=:status, phase=:phase, message=:message,
                       metrics_json=:metrics_json, updated_at=:updated_at,
                       expires_at=:expires_at
                   WHERE operation_id=:operation_id"""
            ),
            {
                "operation_id": operation_id,
                "status": status,
                "phase": phase,
                "message": message,
                "metrics_json": json.dumps(metrics, ensure_ascii=False, default=str)
                if metrics is not None
                else None,
                "updated_at": now,
                "expires_at": now + _RETENTION,
            },
        )


def get(conn, operation_id: str) -> dict[str, Any] | None:
    row = conn.execute(
        db.text("SELECT * FROM sync_operations WHERE operation_id=:operation_id"),
        {"operation_id": operation_id},
    ).first()
    if not row:
        return None
    item = dict(row._mapping)
    metrics = item.get("metrics_json")
    if isinstance(metrics, str):
        try:
            item["metrics"] = json.loads(metrics)
        except ValueError:
            item["metrics"] = None
    elif metrics is not None:
        item["metrics"] = metrics
    item.pop("metrics_json", None)
    for key in ("created_at", "updated_at", "expires_at"):
        if item.get(key) is not None:
            item[key] = item[key].isoformat(timespec="milliseconds")
    return item
