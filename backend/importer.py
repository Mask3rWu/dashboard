"""Config-driven generic data importer.

Replaces the 8 hardcoded import_* functions with a single generic
import_data_type() that reads column definitions from a format config.
"""

import logging
import sqlite3
from backend.format_configs import (
    load_format_config_by_model, data_table_name, get_data_type_key,
)

logger = logging.getLogger(__name__)


def _float(v):
    try:
        return float(v)
    except (ValueError, TypeError):
        return None


def _int(v):
    try:
        return int(float(v))
    except (ValueError, TypeError):
        return None


def _str(v):
    return v if v else None


COERCE = {
    'REAL': _float,
    'FLOAT': _float,
    'DOUBLE': _float,
    'INTEGER': _int,
    'INT': _int,
    'BOOL': lambda v: int(v) if v and v.lower() in ('true', '1') else (0 if v else None),
    'BOOLEAN': lambda v: int(v) if v and v.lower() in ('true', '1') else (0 if v else None),
    'TEXT': _str,
}


def time_to_sec(t_str):
    try:
        parts = t_str.strip().split(':')
        return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
    except (ValueError, IndexError):
        return 0.0


def import_data_type(conn, flight_id, filepath, data_type_key, format_config, model_id):
    """Import a single data file using format config column definitions.

    Args:
        conn: SQLite connection
        flight_id: flights.id
        filepath: Path to the data file
        data_type_key: e.g., 'gps', 'drone_state'
        format_config: Loaded format config dict
        model_id: aircraft_models.id

    Returns:
        int: Number of rows inserted
    """
    tdef = format_config['data_types'].get(data_type_key)
    if not tdef:
        logger.error(f"No config for data type: {data_type_key}")
        return 0

    columns = tdef['columns']
    table_name = data_table_name(model_id, data_type_key)
    has_uav = format_config.get('has_uav_send_id', False)

    # Per-file header detection
    from backend.scanner import has_header, parse_lines
    skip_header = has_header(filepath)

    # Build INSERT SQL — only include columns with non-null ordinals
    active_cols = [c for c in columns if c.get('ordinal') is not None]
    col_names = [c['name'] for c in active_cols]
    placeholders = ','.join(['?'] * (len(col_names) + 3))
    sql = (
        f"INSERT INTO {table_name} "
        f"(flight_id, time_str, time_sec, {','.join(col_names)}) "
        f"VALUES ({placeholders})"
    )

    # Parse file
    try:
        lines = parse_lines(filepath)
    except Exception as e:
        logger.error(f"Failed to read {filepath}: {e}")
        return 0

    if not lines:
        return 0

    start = 1 if skip_header else 0
    if start >= len(lines):
        return 0

    # Calculate base time
    base_sec = time_to_sec(lines[start].split()[0])

    data = []
    for line in lines[start:]:
        p = line.split()
        if not p:
            continue

        t = time_to_sec(p[0]) - base_sec
        row = [flight_id, p[0], t]

        for col in active_cols:
            ordinal = col.get('ordinal')
            val = p[ordinal] if ordinal < len(p) else None
            col_type = col.get('type', 'REAL').upper()
            coerce_fn = COERCE.get(col_type, _str)
            row.append(coerce_fn(val))

        data.append(tuple(row))

    # Batch insert
    _batch_insert(conn, sql, data)
    return len(data)


def import_alerts(conn, flight_id, filepath, data_type_key, format_config, model_id):
    """Special importer for alert files (multi-word descriptions).

    For legacy format configs that define semantic column names (alert_desc,
    extra_value, etc.), uses multi-word token parsing.  For auto-generated
    configs with generic col_N names, falls back to standard one-token-per-column
    import.
    """
    from backend.scanner import has_header, parse_lines
    tdef = format_config['data_types'].get(data_type_key, {})
    table_name = data_table_name(model_id, data_type_key)

    # Check whether config uses known legacy alert column names.
    columns = tdef.get('columns', [])
    active_cols = [c for c in columns if c.get('ordinal') is not None]
    col_names_set = {c['name'] for c in active_cols}
    known_alert_cols = {'alert_desc', 'extra_value', 'uav_id', 'drone_model'}

    if not col_names_set & known_alert_cols:
        # Auto-generated config — use generic token-per-column import
        logger.info(
            "Alert table %s uses generic column names, falling back to standard import",
            table_name,
        )
        return import_data_type(
            conn, flight_id, filepath, data_type_key, format_config, model_id
        )

    # ── Legacy format configs — multi-word alert parsing ──
    has_uav = format_config.get('has_uav_send_id', False)

    try:
        lines = parse_lines(filepath)
    except Exception as e:
        logger.error(f"Failed to read alert file {filepath}: {e}")
        return 0

    if not lines:
        return 0

    skip_header = has_header(filepath)
    start = 1 if skip_header else 0
    if start >= len(lines):
        return 0

    base_sec = time_to_sec(lines[start].split()[0])

    # Build INSERT based on format
    if has_uav:
        # Format A: Time, UAV, Model, Desc..., ExtraValue (at least 5 tokens)
        sql = (
            f"INSERT INTO {table_name} (flight_id, time_str, time_sec, uav_id, drone_model, alert_desc, extra_value) "
            f"VALUES (?,?,?,?,?,?,?)"
        )
    else:
        # Format B/C: Time, Desc..., ExtraValue (at least 3 tokens)
        sql = (
            f"INSERT INTO {table_name} (flight_id, time_str, time_sec, alert_desc, extra_value) "
            f"VALUES (?,?,?,?,?)"
        )

    data = []
    for line in lines[start:]:
        p = line.split()
        if len(p) < 2:
            continue

        t = time_to_sec(p[0]) - base_sec

        if has_uav:
            uav = p[1] if len(p) > 1 else None
            model = p[2] if len(p) > 2 else None
            if len(p) >= 5:
                desc = ' '.join(p[3:-1])
                extra = p[-1]
            elif len(p) == 4:
                desc = p[3]
                extra = None
            else:
                desc = None
                extra = None
            data.append((flight_id, p[0], t, uav, model, desc, extra))
        else:
            if len(p) >= 4:
                desc = ' '.join(p[1:-1])
                extra = p[-1]
            elif len(p) == 3:
                desc = p[2]
                extra = None
            elif len(p) == 2:
                desc = p[1]
                extra = None
            else:
                desc = None
                extra = None
            data.append((flight_id, p[0], t, desc, extra))

    _batch_insert(conn, sql, data)
    return len(data)


def _fix_duplicate_seconds(conn, table_name, flight_id):
    """Fix duplicate integer-seconds in ~1Hz data by shifting repeated timestamps.

    Pattern [a, b, b, c] with c - b >= 2 → second b becomes b+1 to fill the gap.
    Pattern [a, b, b, b+1] → no change (next second is already continuous).

    Only applies to tables with sample rate in [0.9, 1.1] Hz.
    """
    rows = conn.execute(
        f"SELECT id, time_sec FROM {table_name} WHERE flight_id=? ORDER BY time_sec",
        (flight_id,)
    ).fetchall()

    if len(rows) < 3:
        return

    # Check sample rate
    min_sec = rows[0]['time_sec']
    max_sec = rows[-1]['time_sec']
    duration = max_sec - min_sec
    if duration <= 0:
        return
    sample_hz = (len(rows) - 1) / duration
    if not (0.9 <= sample_hz <= 1.1):
        return

    # Scan for duplicate integer-second groups with a gap ahead
    updates = []
    i = 0
    while i < len(rows):
        curr_int = int(rows[i]['time_sec'])
        j = i + 1
        while j < len(rows) and int(rows[j]['time_sec']) == curr_int:
            j += 1

        count = j - i  # rows with this integer second
        if count >= 2 and j < len(rows):
            next_int = int(rows[j]['time_sec'])
            gap = next_int - curr_int
            if gap >= 2:
                # Shift excess rows forward: 2nd row → curr_int+1, 3rd → curr_int+2, etc.
                for k in range(1, count):
                    new_sec = float(curr_int + k)
                    if new_sec < float(next_int):
                        updates.append((new_sec, rows[i + k]['id']))

        i = j

    if updates:
        conn.executemany(
            f"UPDATE {table_name} SET time_sec=? WHERE id=?",
            updates
        )
        conn.commit()
        logger.info(
            "Fixed %d duplicate second(s) in %s for flight %d",
            len(updates), table_name, flight_id,
        )


def _batch_insert(conn, sql, data, batch_size=1000):
    """Batch insert with commit."""
    for i in range(0, len(data), batch_size):
        conn.executemany(sql, data[i:i + batch_size])
    conn.commit()


def import_files_for_session(conn, flight_id, files_info, model_id, format_config=None):
    """Import all files for a flight session.

    Args:
        conn: SQLite connection
        flight_id: flights.id
        files_info: list of dicts from scanner (with filepath, data_type_key, is_alert)
        model_id: aircraft_models.id (determines table names)
        format_config: the SAME generated config used to scan the files. When
            provided it is used directly (column / table definitions match the
            files_info data_type_keys exactly, since both derive from one scan).
            When omitted, the model's stored config is loaded — only correct when
            the stored config still matches the folder's actual file patterns.

    Returns:
        dict: {data_type_key: row_count, ...}
    """
    # Verify the model exists
    model = conn.execute(
        "SELECT id FROM aircraft_models WHERE id=?", (model_id,)
    ).fetchone()
    if not model:
        return {'error': f'Model {model_id} not found'}

    if format_config is None:
        format_config = load_format_config_by_model(conn, model_id)
    if not format_config:
        return {'error': f'Format config not found for model {model_id}'}

    total_rows = 0
    details = {}

    for f_info in files_info:
        dt_key = f_info['data_type_key']
        filepath = f_info['filepath']
        is_alert = f_info.get('is_alert', False)

        try:
            if is_alert:
                count = import_alerts(conn, flight_id, filepath, dt_key, format_config, model_id)
            else:
                count = import_data_type(conn, flight_id, filepath, dt_key, format_config, model_id)
                if count > 0:
                    table_name = data_table_name(model_id, dt_key)
                    _fix_duplicate_seconds(conn, table_name, flight_id)
            details[dt_key] = count
            total_rows += count
        except Exception as e:
            details[dt_key] = f"Error: {e}"
            logger.error(f"Import error {filepath}: {e}")

    # Update flight metadata
    _update_flight_meta(conn, flight_id, total_rows, format_config, model_id)

    return {
        'rows': total_rows,
        'details': details,
    }


def _update_flight_meta(conn, flight_id, total_rows, format_config, model_id):
    """Update flight duration, times, and row count from imported data.

    The time range is taken from the data type with the most rows (best time
    coverage). This is type-agnostic — no hardcoded data-type key — so unknown
    .txt types contribute on equal footing. Tables that do not exist (e.g. a
    type with no usable columns) are skipped.
    """
    # Fetch flight_date to build full datetime strings
    flight = conn.execute(
        "SELECT flight_date FROM flights WHERE id=?", (flight_id,)
    ).fetchone()
    flight_date = flight['flight_date'] if flight else None

    best = None
    best_rows = -1
    for dt_key in format_config['data_types']:
        table = data_table_name(model_id, dt_key)
        try:
            info = conn.execute(
                f"SELECT MIN(time_sec) as start_sec, MAX(time_sec) as end_sec, "
                f"MIN(time_str) as start_str, MAX(time_str) as end_str, "
                f"COUNT(*) as n "
                f"FROM {table} WHERE flight_id=?",
                (flight_id,)
            ).fetchone()
        except sqlite3.OperationalError:
            continue
        if not info or info['end_sec'] is None:
            continue
        n = info['n'] or 0
        if n > best_rows:
            best_rows = n
            best = info

    if best:
        duration = best['end_sec'] - best['start_sec']
        start_dt = f"{flight_date} {best['start_str']}" if flight_date else best['start_str']
        end_dt = f"{flight_date} {best['end_str']}" if flight_date else best['end_str']
        conn.execute(
            "UPDATE flights SET start_time=?, end_time=?, duration_sec=?, total_rows=? WHERE id=?",
            (start_dt, end_dt, duration, total_rows, flight_id)
        )
    else:
        conn.execute(
            "UPDATE flights SET total_rows=? WHERE id=?", (total_rows, flight_id)
        )
    conn.commit()
