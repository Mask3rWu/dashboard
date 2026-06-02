"""Time alignment, statistics, correlation, and anomaly detection."""

import math
from backend.database import get_db

# All data tables and their known numeric columns with display labels
DATA_TABLES = {
    'gps_data': {
        'label': 'GPS数据',
        'columns': {
            'north_vel': '北向速度', 'east_vel': '东向速度', 'down_vel': '地向速度',
            'nava_lat': '纬度(A)', 'nava_lng': '经度(A)', 'nava_alt': '高度(A)',
            'navb_lat': '纬度(B)', 'navb_lng': '经度(B)', 'navb_alt': '高度(B)',
            'gpsb_vel_n': 'GPSB北速', 'gpsb_vel_e': 'GPSB东速', 'gpsb_vel_d': 'GPSB地速',
            'pos_accuracy': '位置精度', 'vel_accuracy': '速度精度',
            'pressure': '气压', 'pressure_alt': '气压高度',
            'heading': '航向角', 'baseline_len': '天线基线长度', 'update_freq': '更新频率',
        },
        'units': {
            'north_vel': 'm/s', 'east_vel': 'm/s', 'down_vel': 'm/s',
            'nava_lat': '°', 'nava_lng': '°', 'nava_alt': 'm',
            'navb_lat': '°', 'navb_lng': '°', 'navb_alt': 'm',
            'gpsb_vel_n': 'm/s', 'gpsb_vel_e': 'm/s', 'gpsb_vel_d': 'm/s',
            'pos_accuracy': '', 'vel_accuracy': '',
            'pressure': 'Pa', 'pressure_alt': 'm',
            'heading': '°', 'baseline_len': 'mm', 'update_freq': 'Hz',
        }
    },
    'imu_data': {
        'label': 'IMU数据',
        'columns': {
            'nava_roll': '横滚(A)', 'nava_pitch': '俯仰(A)', 'nava_yaw': '偏航(A)',
            'vx': 'Vx', 'vy': 'Vy', 'vz': 'Vz',
            'ax': 'Ax', 'ay': 'Ay', 'az': 'Az',
            'navb_roll': '横滚(B)', 'navb_pitch': '俯仰(B)', 'navb_yaw': '偏航(B)',
            'navb_vx': 'Vx(B)', 'navb_vy': 'Vy(B)', 'navb_vz': 'Vz(B)',
            'navb_ax': 'Ax(B)', 'navb_ay': 'Ay(B)', 'navb_az': 'Az(B)',
            'ax_extremum': 'Ax极值', 'ay_extremum': 'Ay极值', 'az_extremum': 'Az极值',
        },
        'units': {
            'nava_roll': '°', 'nava_pitch': '°', 'nava_yaw': '°',
            'vx': 'm/s', 'vy': 'm/s', 'vz': 'm/s',
            'ax': 'm/s²', 'ay': 'm/s²', 'az': 'm/s²',
            'navb_roll': '°', 'navb_pitch': '°', 'navb_yaw': '°',
            'navb_vx': 'm/s', 'navb_vy': 'm/s', 'navb_vz': 'm/s',
            'navb_ax': 'm/s²', 'navb_ay': 'm/s²', 'navb_az': 'm/s²',
            'ax_extremum': 'm/s²', 'ay_extremum': 'm/s²', 'az_extremum': 'm/s²',
        }
    },
    'drone_state_data': {
        'label': '飞控状态',
        'columns': {
            'roll': '横滚角', 'pitch': '俯仰角', 'yaw': '航向角',
            'fwd_vel': '前向速度', 'side_vel': '侧向速度', 'down_vel': '地向速度',
            'fwd_target_vel': '目标前速', 'side_target_vel': '目标侧速', 'down_target_vel': '目标地速',
            'target_yaw': '目标偏航', 'target_pitch': '目标俯仰',
            'yaw_rate': '偏航角速度', 'target_yaw_rate': '目标偏航速', 'target_alt': '目标高度',
            'battery_pct': '电量', 'servo_battery': '舵机电量',
            'link_quality': '链路质量', 'link_switches': '链路切换次数',
            'flight_time': '飞行时长', 'remaining_time': '剩余时间',
        },
        'units': {
            'roll': '°', 'pitch': '°', 'yaw': '°',
            'fwd_vel': 'm/s', 'side_vel': 'm/s', 'down_vel': 'm/s',
            'fwd_target_vel': 'm/s', 'side_target_vel': 'm/s', 'down_target_vel': 'm/s',
            'target_yaw': '°', 'target_pitch': '°',
            'yaw_rate': '°/s', 'target_yaw_rate': '°/s', 'target_alt': 'm',
            'battery_pct': '%', 'servo_battery': '%',
            'link_quality': '%', 'link_switches': '次',
            'flight_time': 'min', 'remaining_time': 'min',
        }
    },
    'pos_data': {
        'label': '位置数据',
        'columns': {
            'north_pos': '北向位置', 'east_pos': '东向位置',
            'rel_alt': '相对高度', 'lng': '经度', 'lat': '纬度', 'alt_amsl': '海拔高度',
            'home_dist': 'Home距离', 'fcs_voltage': '飞控电压',
            'target_route': '目标航线', 'cross_track_err': '偏航距',
            'gps_time_h': 'GPS时', 'gps_time_m': 'GPS分', 'gps_time_s': 'GPS秒',
            'pdop': 'PDOP',
        },
        'units': {
            'north_pos': 'm', 'east_pos': 'm',
            'rel_alt': 'm', 'lng': '°', 'lat': '°', 'alt_amsl': 'm',
            'home_dist': '', 'fcs_voltage': 'V',
            'target_route': '', 'cross_track_err': '',
            'gps_time_h': 'h', 'gps_time_m': 'm', 'gps_time_s': 's',
            'pdop': '',
        }
    },
    'engine_data': {
        'label': '发动机数据',
        'columns': {
            'cyl_head_temp': '缸头温度', 'exhaust_temp_1': '排气温度1', 'exhaust_temp_2': '排气温度2',
            'engine_temp': '发动机温度',
            'intake_temp_1': '进气温度1', 'intake_temp_2': '进气温度2',
            'intake_temp_3': '进气温度3', 'intake_temp_4': '进气温度4',
            'rpm': '发动机转速', 'throttle': '节气门开度',
            'manifold_press': '进气歧管压力', 'fuel_remaining': '剩余燃油',
            'battery_voltage': '电池电压', 'tcu_temp': 'TCU温度', 'tcu_manifold': 'TCU歧管压力',
        },
        'units': {
            'cyl_head_temp': '°C', 'exhaust_temp_1': '°C', 'exhaust_temp_2': '°C',
            'engine_temp': '°C',
            'intake_temp_1': '°C', 'intake_temp_2': '°C',
            'intake_temp_3': '°C', 'intake_temp_4': '°C',
            'rpm': 'RPM', 'throttle': '%',
            'manifold_press': 'mbar', 'fuel_remaining': 'L',
            'battery_voltage': 'V', 'tcu_temp': '°C', 'tcu_manifold': 'mbar',
        }
    },
    'powerbox_data': {
        'label': '电源数据',
        'columns': {
            'fcs_voltage': '飞控电压', 'servo_voltage': '舵机电压',
            'rcvr_voltage': '接收机电压', 'battery_voltage': '电池电压',
            'fcs_current': '飞控电流', 'servo_current': '舵机电流',
            'rcvr_current': '接收机电流', 'battery_current': '电池电流',
            'v12': '12V电压', 'v28': '28V电压', 'servo_current_alt': '舵机电流(备)',
        },
        'units': {
            'fcs_voltage': 'V', 'servo_voltage': 'V',
            'rcvr_voltage': 'V', 'battery_voltage': 'V',
            'fcs_current': 'mA', 'servo_current': 'mA',
            'rcvr_current': 'mA', 'battery_current': 'mA',
            'v12': 'V', 'v28': 'V', 'servo_current_alt': 'mA',
        }
    },
    'dual_antenna_data': {
        'label': '双天线差分',
        'columns': {
            'pdop_diff': 'PDOP差分', 'hdop_diff': 'HDOP差分', 'sat_num_diff': '卫星数',
            'pos_update_rate': '位置更新率', 'vel_update_rate': '速度更新率', 'state_update_rate': '状态更新率',
            'pressure': '气压', 'pressure_2': '气压2', 'vel': '速度',
            'pressure_rate': '气压更新率', 'pressure2_rate': '气压2更新率',
            'pressure_temp': '气压温度', 'pressure2_temp': '气压2温度',
        },
        'units': {
            'pdop_diff': '', 'hdop_diff': '', 'sat_num_diff': '',
            'pos_update_rate': 'Hz', 'vel_update_rate': 'Hz', 'state_update_rate': 'Hz',
            'pressure': 'Pa', 'pressure_2': 'Pa', 'vel': 'm/s',
            'pressure_rate': 'Hz', 'pressure2_rate': 'Hz',
            'pressure_temp': '°C', 'pressure2_temp': '°C',
        }
    },
}


def get_columns_for_flight(flight_id):
    """Return all available columns for a flight, grouped by data type."""
    conn = get_db()
    result = []
    for table_name, meta in DATA_TABLES.items():
        # Check if this table has data for this flight
        cur = conn.execute(
            f"SELECT COUNT(*) as cnt FROM {table_name} WHERE flight_id=? LIMIT 1",
            (flight_id,)
        )
        row = cur.fetchone()
        if not row or row['cnt'] == 0:
            continue
        columns = []
        for col_key, col_label in meta['columns'].items():
            unit = meta['units'].get(col_key, '')
            columns.append({
                'key': f"{table_name}.{col_key}",
                'label': col_label,
                'unit': unit,
            })
        if columns:
            result.append({
                'table': table_name,
                'label': meta['label'],
                'columns': columns,
            })
    conn.close()
    return result


def get_aligned_data(flight_id, column_keys, ref_table='gps_data', tolerance=0.5, filter_spec=None):
    """Align selected columns to a reference time series.

    Args:
        flight_id: Flight ID
        column_keys: List of "table.column" strings
        ref_table: Reference table for time base
        tolerance: Max time difference for nearest-neighbor matching
        filter_spec: Optional filter with {logic, conditions} to apply after alignment
        tolerance: Max time difference for nearest-neighbor match (seconds)

    Returns:
        {times: [...], series: {key: {label, unit, values: [...]}}, alerts: [...]}
    """
    conn = get_db()

    # Get reference times
    ref_rows = conn.execute(
        f"SELECT time_str, time_sec FROM {ref_table} WHERE flight_id=? ORDER BY time_sec",
        (flight_id,)
    ).fetchall()

    if not ref_rows:
        # Try gps_data as fallback
        ref_rows = conn.execute(
            "SELECT time_str, time_sec FROM gps_data WHERE flight_id=? ORDER BY time_sec",
            (flight_id,)
        ).fetchall()

    if not ref_rows:
        conn.close()
        return {'times': [], 'series': {}, 'alerts': []}

    times = [r['time_str'] for r in ref_rows]
    ref_secs = [r['time_sec'] for r in ref_rows]

    # Group column_keys by table
    from collections import defaultdict
    by_table = defaultdict(list)
    for key in column_keys:
        if '.' in key:
            table, col = key.split('.', 1)
            by_table[table].append(col)

    # Fetch data for each table and align
    series = {}
    for table, cols in by_table.items():
        # Get meta for labeling
        meta = DATA_TABLES.get(table, {})
        col_labels = meta.get('columns', {})
        col_units = meta.get('units', {})

        # Fetch all data for this table
        col_str = ', '.join(cols + ['time_sec'])
        db_rows = conn.execute(
            f"SELECT {col_str} FROM {table} WHERE flight_id=? ORDER BY time_sec",
            (flight_id,)
        ).fetchall()

        if not db_rows:
            continue

        # Build lookup: sorted list of (time_sec, {col: val})
        table_data = []
        for row in db_rows:
            d = {c: row[c] for c in cols}
            table_data.append((row['time_sec'], d))

        # Align to reference times
        for col in cols:
            full_key = f"{table}.{col}"
            label = col_labels.get(col, col)
            unit = col_units.get(col, '')
            values = []

            ti = 0
            for ref_t in ref_secs:
                # Advance to nearest point: skip duplicates and move to closer match
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
                'table': table,
                'values': values,
            }

    # Get alerts
    alert_rows = conn.execute(
        "SELECT time_str, time_sec, alert_desc, extra_value FROM flight_alerts "
        "WHERE flight_id=? ORDER BY time_sec",
        (flight_id,)
    ).fetchall()
    alerts = [{
        'time_str': r['time_str'], 'time_sec': r['time_sec'],
        'desc': r['alert_desc'], 'extra': r['extra_value'],
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
    """Compute filter mask and segments from aligned data.

    Does NOT modify series values — only adds mask[] and segments[] for
    the frontend to highlight matching regions.
    """
    n = len(aligned['times'])
    if n == 0:
        return aligned

    conditions = filter_spec.get('conditions', []) if isinstance(filter_spec, dict) else filter_spec.conditions
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


def get_flight_stats(flight_id):
    """Compute summary statistics for a flight."""
    conn = get_db()

    flight = conn.execute("SELECT * FROM flights WHERE id=?", (flight_id,)).fetchone()
    if not flight:
        conn.close()
        return {}

    stats = {
        'duration_sec': flight['duration_sec'],
        'start_time': flight['start_time'],
        'end_time': flight['end_time'],
        'drone_id': flight['drone_id'],
        'name': flight['name'],
    }

    # Max altitude from gps
    row = conn.execute(
        "SELECT MAX(nava_alt) as max_alt, MAX(heading) as max_heading FROM gps_data WHERE flight_id=?",
        (flight_id,)
    ).fetchone()
    stats['max_altitude'] = row['max_alt']

    # Max speed from drone_state
    row = conn.execute(
        "SELECT MAX(ABS(fwd_vel)) as max_fwd FROM drone_state_data WHERE flight_id=?",
        (flight_id,)
    ).fetchone()
    stats['max_speed'] = row['max_fwd']

    # Engine stats
    row = conn.execute(
        "SELECT AVG(rpm) as avg_rpm, MAX(rpm) as max_rpm, "
        "MIN(fuel_remaining) as min_fuel, MAX(fuel_remaining) as max_fuel "
        "FROM engine_data WHERE flight_id=?",
        (flight_id,)
    ).fetchone()
    stats['avg_rpm'] = round(row['avg_rpm'], 1) if row['avg_rpm'] else 0
    stats['max_rpm'] = row['max_rpm'] or 0
    stats['fuel_start'] = row['max_fuel']
    stats['fuel_end'] = row['min_fuel']

    # Battery
    row = conn.execute(
        "SELECT MIN(battery_pct) as min_bat, MAX(battery_pct) as max_bat "
        "FROM drone_state_data WHERE flight_id=?",
        (flight_id,)
    ).fetchone()
    stats['battery_start'] = row['max_bat']
    stats['battery_end'] = row['min_bat']

    # Alert count
    row = conn.execute(
        "SELECT COUNT(*) as cnt FROM flight_alerts WHERE flight_id=?",
        (flight_id,)
    ).fetchone()
    stats['alert_count'] = row['cnt']

    conn.close()
    return stats


def get_correlation(flight_id, column_keys):
    """Compute Pearson correlation matrix for selected columns."""
    aligned = get_aligned_data(flight_id, column_keys)
    series = aligned.get('series', {})
    if len(series) < 2:
        return {'columns': [], 'matrix': [], 'labels': []}

    # Extract values, filter rows where any column is None
    keys = [k for k in column_keys if k in series]
    labels = [series[k]['label'] for k in keys]
    n = len(keys)

    # Build aligned data matrix
    values_by_key = {k: series[k]['values'] for k in keys}
    n_points = len(aligned.get('times', []))

    # Filter to complete rows
    complete_rows = []
    for i in range(n_points):
        row = [values_by_key[k][i] for k in keys]
        if all(v is not None for v in row):
            complete_rows.append(row)

    if len(complete_rows) < 3:
        return {'columns': keys, 'labels': labels, 'matrix': [[1.0]*n for _ in range(n)]}

    # Compute correlation
    m = len(complete_rows)
    means = [sum(row[j] for row in complete_rows) / m for j in range(n)]

    matrix = []
    for i in range(n):
        row_vals = []
        for j in range(n):
            if i == j:
                row_vals.append(1.0)
            else:
                # Pearson r
                num = sum((complete_rows[k][i] - means[i]) * (complete_rows[k][j] - means[j]) for k in range(m))
                den_i = math.sqrt(sum((complete_rows[k][i] - means[i])**2 for k in range(m)))
                den_j = math.sqrt(sum((complete_rows[k][j] - means[j])**2 for k in range(m)))
                if den_i == 0 or den_j == 0:
                    row_vals.append(0.0)
                else:
                    row_vals.append(round(num / (den_i * den_j), 4))
        matrix.append(row_vals)

    return {'columns': keys, 'labels': labels, 'matrix': matrix}


def get_anomalies(flight_id, column_key, window_size=30, sigma=3.0):
    """Detect anomalies using sliding window z-score.

    Args:
        flight_id: Flight ID
        column_key: "table.column"
        window_size: Number of surrounding points for baseline
        sigma: Z-score threshold

    Returns:
        {times, values, anomaly_indices, upper_bound, lower_bound}
    """
    aligned = get_aligned_data(flight_id, [column_key])
    series = aligned.get('series', {}).get(column_key)
    if not series:
        return {'times': [], 'values': [], 'anomaly_indices': [], 'upper_bound': [], 'lower_bound': []}

    values = series['values']
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
        std = math.sqrt(sum((v - mean)**2 for v in window) / len(window))
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
        'label': series['label'],
        'unit': series['unit'],
    }


def get_compare(flight_ids, column_key):
    """Get aligned data for one column across multiple flights.
    Returns data normalized to 0-100% flight time for overlay comparison.
    """
    results = []
    for fid in flight_ids:
        conn = get_db()
        flight = conn.execute("SELECT name, drone_id, duration_sec FROM flights WHERE id=?", (fid,)).fetchone()
        if not flight:
            conn.close()
            continue

        aligned = get_aligned_data(fid, [column_key])
        series = aligned.get('series', {}).get(column_key)
        if not series:
            conn.close()
            continue

        # Normalize time to 0-100%
        duration = flight['duration_sec'] or 1
        ref_secs = aligned.get('ref_secs', [])
        pct_times = [(s / duration) * 100 for s in ref_secs]

        results.append({
            'flight_id': fid,
            'name': f"{flight['name']} UAV{flight['drone_id']}",
            'times_pct': pct_times,
            'values': series['values'],
            'label': series['label'],
            'unit': series['unit'],
        })
        conn.close()

    return results
