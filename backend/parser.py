"""TSV file parsing and data import pipeline.

Data files are space-delimited with fixed column order per data type.
Column names may be Chinese (GBK encoded) or English, making name-based
matching unreliable. We use positional indices for robustness.
"""

import os
import logging
from backend.database import get_db

logger = logging.getLogger(__name__)

ENCODINGS = ['gbk', 'gb2312', 'utf-8', 'latin-1']

FILE_PATTERNS = [
    ('GPSData',         'gps',          'gps_data'),
    ('IMUData',         'imu',          'imu_data'),
    ('DroneStateData',  'drone_state',  'drone_state_data'),
    ('PosData',         'pos',          'pos_data'),
    ('EngineData',      'engine',       'engine_data'),
    ('PowerBoxData',    'powerbox',     'powerbox_data'),
    ('DualAntennaData', 'dual_antenna', 'dual_antenna_data'),
    ('FlightAlertInfo', 'alert',        'flight_alerts'),
]


def detect_encoding(filepath):
    for enc in ENCODINGS:
        try:
            with open(filepath, 'r', encoding=enc) as f:
                f.readline()
            return enc
        except (UnicodeDecodeError, UnicodeError):
            continue
    return 'latin-1'


def parse_lines(filepath):
    """Read file, return list of line strings."""
    encoding = detect_encoding(filepath)
    with open(filepath, 'r', encoding=encoding, errors='replace') as f:
        return [line.strip() for line in f.readlines() if line.strip()]


def time_to_sec(t_str):
    try:
        parts = t_str.strip().split(':')
        return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
    except (ValueError, IndexError):
        return 0.0


def _float(v):
    try: return float(v)
    except (ValueError, TypeError): return None


def _int(v):
    try: return int(float(v))
    except (ValueError, TypeError): return None


def _str(v):
    return v if v else None


def scan_folder(root_path):
    """Discover drone/data-type files."""
    results = []
    if not os.path.isdir(root_path):
        return results
    for entry in os.listdir(root_path):
        drone_dir = os.path.join(root_path, entry)
        if not os.path.isdir(drone_dir):
            continue
        drone_id = entry
        parser_dir = os.path.join(drone_dir, 'ParserData')
        if os.path.isdir(parser_dir):
            for fname in os.listdir(parser_dir):
                filepath = os.path.join(parser_dir, fname)
                if not fname.endswith('.txt'):
                    continue
                for pattern, type_key, table in FILE_PATTERNS:
                    if pattern in fname:
                        results.append({
                            'drone_id': drone_id, 'data_type_key': type_key,
                            'db_table': table, 'filepath': filepath,
                            'is_alert': False, 'filename': fname,
                        })
                        break
        alert_dir = os.path.join(drone_dir, 'FlightAlertInfo')
        if os.path.isdir(alert_dir):
            for fname in os.listdir(alert_dir):
                if fname.endswith('.txt'):
                    results.append({
                        'drone_id': drone_id, 'data_type_key': 'alert',
                        'db_table': 'flight_alerts',
                        'filepath': os.path.join(alert_dir, fname),
                        'is_alert': True, 'filename': fname,
                    })
    return results


def _batch_insert(conn, sql, data, batch_size=1000):
    for i in range(0, len(data), batch_size):
        conn.executemany(sql, data[i:i + batch_size])
    conn.commit()


# ============================================================
# Import functions — use POSITIONAL indices, not column names
# ============================================================

def import_gps(conn, flight_id, filepath):
    """GPS: 22 columns (0=Time, 1=UAVSendID, 2-4=vel, 5-7=NavA, 8-10=GPSBVel, 11-13=NavB, 14=pos_acc, 15=vel_acc, 16=pressure, 17=pressure_alt, 18=heading, 19=baseline?, 20=baseline?, 21=freq)"""
    lines = parse_lines(filepath)
    if len(lines) < 2: return 0
    base_sec = time_to_sec(lines[1].split()[0])
    sql = """INSERT INTO gps_data (flight_id, time_str, time_sec, north_vel, east_vel, down_vel,
        nava_lat, nava_lng, nava_alt, gpsb_vel_n, gpsb_vel_e, gpsb_vel_d,
        navb_lat, navb_lng, navb_alt, pos_accuracy, vel_accuracy, pressure,
        pressure_alt, heading, baseline_len, update_freq)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"""
    data = []
    for line in lines[1:]:
        p = line.split()
        if len(p) < 22: continue
        t = time_to_sec(p[0]) - base_sec
        data.append((flight_id, p[0], t, _float(p[2]), _float(p[3]), _float(p[4]),
            _float(p[5]), _float(p[6]), _float(p[7]), _float(p[8]), _float(p[9]), _float(p[10]),
            _float(p[11]), _float(p[12]), _float(p[13]), _float(p[14]), _float(p[15]),
            _float(p[16]), _float(p[17]), _float(p[18]), _float(p[19]), _float(p[21])))
    _batch_insert(conn, sql, data)
    return len(data)


def import_imu(conn, flight_id, filepath):
    """IMU: 23 cols (0=Time, 1=UAV, 2-4=NavA_RPY, 5-7=V, 8-10=A, 11-13=NavB_RPY, 14-16=V, 17-19=A, 20-22=Extremum)"""
    lines = parse_lines(filepath)
    if len(lines) < 2: return 0
    base_sec = time_to_sec(lines[1].split()[0])
    sql = """INSERT INTO imu_data (flight_id, time_str, time_sec, nava_roll, nava_pitch, nava_yaw,
        vx, vy, vz, ax, ay, az, navb_roll, navb_pitch, navb_yaw,
        navb_vx, navb_vy, navb_vz, navb_ax, navb_ay, navb_az,
        ax_extremum, ay_extremum, az_extremum)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"""
    data = []
    for line in lines[1:]:
        p = line.split()
        if len(p) < 23: continue
        t = time_to_sec(p[0]) - base_sec
        data.append((flight_id, p[0], t,
            _float(p[2]), _float(p[3]), _float(p[4]),
            _float(p[5]), _float(p[6]), _float(p[7]),
            _float(p[8]), _float(p[9]), _float(p[10]),
            _float(p[11]), _float(p[12]), _float(p[13]),
            _float(p[14]), _float(p[15]), _float(p[16]),
            _float(p[17]), _float(p[18]), _float(p[19]),
            _float(p[20]), _float(p[21]), _float(p[22])))
    _batch_insert(conn, sql, data)
    return len(data)


def import_drone_state(conn, flight_id, filepath):
    """DroneState: 28 cols (0=Time, 1=UAV, 2=UpLink, 3=DownLink, 4-6=RPY, 7-9=Vel, 10-12=TargetVel, 13=TargetYaw, 14=TargetPitch, 15=YawRate, 16=TargetYawRate, 17=TargetAlt, 18=Battery%, 19=ServoBatt%, 20=???, 21=???, 22=LinkQuality, 23=LinkSwitches, 24=???, 25=???, 26=FlightTime, 27=RemainingTime)"""
    lines = parse_lines(filepath)
    if len(lines) < 2: return 0
    base_sec = time_to_sec(lines[1].split()[0])
    sql = """INSERT INTO drone_state_data (flight_id, time_str, time_sec,
        uplink_type, downlink_type, roll, pitch, yaw,
        fwd_vel, side_vel, down_vel, fwd_target_vel, side_target_vel, down_target_vel,
        target_yaw, target_pitch, yaw_rate, target_yaw_rate, target_alt,
        battery_pct, servo_battery, link_quality, link_switches,
        flight_mode, flight_time, remaining_time)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"""
    data = []
    for line in lines[1:]:
        p = line.split()
        if len(p) < 28: continue
        t = time_to_sec(p[0]) - base_sec
        data.append((flight_id, p[0], t,
            p[2], p[3],
            _float(p[4]), _float(p[5]), _float(p[6]),
            _float(p[7]), _float(p[8]), _float(p[9]),
            _float(p[10]), _float(p[11]), _float(p[12]),
            _float(p[13]), _float(p[14]),
            _float(p[15]), _float(p[16]), _float(p[17]),
            _float(p[18]), _float(p[19]),
            _float(p[22]), _int(p[23]),
            _str(p[24]) if len(p) > 24 else None,
            _float(p[26]) if len(p) > 26 else None,
            _float(p[27]) if len(p) > 27 else None))
    _batch_insert(conn, sql, data)
    return len(data)


def import_pos(conn, flight_id, filepath):
    """PosData: 35+ cols (0=Time, 1=UAV, 2=NorthPos, 3=EastPos, 4=RelAlt, 5=Lng, 6=Lat, 7=AltAMSL, 8=HomeDist, 9=FCSVoltage, 10-15=Online flags, 16-21=Servo online, 22=FlightMode, 23=Recorder, 24=TargetRoute, 25=CrossTrackErr, 26=???, 27=NavAState, 28=NavBFront, 29=NavBBack, 30=NavAOK, 31=???, 32=GPSState, 33=NavAFault, 34=NavBFault, 35=NavBState, 36-39=GPS time+pdop, 40-42=PreWarn)"""
    lines = parse_lines(filepath)
    if len(lines) < 2: return 0
    base_sec = time_to_sec(lines[1].split()[0])
    sql = """INSERT INTO pos_data (flight_id, time_str, time_sec,
        north_pos, east_pos, rel_alt, lng, lat, alt_amsl, home_dist, fcs_voltage,
        is_nava_online, is_navb_online, is_gcs_online,
        is_powerbox_online, is_ecu_online, is_tcu_online,
        is_servo1_online, is_servo2_online, is_servo3_online,
        is_servo4_online, is_servo5_online, is_servo6_online,
        flight_mode, recorder_state, target_route, cross_track_err,
        navia_state, navib_state, gps_time_h, gps_time_m, gps_time_s, pdop,
        prewarn_pos, prewarn_boundary)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"""
    data = []
    for line in lines[1:]:
        p = line.split()
        if len(p) < 30: continue
        t = time_to_sec(p[0]) - base_sec
        data.append((flight_id, p[0], t,
            _float(p[2]), _float(p[3]), _float(p[4]),
            _float(p[5]), _float(p[6]), _float(p[7]),
            _float(p[8]), _float(p[9]),
            _int(p[10]) if p[10] == 'True' else 0,
            _int(p[11]) if p[11] == 'True' else 0,
            _int(p[12]) if len(p) > 12 and p[12] == 'True' else 0,
            1 if len(p) > 13 and p[13] == 'True' else 0,
            1 if len(p) > 14 and p[14] == 'True' else 0,
            1 if len(p) > 15 and p[15] == 'True' else 0,
            1 if len(p) > 16 and p[16] == 'True' else 0,
            1 if len(p) > 17 and p[17] == 'True' else 0,
            1 if len(p) > 18 and p[18] == 'True' else 0,
            1 if len(p) > 19 and p[19] == 'True' else 0,
            1 if len(p) > 20 and p[20] == 'True' else 0,
            1 if len(p) > 21 and p[21] == 'True' else 0,
            p[22] if len(p) > 22 else None,
            p[23] if len(p) > 23 else None,
            _int(p[24]) if len(p) > 24 else None,
            _float(p[25]) if len(p) > 25 else None,
            p[27] if len(p) > 27 else None,
            p[30] if len(p) > 30 else None,
            _int(p[36]) if len(p) > 36 else None,
            _int(p[37]) if len(p) > 37 else None,
            _int(p[38]) if len(p) > 38 else None,
            _float(p[39]) if len(p) > 39 else None,
            _int(p[40]) if len(p) > 40 else None,
            _int(p[41]) if len(p) > 41 else None))
    _batch_insert(conn, sql, data)
    return len(data)


def import_engine(conn, flight_id, filepath):
    """Engine: 21 cols (0=Time, 1=UAV, 2=CylHeadTemp, 3-4=ExhaustTemp, 5=EngineTemp, 6-9=IntakeTemp, 10=ManifoldPress, 11=FuelRemaining, 12=BatteryVoltage, 13=???, 14=RPM, 15=Throttle%, 16=ManifoldPress2, 17=???, 18=TCUTemp, 19=???, 20=TCUManifold)"""
    lines = parse_lines(filepath)
    if len(lines) < 2: return 0
    base_sec = time_to_sec(lines[1].split()[0])
    sql = """INSERT INTO engine_data (flight_id, time_str, time_sec,
        cyl_head_temp, exhaust_temp_1, exhaust_temp_2, engine_temp,
        intake_temp_1, intake_temp_2, intake_temp_3, intake_temp_4,
        rpm, throttle, manifold_press, fuel_remaining, battery_voltage,
        tcu_temp, tcu_manifold)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"""
    data = []
    for line in lines[1:]:
        p = line.split()
        if len(p) < 21: continue
        t = time_to_sec(p[0]) - base_sec
        data.append((flight_id, p[0], t,
            _float(p[2]), _float(p[3]), _float(p[4]),
            _float(p[5]), _float(p[6]), _float(p[7]), _float(p[8]), _float(p[9]),
            _float(p[14]), _float(p[15]), _float(p[10]),
            _float(p[11]), _float(p[12]),
            _float(p[18]), _float(p[20])))
    _batch_insert(conn, sql, data)
    return len(data)


def import_powerbox(conn, flight_id, filepath):
    """PowerBox: 13 cols (0=Time, 1=UAV, 2-5=Voltages, 6-9=Currents, 10=V12, 11=V28, 12=ServoCurrentAlt)"""
    lines = parse_lines(filepath)
    if len(lines) < 2: return 0
    base_sec = time_to_sec(lines[1].split()[0])
    sql = """INSERT INTO powerbox_data (flight_id, time_str, time_sec,
        fcs_voltage, servo_voltage, rcvr_voltage, battery_voltage,
        fcs_current, servo_current, rcvr_current, battery_current,
        v12, v28, servo_current_alt)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)"""
    data = []
    for line in lines[1:]:
        p = line.split()
        if len(p) < 13: continue
        t = time_to_sec(p[0]) - base_sec
        data.append((flight_id, p[0], t,
            _float(p[2]), _float(p[3]), _float(p[4]), _float(p[5]),
            _float(p[6]), _float(p[7]), _float(p[8]), _float(p[9]),
            _float(p[10]), _float(p[11]), _float(p[12])))
    _batch_insert(conn, sql, data)
    return len(data)


def import_dual_antenna(conn, flight_id, filepath):
    """DualAntenna: 23 cols"""
    lines = parse_lines(filepath)
    if len(lines) < 2: return 0
    base_sec = time_to_sec(lines[1].split()[0])
    sql = """INSERT INTO dual_antenna_data (flight_id, time_str, time_sec,
        gps_pos_type, radio_pos_flag, pa_alt_flag, mag_heading_flag, gps_data_flag,
        pdop_diff, hdop_diff, sat_num_diff,
        pos_update_rate, vel_update_rate, state_update_rate,
        pressure, pressure_2, vel, pressure_rate, pressure2_rate,
        pressure_temp, pressure2_temp, pressure_alt_sensor, pressure_alt_comp)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"""
    data = []
    for line in lines[1:]:
        p = line.split()
        if len(p) < 23: continue
        t = time_to_sec(p[0]) - base_sec
        data.append((flight_id, p[0], t,
            p[2], p[3], p[4], p[5], p[6],
            _float(p[7]), _float(p[8]), _int(p[9]),
            _float(p[10]), _float(p[11]), _float(p[12]),
            _float(p[13]), _float(p[14]), _float(p[15]),
            _float(p[16]), _float(p[17]),
            _float(p[18]), _float(p[19]),
            p[20] if len(p) > 20 else None,
            p[21] if len(p) > 21 else None))
    _batch_insert(conn, sql, data)
    return len(data)


def import_alerts(conn, flight_id, filepath):
    """Flight Alert: 5 cols space-delimited (0=Time, 1=UAV, 2=Model, 3=Desc..., 4=ExtraValue)"""
    lines = parse_lines(filepath)
    if not lines: return 0
    base_sec = time_to_sec(lines[0].split()[0])
    sql = """INSERT INTO flight_alerts (flight_id, time_str, time_sec, uav_id, drone_model, alert_desc, extra_value)
        VALUES (?,?,?,?,?,?,?)"""
    data = []
    for line in lines:
        p = line.split()
        if len(p) < 3: continue
        t_str = p[0]
        uav = p[1]
        model = p[2]
        # Description may contain spaces - it's everything between model and the last numeric field
        if len(p) >= 5:
            desc = ' '.join(p[3:-1])
            extra = p[-1]
        elif len(p) == 4:
            desc = p[3]
            extra = None
        else:
            desc = None
            extra = None
        data.append((flight_id, t_str, time_to_sec(t_str) - base_sec, uav, model, desc, extra))
    _batch_insert(conn, sql, data)
    return len(data)


IMPORTERS = {
    'gps': import_gps,
    'imu': import_imu,
    'drone_state': import_drone_state,
    'pos': import_pos,
    'engine': import_engine,
    'powerbox': import_powerbox,
    'dual_antenna': import_dual_antenna,
    'alert': import_alerts,
}


def import_flight(source_path):
    """Import an entire flight folder."""
    files = scan_folder(source_path)
    if not files:
        return {'error': 'No drone data files found in folder'}

    conn = get_db()
    from collections import defaultdict
    by_drone = defaultdict(list)
    for f in files:
        by_drone[f['drone_id']].append(f)

    imported = []
    for drone_id, drone_files in by_drone.items():
        folder_name = os.path.basename(source_path.rstrip('/\\'))
        flight_date = None
        if len(folder_name) >= 8:
            ds = folder_name[:8]
            flight_date = f"{ds[:4]}-{ds[4:6]}-{ds[6:8]}"

        cur = conn.execute("SELECT id FROM flights WHERE source_path=? AND drone_id=?", (source_path, drone_id))
        if cur.fetchone():
            conn.execute("DELETE FROM flights WHERE source_path=? AND drone_id=?", (source_path, drone_id))
            conn.commit()

        conn.execute(
            "INSERT INTO flights (name, drone_id, drone_model, source_path, flight_date) VALUES (?, ?, 'CR500A', ?, ?)",
            (folder_name, drone_id, source_path, flight_date)
        )
        flight_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

        total_rows = 0
        details = {}
        for f in drone_files:
            importer = IMPORTERS.get(f['data_type_key'])
            if importer:
                try:
                    count = importer(conn, flight_id, f['filepath'])
                    details[f['data_type_key']] = count
                    total_rows += count
                except Exception as e:
                    details[f['data_type_key']] = f"Error: {e}"
                    logger.error(f"Import error {f['filepath']}: {e}")

        _update_flight_meta(conn, flight_id, total_rows)
        imported.append({
            'flight_id': flight_id, 'drone_id': drone_id,
            'name': folder_name, 'rows': total_rows, 'details': details,
        })

    conn.close()
    return {'imported': imported}


def _update_flight_meta(conn, flight_id, total_rows):
    time_info = conn.execute(
        "SELECT MIN(time_sec) as start_sec, MAX(time_sec) as end_sec, "
        "MIN(time_str) as start_str, MAX(time_str) as end_str "
        "FROM gps_data WHERE flight_id=?", (flight_id,)
    ).fetchone()
    if time_info and time_info['end_sec'] is not None:
        duration = time_info['end_sec'] - time_info['start_sec']
        conn.execute(
            "UPDATE flights SET start_time=?, end_time=?, duration_sec=?, total_rows=? WHERE id=?",
            (time_info['start_str'], time_info['end_str'], duration, total_rows, flight_id)
        )
    else:
        conn.execute("UPDATE flights SET total_rows=? WHERE id=?", (total_rows, flight_id))
    conn.commit()
