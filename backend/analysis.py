"""Time alignment, statistics, correlation, and anomaly detection.

Refactored to use column_registry and data_table_registry instead
of hardcoded DATA_TABLES.
"""

import math
from bisect import bisect_left
from collections import Counter, defaultdict, OrderedDict
from backend.database import get_db
from backend.import_pipeline.format_configs import get_table_name, get_columns_for_flight


def time_to_sec(t_str):
    """Convert HH:MM:SS[.f] to float seconds since midnight."""
    try:
        parts = t_str.strip().split(':')
        return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
    except (ValueError, IndexError):
        return 0.0


def sec_to_time_str(sec):
    """Convert float seconds since midnight to HH:MM:SS string."""
    total = max(0, int(round(sec)))
    h = total // 3600
    m = (total % 3600) // 60
    s = total % 60
    return f"{h:02d}:{m:02d}:{s:02d}"


# ── Registry helpers ──

def _get_model_id(conn, flight_id):
    """Get model_id for a flight."""
    row = conn.execute(
        "SELECT a.model_id FROM flights f JOIN aircraft a ON a.id = f.aircraft_id WHERE f.id = ?",
        (flight_id,)
    ).fetchone()
    return row['model_id'] if row else None


def _get_alert_data_type(conn, model_id):
    """Find a model's alert data type by its is_alert flag (not by name).

    Returns (data_type_key, table_name) for the first data type with is_alert=1,
    or (None, None) if the model has no alert type. Type-agnostic — works for
    auto-generated keys like 'flightalertinfo' as well as any future alert-named
    pattern, since alertness is a flag set during config generation, not a
    reserved key.
    """
    row = conn.execute(
        "SELECT data_type_key, table_name FROM data_table_registry "
        "WHERE model_id=? AND is_alert=1 ORDER BY data_type_key LIMIT 1",
        (model_id,)
    ).fetchone()
    if not row:
        return None, None
    return row['data_type_key'], row['table_name']


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
    """Get display label, unit, scale_factor, and is_numeric for a column from the registry."""
    row = conn.execute(
        "SELECT display_label, unit, scale_factor, is_numeric FROM column_registry "
        "WHERE model_id=? AND data_type_key=? AND column_name=?",
        (model_id, dt_key, col_name)
    ).fetchone()
    if row:
        return (
            row['display_label'],
            row['unit'] or '',
            row['scale_factor'] or 1.0,
            bool(row['is_numeric']),
        )
    return col_name, '', 1.0, True


# ── Column listing ──

def get_columns_for_flight_api(flight_id):
    """Return available columns for a flight, grouped by data type.

    Delegates to format_configs.get_columns_for_flight.
    """
    conn = get_db()
    result = get_columns_for_flight(conn, flight_id)
    conn.close()
    return result


def _get_table_stats(conn, table_name, flight_id):
    """Return row count and time span for a flight table."""
    row = conn.execute(
        f"""SELECT COUNT(*) as row_count,
                  MIN(time_sec) as start_sec,
                  MAX(time_sec) as end_sec,
                  MIN(time_str) as start_str,
                  MAX(time_str) as end_str
           FROM {table_name} WHERE flight_id=?""",
        (flight_id,)
    ).fetchone()
    row_count = row['row_count'] if row else 0
    start_sec = row['start_sec'] if row else None
    end_sec = row['end_sec'] if row else None
    duration = (end_sec - start_sec) if start_sec is not None and end_sec is not None else 0
    return {
        'row_count': row_count,
        'start_sec': start_sec,
        'end_sec': end_sec,
        'start_str': row['start_str'] if row else None,
        'end_str': row['end_str'] if row else None,
        'duration_sec': duration,
    }


def _get_available_data_tables(conn, model_id, flight_id):
    """Return registered data tables that actually contain rows for this flight."""
    rows = conn.execute(
        """SELECT data_type_key, display_label, table_name, is_alert
           FROM data_table_registry WHERE model_id=? ORDER BY data_type_key""",
        (model_id,)
    ).fetchall()

    refs = []
    for row in rows:
        stats = _get_table_stats(conn, row['table_name'], flight_id)
        if stats['row_count'] == 0:
            continue
        refs.append({
            'data_type_key': row['data_type_key'],
            'label': row['display_label'],
            'table_name': row['table_name'],
            'is_alert': bool(row['is_alert']),
            **stats,
        })
    return refs


def _nearest_table_data(table_data, table_times, ref_t, tolerance):
    """Return the row data nearest to ref_t within tolerance, or None.

    Uses binary search instead of a moving pointer so duplicate timestamps do
    not block progress for later reference times.
    """
    if not table_data:
        return None
    if ref_t < table_times[0] or ref_t > table_times[-1]:
        return None

    pos = bisect_left(table_times, ref_t)
    candidates = []
    if pos < len(table_data):
        candidates.append(pos)
    if pos > 0:
        candidates.append(pos - 1)

    best_idx = None
    best_delta = None
    for idx in candidates:
        delta = abs(table_times[idx] - ref_t)
        if best_delta is None or delta < best_delta:
            best_idx = idx
            best_delta = delta

    if best_idx is not None and best_delta is not None and best_delta <= tolerance:
        return table_data[best_idx][1]
    return None


def _table_time_shape(table_times):
    """Classify a table's timestamp structure without estimating Hz."""
    if not table_times:
        return {'high_rate': False, 'irregular': False}

    seconds = [int(math.floor(t)) for t in table_times]
    counts = Counter(seconds)
    occupied = sorted(counts)
    row_density = len(table_times) / max(1, len(occupied))
    has_gaps = any((occupied[i] - occupied[i - 1]) != 1 for i in range(1, len(occupied)))
    has_duplicates = any(count > 1 for count in counts.values())

    return {
        'high_rate': row_density >= 1.5,
        'irregular': has_gaps or has_duplicates,
    }


def _time_part(value):
    if not value:
        return None
    return str(value).strip().split()[-1]


def _time_offset_from_axis(time_str, axis_start_sec):
    sec = time_to_sec(time_str)
    if sec < axis_start_sec - 43200:
        sec += 86400
    return sec - axis_start_sec


def _generate_flight_grid(conn, flight_id, data_tables):
    """Generate a uniform 1-second grid covering all data in the flight."""
    flight = conn.execute(
        "SELECT start_time, end_time, duration_sec FROM flights WHERE id=?",
        (flight_id,),
    ).fetchone()

    start_parts = [r.get('start_str') for r in data_tables if r.get('start_str')]
    end_parts = [r.get('end_str') for r in data_tables if r.get('end_str')]
    if flight:
        flight_start = _time_part(flight['start_time'])
        flight_end = _time_part(flight['end_time'])
        if flight_start:
            start_parts.append(flight_start)
        if flight_end:
            end_parts.append(flight_end)

    if not start_parts or not end_parts:
        return [], [], None

    axis_start_sec = min(time_to_sec(t) for t in start_parts)
    end_secs = []
    for value in end_parts:
        sec = time_to_sec(value)
        if sec < axis_start_sec:
            sec += 86400
        end_secs.append(sec)
    axis_end_sec = max(end_secs)

    ref_secs = list(range(0, int(math.ceil(axis_end_sec - axis_start_sec)) + 1))
    times = [sec_to_time_str(axis_start_sec + s) for s in ref_secs]
    return ref_secs, times, axis_start_sec


# ── Aligned data ──

def _build_aligned_series(conn, model_id, flight_id, column_keys):
    """Build the shared 1-second grid and only the requested data series."""
    data_tables = _get_available_data_tables(conn, model_id, flight_id)
    ref_secs, times, axis_start_sec = _generate_flight_grid(conn, flight_id, data_tables)
    if not ref_secs or axis_start_sec is None:
        return {'times': [], 'ref_secs': [], 'series': {}}, None

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
        col_str = ', '.join(cols + ['time_str'])
        try:
            db_rows = conn.execute(
                f"SELECT {col_str} FROM {table_name} WHERE flight_id=? ORDER BY time_str, id",
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
            table_data.append((_time_offset_from_axis(row['time_str'], axis_start_sec), d))
        table_data.sort(key=lambda item: item[0])
        table_times = [t for t, _ in table_data]
        time_shape = _table_time_shape(table_times)

        # Align to uniform integer-second grid
        for col in cols:
            full_key = f"{dt_key}.{col}"
            label, unit, scale_factor, is_numeric = _get_column_info(conn, model_id, dt_key, col)
            values = []

            if time_shape['high_rate']:
                # Multi-row-per-second tables are downsampled to the first row
                # in each displayed second.
                row_idx = 0
                for s in ref_secs:
                    while row_idx < len(table_data) and table_data[row_idx][0] < s:
                        row_idx += 1
                    if row_idx < len(table_data) and table_data[row_idx][0] < s + 1:
                        values.append(table_data[row_idx][1].get(col))
                        while row_idx + 1 < len(table_data) and table_data[row_idx + 1][0] < s + 1:
                            row_idx += 1
                    else:
                        values.append(None)
            else:
                match_tolerance = 1.0 if time_shape['irregular'] else 0.05
                for s in ref_secs:
                    nearest = _nearest_table_data(
                        table_data,
                        table_times,
                        s,
                        match_tolerance,
                    )
                    values.append(nearest.get(col) if nearest else None)

            entry = {
                'label': label,
                'unit': unit,
                'scale_factor': scale_factor,
                'is_numeric': is_numeric,
                'table': table_name,
            }
            if is_numeric:
                entry['values'] = values
            else:
                # Text columns: return raw string values, skip chart rendering
                entry['values'] = []
                entry['text_values'] = values
            series[full_key] = entry

    return {
        'times': times,
        'ref_secs': ref_secs,
        'series': series,
    }, axis_start_sec


def get_aligned_data(flight_id, column_keys, filter_spec=None):
    """Align selected columns to the flight's unified 1-second time series.

    Args:
        flight_id: Flight ID
        column_keys: List of "data_type_key.column_name" strings
        filter_spec: Optional filter with {logic, conditions}

    Returns:
        {times, ref_secs, series: {key: {label, unit, table, values}}, alerts}
    """
    conn = get_db()
    model_id = _get_model_id(conn, flight_id)
    if model_id is None:
        conn.close()
        return {'times': [], 'series': {}, 'alerts': [], 'error': 'Flight not found'}

    result, axis_start_sec = _build_aligned_series(conn, model_id, flight_id, column_keys)
    if axis_start_sec is None:
        conn.close()
        return {'times': [], 'series': {}, 'alerts': []}

    # Get alerts — locate the alert data type by its is_alert flag
    # (type-agnostic; works for any alert-named file pattern).
    alert_dt_key, alert_table = _get_alert_data_type(conn, model_id)
    alerts = []
    if alert_table:
        # Read alert columns from column_registry (ordered by ordinal)
        alert_col_rows = conn.execute(
            "SELECT column_name FROM column_registry "
            "WHERE model_id=? AND data_type_key=? AND ordinal IS NOT NULL "
            "ORDER BY ordinal",
            (model_id, alert_dt_key)
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
                'time_sec': _time_offset_from_axis(r['time_str'], axis_start_sec),
                'desc': str(r[desc_col]) if desc_col and r[desc_col] is not None else '',
                'extra': str(r[extra_col]) if extra_col and r[extra_col] is not None else '',
            } for r in alert_rows]

    conn.close()

    result['alerts'] = alerts

    if filter_spec:
        result = apply_filter(result, filter_spec)

    return result


def apply_filter(aligned, filter_spec, *, missing_is_false=False):
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
            if missing_is_false:
                masks.append([False] * n)
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


def _filter_conditions(filter_spec):
    if isinstance(filter_spec, dict):
        return filter_spec.get('conditions', [])
    return getattr(filter_spec, 'conditions', [])


def _validate_data_filter(conn, model_id, filter_spec):
    conditions = _filter_conditions(filter_spec)
    if not conditions:
        raise ValueError("At least one data filter condition is required")

    column_keys = []
    for condition in conditions:
        if isinstance(condition, dict):
            column = condition.get('column')
            op = condition.get('op')
            value = condition.get('value')
            min_val = condition.get('min_val')
            max_val = condition.get('max_val')
        else:
            column = condition.column
            op = condition.op
            value = condition.value
            min_val = condition.min_val
            max_val = condition.max_val

        if not column or '.' not in column:
            raise ValueError(f"Invalid data column: {column or ''}")
        data_type_key, column_name = column.split('.', 1)
        registered = conn.execute(
            """SELECT cr.is_numeric
               FROM column_registry cr
               JOIN data_table_registry dtr
                 ON dtr.model_id=cr.model_id AND dtr.data_type_key=cr.data_type_key
               WHERE cr.model_id=? AND cr.data_type_key=? AND cr.column_name=?""",
            (model_id, data_type_key, column_name),
        ).fetchone()
        if not registered:
            raise ValueError(f"Data column does not belong to model {model_id}: {column}")
        if not registered['is_numeric']:
            raise ValueError(f"Data column is not numeric: {column}")
        if op == 'between':
            if min_val is None or max_val is None:
                raise ValueError(f"Range filter requires both bounds: {column}")
            if min_val > max_val:
                raise ValueError(f"Range filter minimum exceeds maximum: {column}")
        elif value is None:
            raise ValueError(f"Filter value is required: {column}")
        column_keys.append(column)

    return list(dict.fromkeys(column_keys))


def match_flights_by_data(model_id, flight_ids, filter_spec):
    """Return flights having at least one aligned second matching the filter."""
    unique_flight_ids = list(dict.fromkeys(flight_ids))
    if not unique_flight_ids:
        return []

    conn = get_db()
    try:
        valid_ids = set()
        for offset in range(0, len(unique_flight_ids), 900):
            batch = unique_flight_ids[offset:offset + 900]
            placeholders = ','.join('?' for _ in batch)
            rows = conn.execute(
                f"""SELECT f.id
                    FROM flights f
                    JOIN aircraft a ON a.id=f.aircraft_id
                    WHERE a.model_id=? AND f.id IN ({placeholders})""",
                [model_id, *batch],
            ).fetchall()
            valid_ids.update(row['id'] for row in rows)
        invalid_ids = [flight_id for flight_id in unique_flight_ids if flight_id not in valid_ids]
        if invalid_ids:
            raise ValueError(f"Flights do not belong to model {model_id}: {invalid_ids}")

        column_keys = _validate_data_filter(conn, model_id, filter_spec)
        matching = []
        for flight_id in unique_flight_ids:
            aligned, _ = _build_aligned_series(conn, model_id, flight_id, column_keys)
            apply_filter(aligned, filter_spec, missing_is_false=True)
            if any(aligned.get('mask', [])):
                matching.append(flight_id)
        return matching
    finally:
        conn.close()


# ── Flight stats ──

def get_flight_stats(flight_id):
    """Compute summary statistics for a flight.

    Returns only flight-level metadata (duration, times, drone id, name).
    Domain-specific stats (max altitude / speed / rpm / fuel / battery / alert
    count) were removed: they depended on hardcoded data-type keys and column
    names that no longer exist under the uniform, type-agnostic config model.
    The alert count is still available per-flight via the /alerts endpoint.
    """
    conn = get_db()

    flight = conn.execute("SELECT * FROM flights WHERE id=?", (flight_id,)).fetchone()
    if not flight:
        conn.close()
        return {}

    ac = conn.execute(
        "SELECT name FROM aircraft WHERE id=?",
        (flight['aircraft_id'],)
    ).fetchone()

    stats = {
        'duration_sec': flight['duration_sec'],
        'start_time': flight['start_time'],
        'end_time': flight['end_time'],
        'drone_id': ac['name'] if ac else '',
        'name': flight['name'],
    }

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
            "SELECT f.name, a.name as aircraft_name, f.duration_sec "
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
            'name': f"{flight['name']} UAV{flight['aircraft_name']}",
            'times_sec': ref_secs,
            'values': entry['values'],
            'label': entry['label'],
            'unit': entry['unit'],
        })
        conn.close()

    return results


def get_alerts(flight_id: int) -> dict:
    conn = get_db()
    try:
        model_id = _get_model_id(conn, flight_id)
        if model_id is None:
            return {"alerts": []}
        alert_dt_key, alert_table = _get_alert_data_type(conn, model_id)
        if not alert_table:
            return {"alerts": []}
        alert_col_rows = conn.execute(
            "SELECT column_name FROM column_registry "
            "WHERE model_id=? AND data_type_key=? AND ordinal IS NOT NULL ORDER BY ordinal",
            (model_id, alert_dt_key),
        ).fetchall()
        if not alert_col_rows:
            return {"alerts": []}
        col_names = [row["column_name"] for row in alert_col_rows]
        try:
            rows = conn.execute(
                f"SELECT time_str, time_sec, {', '.join(col_names)} "
                f"FROM {alert_table} WHERE flight_id=? ORDER BY time_sec",
                (flight_id,),
            ).fetchall()
        except Exception:
            return {"alerts": []}
        data_tables = _get_available_data_tables(conn, model_id, flight_id)
        _, _, axis_start_sec = _generate_flight_grid(conn, flight_id, data_tables)
        desc_col = next((col for col in col_names if "desc" in col.lower()), col_names[0] if col_names else None)
        extra_cols = [col for col in col_names if "extra" in col.lower()]
        extra_col = extra_cols[0] if extra_cols else (col_names[-1] if len(col_names) > 1 else None)
        return {"alerts": [{
            "time_str": row["time_str"],
            "time_sec": _time_offset_from_axis(row["time_str"], axis_start_sec) if axis_start_sec is not None else row["time_sec"],
            "desc": str(row[desc_col]) if desc_col and row[desc_col] is not None else "",
            "extra": str(row[extra_col]) if extra_col and row[extra_col] is not None else "",
        } for row in rows]}
    finally:
        conn.close()
