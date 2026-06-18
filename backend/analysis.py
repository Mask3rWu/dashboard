"""Time alignment, statistics, correlation, and anomaly detection.

Refactored to use column_registry and data_table_registry instead
of hardcoded DATA_TABLES.
"""

import math
from collections import defaultdict, OrderedDict
from backend.database import get_db
from backend.format_configs import get_table_name, get_columns_for_flight


# ── Registry helpers ──

def _get_model_id(conn, flight_id):
    """Get model_id for a flight."""
    row = conn.execute(
        "SELECT a.model_id FROM flights f JOIN aircraft a ON a.id = f.aircraft_id WHERE f.id = ?",
        (flight_id,)
    ).fetchone()
    return row['model_id'] if row else None


def _resolve_table_col(conn, model_id, col_key):
    """Resolve "data_type_key.column_name" → (table_name, column_name).

    Falls back to direct table.column format for backward compatibility.
    """
    if '.' not in col_key:
        return None, None

    dt_key, col_name = col_key.split('.', 1)

    # Try registry
    table = get_table_name(conn, model_id, dt_key)
    if table:
        # Verify column exists in registry
        reg = conn.execute(
            "SELECT column_name FROM column_registry WHERE model_id=? AND data_type_key=? AND column_name=?",
            (model_id, dt_key, col_name)
        ).fetchone()
        if reg:
            return table, col_name

    # Fallback: col_key might be "table_name.column" (old format)
    # Try to find matching table
    row = conn.execute(
        "SELECT table_name FROM data_table_registry WHERE model_id=? AND table_name=?",
        (model_id, dt_key)
    ).fetchone()
    if row:
        return dt_key, col_name

    return None, None


def _get_column_info(conn, model_id, dt_key, col_name):
    """Get display label, unit, and scale_factor for a column from the registry."""
    row = conn.execute(
        "SELECT display_label, unit, scale_factor FROM column_registry "
        "WHERE model_id=? AND data_type_key=? AND column_name=?",
        (model_id, dt_key, col_name)
    ).fetchone()
    if row:
        return row['display_label'], row['unit'] or '', row['scale_factor'] or 1.0
    return col_name, '', 1.0


# ── Column listing ──

def get_columns_for_flight_api(flight_id):
    """Return available columns for a flight, grouped by data type.

    Delegates to format_configs.get_columns_for_flight.
    """
    conn = get_db()
    result = get_columns_for_flight(conn, flight_id)
    conn.close()
    return result


# ── Aligned data ──

def get_aligned_data(flight_id, column_keys, ref_table=None, tolerance=0.5, filter_spec=None):
    """Align selected columns to a reference time series.

    Args:
        flight_id: Flight ID
        column_keys: List of "data_type_key.column_name" strings
        ref_table: Reference data_type_key for time base ("gps" default)
        tolerance: Max time difference for nearest-neighbor matching
        filter_spec: Optional filter with {logic, conditions}

    Returns:
        {times, ref_secs, series: {key: {label, unit, table, values}}, alerts}
    """
    conn = get_db()
    model_id = _get_model_id(conn, flight_id)
    if model_id is None:
        conn.close()
        return {'times': [], 'series': {}, 'alerts': [], 'error': 'Flight not found'}

    # Determine reference table
    if ref_table is None:
        ref_table = 'gps'
    ref_table_name = get_table_name(conn, model_id, ref_table)
    if ref_table_name is None:
        ref_table_name = get_table_name(conn, model_id, 'gps')
    if ref_table_name is None:
        conn.close()
        return {'times': [], 'series': {}, 'alerts': [], 'error': 'No reference table found'}

    # Get reference times
    ref_rows = conn.execute(
        f"SELECT time_str, time_sec FROM {ref_table_name} WHERE flight_id=? ORDER BY time_sec",
        (flight_id,)
    ).fetchall()

    if not ref_rows:
        # Try gps as fallback
        gps_table = get_table_name(conn, model_id, 'gps')
        if gps_table and gps_table != ref_table_name:
            ref_rows = conn.execute(
                f"SELECT time_str, time_sec FROM {gps_table} WHERE flight_id=? ORDER BY time_sec",
                (flight_id,)
            ).fetchall()

    if not ref_rows:
        # Try any available table
        tables = conn.execute(
            "SELECT table_name FROM data_table_registry WHERE model_id=?",
            (model_id,)
        ).fetchall()
        for t in tables:
            ref_rows = conn.execute(
                f"SELECT time_str, time_sec FROM {t['table_name']} WHERE flight_id=? ORDER BY time_sec LIMIT 1",
                (flight_id,)
            ).fetchall()
            if ref_rows:
                break

    if not ref_rows:
        conn.close()
        return {'times': [], 'series': {}, 'alerts': []}

    times = [r['time_str'] for r in ref_rows]
    ref_secs = [r['time_sec'] for r in ref_rows]

    # Group column_keys by data_type_key
    by_dt = defaultdict(list)
    for key in column_keys:
        if '.' in key:
            dt_key, col_name = key.split('.', 1)
            by_dt[dt_key].append(col_name)

    # Fetch and align data for each data type
    series = {}
    for dt_key, cols in by_dt.items():
        table_name = get_table_name(conn, model_id, dt_key)
        if not table_name:
            continue

        # Fetch all data for this table
        col_str = ', '.join(cols + ['time_sec'])
        try:
            db_rows = conn.execute(
                f"SELECT {col_str} FROM {table_name} WHERE flight_id=? ORDER BY time_sec",
                (flight_id,)
            ).fetchall()
        except Exception:
            continue

        if not db_rows:
            continue

        # Build lookup: sorted list of (time_sec, {col: val})
        table_data = []
        for row in db_rows:
            d = {c: row[c] for c in cols}
            table_data.append((row['time_sec'], d))

        # Align to reference times
        for col in cols:
            full_key = f"{dt_key}.{col}"
            label, unit, scale_factor = _get_column_info(conn, model_id, dt_key, col)
            values = []

            ti = 0
            for ref_t in ref_secs:
                while ti < len(table_data) - 1 and (
                    abs(table_data[ti + 1][0] - ref_t) < abs(table_data[ti][0] - ref_t) or
                    table_data[ti + 1][0] == table_data[ti][0]
                ):
                    ti += 1
                if ti < len(table_data) and abs(table_data[ti][0] - ref_t) <= tolerance:
                    values.append(table_data[ti][1].get(col))
                else:
                    values.append(None)

            series[full_key] = {
                'label': label,
                'unit': unit,
                'scale_factor': scale_factor,
                'table': table_name,
                'values': values,
            }

    # Get alerts — use column_registry for actual column names
    alert_table = get_table_name(conn, model_id, 'alert')
    alerts = []
    if alert_table:
        # Read alert columns from column_registry (ordered by ordinal)
        alert_col_rows = conn.execute(
            "SELECT column_name FROM column_registry "
            "WHERE model_id=? AND data_type_key='alert' AND ordinal IS NOT NULL "
            "ORDER BY ordinal",
            (model_id,)
        ).fetchall()

        if alert_col_rows:
            col_names = [r['column_name'] for r in alert_col_rows]
            cols_str = ', '.join(col_names)
            try:
                alert_rows = conn.execute(
                    f"SELECT time_str, time_sec, {cols_str} FROM {alert_table} "
                    f"WHERE flight_id=? ORDER BY time_sec",
                    (flight_id,)
                ).fetchall()
            except Exception:
                alert_rows = []

            # Map to frontend-compatible {desc, extra} format.
            # Prefer columns named with 'desc'/'extra', else use first/last column.
            desc_col = next((c for c in col_names if 'desc' in c.lower()), col_names[0] if col_names else None)
            extra_candidates = [c for c in col_names if 'extra' in c.lower()]
            extra_col = extra_candidates[0] if extra_candidates else (
                col_names[-1] if len(col_names) > 1 else None
            )

            alerts = [{
                'time_str': r['time_str'],
                'time_sec': r['time_sec'],
                'desc': str(r[desc_col]) if desc_col and r[desc_col] is not None else '',
                'extra': str(r[extra_col]) if extra_col and r[extra_col] is not None else '',
            } for r in alert_rows]

    conn.close()

    result = {
        'times': times,
        'ref_secs': ref_secs,
        'series': series,
        'alerts': alerts,
    }

    if filter_spec:
        result = apply_filter(result, filter_spec)

    return result


def apply_filter(aligned, filter_spec):
    """Compute filter mask and segments from aligned data."""
    n = len(aligned['times'])
    if n == 0:
        return aligned

    conditions = filter_spec.get('conditions', []) if isinstance(filter_spec, dict) else getattr(filter_spec, 'conditions', [])
    logic = (filter_spec.get('logic', 'and') if isinstance(filter_spec, dict) else getattr(filter_spec, 'logic', 'and'))

    masks = []
    for cond in conditions:
        if isinstance(cond, dict):
            col, op = cond.get('column'), cond.get('op')
            val, min_v, max_v = cond.get('value'), cond.get('min_val'), cond.get('max_val')
        else:
            col, op = cond.column, cond.op
            val, min_v, max_v = cond.value, cond.min_val, cond.max_val

        series_entry = aligned['series'].get(col)
        if not series_entry:
            continue

        values = series_entry['values']
        mask = [False] * n
        for i, v in enumerate(values):
            if v is None:
                continue
            if op == 'gt':
                mask[i] = v > val
            elif op == 'gte':
                mask[i] = v >= val
            elif op == 'lt':
                mask[i] = v < val
            elif op == 'lte':
                mask[i] = v <= val
            elif op == 'eq':
                mask[i] = abs(v - val) < 1e-9
            elif op == 'between':
                mask[i] = (min_v is None or v >= min_v) and (max_v is None or v <= max_v)
        masks.append(mask)

    if not masks:
        return aligned

    combined = masks[0][:]
    if logic == 'and':
        for m in masks[1:]:
            for i in range(n):
                combined[i] = combined[i] and m[i]
    else:
        for m in masks[1:]:
            for i in range(n):
                combined[i] = combined[i] or m[i]

    segments = []
    start = None
    for i in range(n):
        if combined[i] and start is None:
            start = i
        elif not combined[i] and start is not None:
            segments.append({'start': start, 'end': i})
            start = None
    if start is not None:
        segments.append({'start': start, 'end': n})

    aligned['mask'] = combined
    aligned['segments'] = segments
    return aligned


# ── Flight stats ──

def get_flight_stats(flight_id):
    """Compute summary statistics for a flight."""
    conn = get_db()
    model_id = _get_model_id(conn, flight_id)

    flight = conn.execute("SELECT * FROM flights WHERE id=?", (flight_id,)).fetchone()
    if not flight:
        conn.close()
        return {}

    # Get aircraft and model info
    ac = conn.execute(
        "SELECT a.serial_number, am.name as model_name FROM aircraft a "
        "JOIN aircraft_models am ON am.id = a.model_id WHERE a.id=?",
        (flight['aircraft_id'],)
    ).fetchone()

    stats = {
        'duration_sec': flight['duration_sec'],
        'start_time': flight['start_time'],
        'end_time': flight['end_time'],
        'drone_id': ac['serial_number'] if ac else '',
        'name': flight['name'],
    }

    if model_id is None:
        conn.close()
        return stats

    # Max altitude from gps or pos
    gps_table = get_table_name(conn, model_id, 'gps')
    pos_table = get_table_name(conn, model_id, 'pos')
    drone_table = get_table_name(conn, model_id, 'drone_state')
    engine_table = get_table_name(conn, model_id, 'engine')

    # Try gps for altitude
    max_alt = None
    if gps_table:
        try:
            row = conn.execute(
                f"SELECT MAX(nava_alt) as max_alt FROM {gps_table} WHERE flight_id=?",
                (flight_id,)
            ).fetchone()
            max_alt = row['max_alt']
        except Exception:
            pass

    if max_alt is None and pos_table:
        try:
            row = conn.execute(
                f"SELECT MAX(rel_alt) as max_alt FROM {pos_table} WHERE flight_id=?",
                (flight_id,)
            ).fetchone()
            max_alt = row['max_alt']
        except Exception:
            pass
    stats['max_altitude'] = max_alt

    # Max speed from drone_state
    max_speed = None
    if drone_table:
        try:
            row = conn.execute(
                f"SELECT MAX(ABS(fwd_vel)) as max_fwd FROM {drone_table} WHERE flight_id=?",
                (flight_id,)
            ).fetchone()
            max_speed = row['max_fwd']
        except Exception:
            pass
    stats['max_speed'] = max_speed

    # Engine stats
    if engine_table:
        try:
            row = conn.execute(
                f"SELECT AVG(engine_rpm) as avg_rpm, MAX(engine_rpm) as max_rpm "
                f"FROM {engine_table} WHERE flight_id=?",
                (flight_id,)
            ).fetchone()
            stats['avg_rpm'] = round(row['avg_rpm'], 1) if row and row['avg_rpm'] else 0
            stats['max_rpm'] = row['max_rpm'] if row else 0
        except Exception:
            stats['avg_rpm'] = 0
            stats['max_rpm'] = 0

        try:
            row = conn.execute(
                f"SELECT MIN(fuel_remaining) as min_fuel, MAX(fuel_remaining) as max_fuel "
                f"FROM {engine_table} WHERE flight_id=?",
                (flight_id,)
            ).fetchone()
            stats['fuel_start'] = row['max_fuel'] if row else None
            stats['fuel_end'] = row['min_fuel'] if row else None
        except Exception:
            stats['fuel_start'] = None
            stats['fuel_end'] = None
    else:
        stats['avg_rpm'] = 0
        stats['max_rpm'] = 0
        stats['fuel_start'] = None
        stats['fuel_end'] = None

    # Battery
    if drone_table:
        try:
            row = conn.execute(
                f"SELECT MIN(battery_pct) as min_bat, MAX(battery_pct) as max_bat "
                f"FROM {drone_table} WHERE flight_id=?",
                (flight_id,)
            ).fetchone()
            stats['battery_start'] = row['max_bat'] if row else None
            stats['battery_end'] = row['min_bat'] if row else None
        except Exception:
            stats['battery_start'] = None
            stats['battery_end'] = None
    else:
        stats['battery_start'] = None
        stats['battery_end'] = None

    # Alert count
    alert_table = get_table_name(conn, model_id, 'alert')
    if alert_table:
        row = conn.execute(
            f"SELECT COUNT(*) as cnt FROM {alert_table} WHERE flight_id=?",
            (flight_id,)
        ).fetchone()
        stats['alert_count'] = row['cnt'] if row else 0
    else:
        stats['alert_count'] = 0

    conn.close()
    return stats


# ── Correlation ──

def get_correlation(flight_id, column_keys):
    """Compute Pearson correlation matrix for selected columns."""
    aligned = get_aligned_data(flight_id, column_keys)
    series = aligned.get('series', {})
    if len(series) < 2:
        return {'columns': [], 'matrix': [], 'labels': []}

    keys = [k for k in column_keys if k in series]
    labels = [series[k]['label'] for k in keys]
    n = len(keys)

    values_by_key = {k: series[k]['values'] for k in keys}
    n_points = len(aligned.get('times', []))

    complete_rows = []
    for i in range(n_points):
        row = [values_by_key[k][i] for k in keys]
        if all(v is not None for v in row):
            complete_rows.append(row)

    if len(complete_rows) < 3:
        return {'columns': keys, 'labels': labels, 'matrix': [[1.0] * n for _ in range(n)]}

    m = len(complete_rows)
    means = [sum(row[j] for row in complete_rows) / m for j in range(n)]

    matrix = []
    for i in range(n):
        row_vals = []
        for j in range(n):
            if i == j:
                row_vals.append(1.0)
            else:
                num = sum((complete_rows[k][i] - means[i]) * (complete_rows[k][j] - means[j]) for k in range(m))
                den_i = math.sqrt(sum((complete_rows[k][i] - means[i]) ** 2 for k in range(m)))
                den_j = math.sqrt(sum((complete_rows[k][j] - means[j]) ** 2 for k in range(m)))
                if den_i == 0 or den_j == 0:
                    row_vals.append(0.0)
                else:
                    row_vals.append(round(num / (den_i * den_j), 4))
        matrix.append(row_vals)

    return {'columns': keys, 'labels': labels, 'matrix': matrix}


# ── Anomaly detection ──

def get_anomalies(flight_id, column_key, window_size=30, sigma=3.0):
    """Detect anomalies using sliding window z-score."""
    aligned = get_aligned_data(flight_id, [column_key])
    entry = aligned.get('series', {}).get(column_key)
    if not entry:
        return {'times': [], 'values': [], 'anomaly_indices': [], 'upper_bound': [], 'lower_bound': []}

    values = entry['values']
    times = aligned.get('times', [])
    n = len(values)

    anomaly_indices = []
    upper_bounds = [None] * n
    lower_bounds = [None] * n

    half = window_size // 2
    for i in range(n):
        if values[i] is None:
            continue
        start = max(0, i - half)
        end = min(n, i + half)
        window = [values[j] for j in range(start, end) if values[j] is not None]
        if len(window) < max(5, half):
            continue
        mean = sum(window) / len(window)
        std = math.sqrt(sum((v - mean) ** 2 for v in window) / len(window))
        if std == 0:
            continue
        upper = mean + sigma * std
        lower = mean - sigma * std
        upper_bounds[i] = upper
        lower_bounds[i] = lower
        if abs(values[i] - mean) > sigma * std:
            anomaly_indices.append(i)

    return {
        'times': times,
        'values': values,
        'anomaly_indices': anomaly_indices,
        'upper_bound': upper_bounds,
        'lower_bound': lower_bounds,
        'label': entry['label'],
        'unit': entry['unit'],
    }


# ── Cross-flight comparison ──

def get_compare(flight_ids, column_key):
    """Get aligned data for one column across multiple flights."""
    results = []
    for fid in flight_ids:
        conn = get_db()
        flight = conn.execute(
            "SELECT f.name, a.serial_number, f.duration_sec "
            "FROM flights f JOIN aircraft a ON a.id = f.aircraft_id WHERE f.id=?",
            (fid,)
        ).fetchone()
        if not flight:
            conn.close()
            continue

        aligned = get_aligned_data(fid, [column_key])
        entry = aligned.get('series', {}).get(column_key)
        if not entry:
            conn.close()
            continue

        ref_secs = aligned.get('ref_secs', [])

        results.append({
            'flight_id': fid,
            'name': f"{flight['name']} UAV{flight['serial_number']}",
            'times_sec': ref_secs,
            'values': entry['values'],
            'label': entry['label'],
            'unit': entry['unit'],
        })
        conn.close()

    return results
