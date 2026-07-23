"""Read-only collaboration-server queries used by desktop data management."""

from __future__ import annotations

import re
from collections import OrderedDict, defaultdict
from typing import Any

from backend import analysis
from backend import server_database as db
from backend.sync import server as server_sync


MAX_DATA_FILTER_CANDIDATES = 1000
RECORD_FIELDS = {
    "record_location": "text",
    "record_weather": "text",
    "record_payload": "text",
    "record_wind_direction": "text",
    "record_total_duration_min": "number",
    "record_fuel_amount": "number",
    "record_takeoff_weight": "number",
    "record_altitude": "number",
    "record_wind_speed": "number",
    "record_temperature": "number",
}
NUMERIC_OPERATORS = {
    "gt": ">",
    "gte": ">=",
    "lt": "<",
    "lte": "<=",
    "eq": "=",
}


class _CompatResult:
    def __init__(self, result):
        self._rows = [dict(row._mapping) for row in result.fetchall()]

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


class SQLAlchemyCompatConnection:
    """Expose the small sqlite3 connection surface used by analysis.py."""

    def __init__(self, conn):
        self._conn = conn

    def execute(self, statement: str, parameters=None):
        values = list(parameters or [])
        index = 0

        def replace(_match):
            nonlocal index
            name = f"p{index}"
            index += 1
            return f":{name}"

        sql = re.sub(r"\?", replace, statement)
        if index != len(values):
            raise ValueError("SQL parameter count mismatch")
        params = {f"p{i}": value for i, value in enumerate(values)}
        return _CompatResult(self._conn.execute(db.text(sql), params))


def _rows(conn, sql: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    return [dict(row._mapping) for row in conn.execute(db.text(sql), params or {}).fetchall()]


def list_models(conn) -> dict[str, Any]:
    models = _rows(
        conn,
        """SELECT am.id, am.name, am.client_uid, am.version AS server_version,
                  am.created_at,
                  COUNT(DISTINCT a.id) AS aircraft_count,
                  COUNT(DISTINCT f.id) AS total_flights,
                  COALESCE(SUM(f.duration_sec), 0) AS total_flight_hours
           FROM aircraft_models am
           LEFT JOIN aircraft a
             ON a.model_id=am.id AND a.deleted_at IS NULL
           LEFT JOIN flights f
             ON f.aircraft_id=a.id AND f.deleted_at IS NULL
           WHERE am.deleted_at IS NULL
           GROUP BY am.id, am.name, am.client_uid, am.version, am.created_at
           ORDER BY am.name, am.id""",
    )
    for item in models:
        item.update({
            "server_id": int(item["id"]),
            "sync_origin": "server",
            "sync_state": "server_remote",
        })
    return {"models": models}


def list_aircraft(conn, model_id: int) -> dict[str, Any]:
    aircraft = _rows(
        conn,
        """SELECT a.id, a.model_id, a.name, a.client_uid,
                  a.version AS server_version, a.created_at,
                  COUNT(f.id) AS flight_count
           FROM aircraft a
           LEFT JOIN flights f
             ON f.aircraft_id=a.id AND f.deleted_at IS NULL
           WHERE a.model_id=:model_id AND a.deleted_at IS NULL
           GROUP BY a.id, a.model_id, a.name, a.client_uid, a.version, a.created_at
           ORDER BY a.name, a.id""",
        {"model_id": int(model_id)},
    )
    for item in aircraft:
        item.update({
            "server_id": int(item["id"]),
            "sync_origin": "server",
            "sync_state": "server_remote",
        })
    return {"aircraft": aircraft}


def get_model_columns(conn, model_id: int) -> dict[str, Any]:
    rows = _rows(
        conn,
        """SELECT dtr.data_type_key, dtr.table_name, dtr.display_label,
                  cr.column_name, cr.display_label AS column_label, cr.unit,
                  cr.scale_factor, cr.data_type, cr.ordinal, cr.is_numeric
           FROM data_table_registry dtr
           JOIN column_registry cr
             ON cr.model_id=dtr.model_id AND cr.data_type_key=dtr.data_type_key
           WHERE dtr.model_id=:model_id
           ORDER BY dtr.data_type_key, cr.ordinal""",
        {"model_id": int(model_id)},
    )
    groups: OrderedDict[str, dict[str, Any]] = OrderedDict()
    for row in rows:
        key = str(row["data_type_key"])
        group = groups.setdefault(
            key,
            {
                "data_type_key": key,
                "table": row["table_name"],
                "label": row["display_label"],
                "columns": [],
            },
        )
        group["columns"].append({
            "column_name": row["column_name"],
            "display_label": row["column_label"],
            "unit": row.get("unit") or "",
            "scale_factor": row.get("scale_factor") or 1.0,
            "data_type": row.get("data_type") or "REAL",
            "ordinal": row.get("ordinal"),
            "is_numeric": bool(row.get("is_numeric")),
        })
    return {"data_types": list(groups.values())}


def get_model_definition(conn, model_id: int) -> dict[str, Any]:
    rows = _rows(
        conn,
        """SELECT * FROM aircraft_models
           WHERE id=:model_id AND deleted_at IS NULL""",
        {"model_id": int(model_id)},
    )
    if not rows:
        raise ValueError(f"Server model not found: {model_id}")
    model = rows[0]
    model["config"] = server_sync.server_model_config(conn, int(model_id))
    return {"model": model}


def _metadata_where(payload: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    clauses = [
        "am.id=:model_id",
        "am.deleted_at IS NULL",
        "a.deleted_at IS NULL",
        "f.deleted_at IS NULL",
    ]
    params: dict[str, Any] = {"model_id": int(payload["model_id"])}
    aircraft_search = str(payload.get("aircraft_search") or "").strip().lower()
    if aircraft_search:
        clauses.append("LOWER(a.name) LIKE :aircraft_search")
        params["aircraft_search"] = f"%{aircraft_search}%"
    if payload.get("time_from") and payload.get("time_to"):
        clauses.extend([
            "f.start_time IS NOT NULL",
            "f.end_time IS NOT NULL",
            "f.start_time <= :time_to",
            "f.end_time >= :time_from",
        ])
        params["time_from"] = str(payload["time_from"]).replace("T", " ")
        params["time_to"] = str(payload["time_to"]).replace("T", " ")

    record_filter = payload.get("record_filter") or {}
    record_clauses: list[str] = []
    for index, condition in enumerate(record_filter.get("conditions") or []):
        field = str(condition.get("field") or "")
        field_type = RECORD_FIELDS.get(field)
        if not field_type:
            raise ValueError(f"Unsupported flight record field: {field}")
        column = f"f.{field}"
        if field_type == "text":
            value = str(condition.get("value") or "").strip().lower()
            if not value:
                continue
            key = f"record_{index}"
            record_clauses.append(f"LOWER(COALESCE({column}, '')) LIKE :{key}")
            params[key] = f"%{value}%"
            continue
        op = str(condition.get("op") or "")
        if op == "between":
            min_value = condition.get("min_val")
            max_value = condition.get("max_val")
            if min_value is None or max_value is None:
                continue
            min_key, max_key = f"record_min_{index}", f"record_max_{index}"
            record_clauses.append(f"{column} BETWEEN :{min_key} AND :{max_key}")
            params[min_key], params[max_key] = float(min_value), float(max_value)
        elif op in NUMERIC_OPERATORS and condition.get("value") not in (None, ""):
            key = f"record_{index}"
            record_clauses.append(f"{column} {NUMERIC_OPERATORS[op]} :{key}")
            params[key] = float(condition["value"])
    if record_clauses:
        logic = " OR " if record_filter.get("logic") == "or" else " AND "
        clauses.append(f"({logic.join(record_clauses)})")
    return " AND ".join(clauses), params


def _flight_select(where_sql: str) -> str:
    return f"""SELECT f.*, a.name AS aircraft_name,
                      am.id AS model_id, am.name AS model_name,
                      (SELECT COUNT(*) FROM flight_raw_files rf
                       WHERE rf.flight_id=f.id) AS raw_file_count
               FROM flights f
               JOIN aircraft a ON a.id=f.aircraft_id
               JOIN aircraft_models am ON am.id=a.model_id
               WHERE {where_sql}"""


def _normalize_flight(row: dict[str, Any]) -> dict[str, Any]:
    item = dict(row)
    item.update({
        "server_id": int(item["id"]),
        "sync_origin": "server",
        "sync_state": "server_remote",
        "import_time": item.get("created_at"),
        "raw_warnings": [],
    })
    return item


def search_flights(conn, payload: dict[str, Any]) -> dict[str, Any]:
    page = max(1, int(payload.get("page") or 1))
    page_size = max(1, min(200, int(payload.get("page_size") or 50)))
    where_sql, params = _metadata_where(payload)
    data_filter = payload.get("data_filter")

    if data_filter:
        candidates = _rows(
            conn,
            f"""SELECT f.id
                FROM flights f
                JOIN aircraft a ON a.id=f.aircraft_id
                JOIN aircraft_models am ON am.id=a.model_id
                WHERE {where_sql}
                ORDER BY f.created_at DESC, f.id DESC
                LIMIT {MAX_DATA_FILTER_CANDIDATES + 1}""",
            params,
        )
        if len(candidates) > MAX_DATA_FILTER_CANDIDATES:
            raise ValueError(
                f"数据项筛选候选架次超过 {MAX_DATA_FILTER_CANDIDATES}，请先缩小飞机或时间范围"
            )
        candidate_ids = [int(item["id"]) for item in candidates]
        matched_ids = analysis.match_flights_by_data_with_connection(
            SQLAlchemyCompatConnection(conn),
            int(payload["model_id"]),
            candidate_ids,
            data_filter,
        )
        if not matched_ids:
            rows: list[dict[str, Any]] = []
        else:
            id_params = {f"match_{i}": value for i, value in enumerate(matched_ids)}
            placeholders = ", ".join(f":match_{i}" for i in range(len(matched_ids)))
            rows = _rows(
                conn,
                _flight_select(f"f.id IN ({placeholders})")
                + " ORDER BY f.created_at DESC, f.id DESC",
                id_params,
            )
    else:
        count_row = _rows(
            conn,
            f"""SELECT COUNT(*) AS flight_count,
                       COALESCE(SUM(f.duration_sec), 0) AS duration_sec
                FROM flights f
                JOIN aircraft a ON a.id=f.aircraft_id
                JOIN aircraft_models am ON am.id=a.model_id
                WHERE {where_sql}""",
            params,
        )[0]
        total = int(count_row["flight_count"])
        duration_sec = float(count_row["duration_sec"] or 0)
        group_rows = _rows(
            conn,
            f"""SELECT f.aircraft_id, COUNT(*) AS matched_count,
                       COALESCE(SUM(f.duration_sec), 0) AS matched_duration_sec
                FROM flights f
                JOIN aircraft a ON a.id=f.aircraft_id
                JOIN aircraft_models am ON am.id=a.model_id
                WHERE {where_sql}
                GROUP BY f.aircraft_id""",
            params,
        )
        offset = (page - 1) * page_size
        page_rows = _rows(
            conn,
            _flight_select(where_sql)
            + " ORDER BY f.created_at DESC, f.id DESC "
            + f"LIMIT {page_size} OFFSET {offset}",
            params,
        )
        return {
            "flights": [_normalize_flight(row) for row in page_rows],
            "page": page,
            "page_size": page_size,
            "total": total,
            "summary": {"flight_count": total, "duration_sec": duration_sec},
            "aircraft_summaries": [
                {
                    "aircraft_id": int(row["aircraft_id"]),
                    "matched_count": int(row["matched_count"]),
                    "matched_duration_sec": float(row["matched_duration_sec"] or 0),
                }
                for row in group_rows
            ],
        }

    total = len(rows)
    duration_sec = sum(float(row.get("duration_sec") or 0) for row in rows)
    aircraft_summary: dict[int, dict[str, Any]] = defaultdict(
        lambda: {"matched_count": 0, "matched_duration_sec": 0.0}
    )
    for row in rows:
        summary = aircraft_summary[int(row["aircraft_id"])]
        summary["matched_count"] += 1
        summary["matched_duration_sec"] += float(row.get("duration_sec") or 0)
    start = (page - 1) * page_size
    page_rows = [_normalize_flight(row) for row in rows[start:start + page_size]]
    return {
        "flights": page_rows,
        "page": page,
        "page_size": page_size,
        "total": total,
        "summary": {"flight_count": total, "duration_sec": duration_sec},
        "aircraft_summaries": [
            {"aircraft_id": aircraft_id, **summary}
            for aircraft_id, summary in aircraft_summary.items()
        ],
    }
