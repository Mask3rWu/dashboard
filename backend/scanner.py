"""Format auto-detection and per-format directory scanning."""

import os
import re
from backend.format_configs import load_format_config, get_data_type_key

ENCODINGS = ['gbk', 'gb2312', 'utf-8', 'latin-1']


def detect_encoding(filepath):
    """Detect text encoding of a file."""
    for enc in ENCODINGS:
        try:
            with open(filepath, 'r', encoding=enc) as f:
                f.readline()
            return enc
        except (UnicodeDecodeError, UnicodeError):
            continue
    return 'latin-1'


def has_header(filepath):
    """Detect if a TSV file has a header row (per-file).

    Returns True if the first token of the first line is 'Time'.
    Returns False if it matches HH:MM:SS (data row).
    """
    encoding = detect_encoding(filepath)
    try:
        with open(filepath, 'r', encoding=encoding, errors='replace') as f:
            first_line = f.readline().strip()
    except Exception:
        return False

    if not first_line:
        return False
    tokens = first_line.split()
    if not tokens:
        return False
    if tokens[0].strip().lower() == 'time':
        return True
    if re.match(r'^\d{1,2}:\d{2}:\d{2}', tokens[0]):
        return False
    # If neither, assume it's a header (could be another language for "Time")
    return True


def parse_lines(filepath):
    """Read file, return list of line strings (non-empty)."""
    encoding = detect_encoding(filepath)
    with open(filepath, 'r', encoding=encoding, errors='replace') as f:
        return [line.strip() for line in f.readlines() if line.strip()]


def detect_format(source_path):
    """Auto-detect the format category of a data folder.

    Detection rules (priority order):
    1. Find ParserData/ at depth ≤ 3 from source_path
       - If parent dir is 1-3 digit number → Format A
       - If parent dir is 8-digit date (YYYYMMDD) → Format B
    2. Find .jlog files → Format B
    3. Find .txt files directly at source_path root (no ParserData subdirs) → Format C
    4. Fallback: None (unknown)

    Returns: 'A' | 'B' | 'C' | None
    """
    if not os.path.isdir(source_path):
        return None

    # Check for ParserData at various depths
    for root, dirs, files in os.walk(source_path):
        depth = root[len(source_path):].count(os.sep)
        if depth > 3:
            continue

        if 'ParserData' in dirs:
            parent_name = os.path.basename(root)
            # Format B: parent is an 8-digit date
            if re.match(r'^\d{8}$', parent_name):
                return 'B'
            # Format A: any other directory with ParserData
            # (aircraft serial can be numbers, letters, Chinese, etc.)
            return 'A'

        # Check for .jlog files (Format B indicator)
        for f in files:
            if f.endswith('.jlog'):
                return 'B'

        # Check for flat .txt files (Format C indicator)
        has_txt = any(f.endswith('.txt') for f in files)
        if has_txt and depth == 0 and 'ParserData' not in dirs:
            # Also check one level deeper for confirmation
            pass

    # Final check: flat txt files at root
    for f in os.listdir(source_path):
        fp = os.path.join(source_path, f)
        if os.path.isfile(fp) and f.endswith('.txt'):
            # Check that we're not inside a structure with subdirs
            has_parser = False
            for sub in os.listdir(source_path):
                subp = os.path.join(source_path, sub)
                if os.path.isdir(subp):
                    for sub2 in os.listdir(subp):
                        if sub2 == 'ParserData':
                            has_parser = True
                            break
            if not has_parser:
                return 'C'

    # Last attempt: walk and look for ParserData at any depth
    for root, dirs, files in os.walk(source_path):
        if 'ParserData' in dirs:
            parent_name = os.path.basename(root)
            if re.match(r'^\d{8}$', parent_name):
                return 'B'
            return 'A'  # Any other directory with ParserData → Format A

    return None


def time_to_sec(t_str):
    """Convert HH:MM:SS[.f] to seconds."""
    try:
        parts = t_str.strip().split(':')
        return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
    except (ValueError, IndexError):
        return 0.0


def parse_session_key(filename, data_type_key, has_aircraft_prefix=False):
    """Extract session key (timestamp[_seq]) from a filename.

    Format A: "21DroneStateData_153351_535.txt" → "153351_535"
    Format B: "DroneStateData_114430.txt" → "114430"
    Format C: same as Format B

    Args:
        filename: The file basename
        data_type_key: The matched data type key (e.g., 'drone_state')
        has_aircraft_prefix: True if filename starts with aircraft ID digits (Format A)

    Returns:
        str: session key, or '' if not found
    """
    base = filename.rsplit('.txt', 1)[0]

    if has_aircraft_prefix:
        # "21DroneStateData_153351_535.txt" → extract after type marker
        for pattern in ['DroneStateData', 'GPSData', 'IMUData', 'PosData',
                        'EngineData', 'PowerBoxData', 'DualAntennaData',
                        'FlightAlertInfo', 'AllReceivedData', 'HandlePacket',
                        'SendCommand', 'AvionicsData', 'ControllerData',
                        'FanControlData', 'GPSCompareData']:
            marker = pattern + '_'
            idx = base.find(marker)
            if idx >= 0:
                return base[idx + len(marker):]
        return ''
    else:
        # "DroneStateData_114430.txt" → everything after the type marker and underscore
        for pattern in ['DroneStateData', 'GPSData', 'IMUData', 'PosData',
                        'EngineData', 'PowerBoxData', 'DualAntennaData',
                        'FlightAlertInfo', 'AllReceivedData', 'HandlePacket',
                        'SendCommand', 'AvionicsData', 'ControllerData',
                        'FanControlData', 'GPSCompareData']:
            marker = pattern + '_'
            idx = base.find(marker)
            if idx >= 0:
                return base[idx + len(marker):]
        # Fallback: last underscore-separated segment looks like timestamp
        parts = base.rsplit('_', 1)
        if len(parts) == 2 and parts[1].isdigit():
            return parts[1]
        return ''


def _extract_timestamp_from_key(session_key):
    """Extract seconds-since-midnight from HHMMSS prefix of a session key."""
    ts = session_key[:6]
    if len(ts) == 6 and ts.isdigit():
        return int(ts[:2]) * 3600 + int(ts[2:4]) * 60 + int(ts[4:6])
    return None


def _build_clusters(files_info, max_diff_sec=3):
    """Group files by time proximity (transitive clustering).

    Args:
        files_info: list of dicts with 'session_key'
        max_diff_sec: max seconds between timestamps to merge

    Returns:
        list of (canonical_key, [file_info, ...])
    """
    from collections import defaultdict

    by_key = defaultdict(list)
    for f in files_info:
        by_key[f['session_key']].append(f)

    if not by_key:
        return []

    items = []
    for key, files in by_key.items():
        ts = _extract_timestamp_from_key(key)
        items.append((ts, key, files))

    items.sort(key=lambda x: (x[0] is None, x[0] or 0))

    clusters = []
    for ts, key, files in items:
        if ts is None:
            clusters.append((key, files, None))
            continue

        if clusters and clusters[-1][2] is not None:
            last_max_ts = clusters[-1][2]
            if ts - last_max_ts <= max_diff_sec:
                canon_key, merged, _ = clusters[-1]
                if '_' in key and '_' not in canon_key:
                    canon_key = key
                elif not ('_' in canon_key and '_' not in key):
                    if len(files) > len(merged):
                        canon_key = key
                clusters[-1] = (canon_key, merged + files, max(last_max_ts, ts))
                continue

        clusters.append((key, files, ts))

    return [(key, files) for key, files, _ in clusters]


# ── Per-format scanners ──

# Subdirectories to scan under each aircraft folder in Format A
# Ordered: ParserData first (primary data), then others
FORMAT_A_SCAN_DIRS = [
    ('ParserData', False),        # data_type derived from filename
    ('FlightAlertInfo', True),    # is_alert=True, data_type='alert'
    ('AllFlightData', False),     # e.g., AllReceivedData
    ('HandlePacket', False),      # e.g., HandlePacket
    ('SendCommand', False),       # e.g., SendCommand
]


def scan_format_a(root_path):
    """Scan Format A directory structure: {root}/{aircraft_id}/{subdir}/*.txt

    Scans all known subdirectories under each aircraft folder, not just
    ParserData. This ensures aircraft serials are detected even when an
    aircraft only has data in non-ParserData directories (e.g., SendCommand).
    """
    config = load_format_config('A')
    results = []

    if not os.path.isdir(root_path):
        return results

    for entry in os.listdir(root_path):
        drone_dir = os.path.join(root_path, entry)
        if not os.path.isdir(drone_dir):
            continue
        # Accept any directory name as aircraft serial (numbers, letters, Chinese, etc.)
        # Skip hidden dirs and files; the directory must contain at least one data subdir
        if entry.startswith('.'):
            continue
        aircraft_serial = entry

        for scan_dir, is_alert_dir in FORMAT_A_SCAN_DIRS:
            dir_path = os.path.join(drone_dir, scan_dir)
            if not os.path.isdir(dir_path):
                continue

            for fname in os.listdir(dir_path):
                if not fname.endswith('.txt'):
                    continue
                filepath = os.path.join(dir_path, fname)

                if is_alert_dir:
                    # FlightAlertInfo: all files are alerts
                    dt_key = 'alert'
                    is_alert = True
                else:
                    dt_key, tdef = get_data_type_key(fname, config)
                    if not dt_key:
                        # File type not in format config — skip for import,
                        # but aircraft_serial is still recorded via other files
                        continue
                    is_alert = tdef.get('is_alert', False) if tdef else False

                results.append({
                    'aircraft_serial': aircraft_serial,
                    'data_type_key': dt_key,
                    'filepath': filepath,
                    'filename': fname,
                    'is_alert': is_alert,
                    'session_key': parse_session_key(
                        fname, dt_key,
                        has_aircraft_prefix=True,
                    ),
                })

    return results


def scan_format_b(root_path):
    """Scan Format B directory structure: {root}/{YYYYMMDD}/ParserData/*.txt etc."""
    config = load_format_config('B')
    results = []

    if not os.path.isdir(root_path):
        return results

    # Look for date subdirectories (8-digit YYYYMMDD)
    for entry in os.listdir(root_path):
        date_dir = os.path.join(root_path, entry)
        if not os.path.isdir(date_dir):
            continue
        if not re.match(r'^\d{8}$', entry):
            continue

        aircraft_serial = ''  # Format B doesn't encode aircraft serial in directory structure

        # Scan ParserData
        parser_dir = os.path.join(date_dir, 'ParserData')
        if os.path.isdir(parser_dir):
            for fname in os.listdir(parser_dir):
                if not fname.endswith('.txt'):
                    continue
                filepath = os.path.join(parser_dir, fname)
                dt_key, tdef = get_data_type_key(fname, config)
                if not dt_key:
                    continue

                results.append({
                    'aircraft_serial': aircraft_serial,
                    'data_type_key': dt_key,
                    'filepath': filepath,
                    'filename': fname,
                    'is_alert': tdef.get('is_alert', False),
                    'session_key': parse_session_key(fname, dt_key, has_aircraft_prefix=False),
                })

        # Scan FlightAlertInfo
        alert_dir = os.path.join(date_dir, 'FlightAlertInfo')
        if os.path.isdir(alert_dir):
            for fname in os.listdir(alert_dir):
                if not fname.endswith('.txt'):
                    continue
                filepath = os.path.join(alert_dir, fname)
                results.append({
                    'aircraft_serial': aircraft_serial,
                    'data_type_key': 'alert',
                    'filepath': filepath,
                    'filename': fname,
                    'is_alert': True,
                    'session_key': parse_session_key(fname, 'alert', has_aircraft_prefix=False),
                })

        # Scan GPSCompareData
        gps_compare_dir = os.path.join(date_dir, 'GPSCompareData')
        if os.path.isdir(gps_compare_dir):
            for fname in os.listdir(gps_compare_dir):
                if not fname.endswith('.txt'):
                    continue
                filepath = os.path.join(gps_compare_dir, fname)
                results.append({
                    'aircraft_serial': aircraft_serial,
                    'data_type_key': 'gps_compare',
                    'filepath': filepath,
                    'filename': fname,
                    'is_alert': False,
                    'session_key': parse_session_key(fname, 'gps_compare', has_aircraft_prefix=False),
                })

    return results


def scan_format_c(root_path):
    """Scan Format C directory structure: flat .txt files at root."""
    config = load_format_config('C')
    results = []

    if not os.path.isdir(root_path):
        return results

    aircraft_serial = ''

    for fname in os.listdir(root_path):
        if not fname.endswith('.txt'):
            continue
        filepath = os.path.join(root_path, fname)
        dt_key, tdef = get_data_type_key(fname, config)
        if not dt_key:
            continue

        results.append({
            'aircraft_serial': aircraft_serial,
            'data_type_key': dt_key,
            'filepath': filepath,
            'filename': fname,
            'is_alert': tdef.get('is_alert', False),
            'session_key': parse_session_key(fname, dt_key, has_aircraft_prefix=False),
        })

    return results


def scan_folder(source_path, format_category=None):
    """Scan a folder for flight data files.

    Args:
        source_path: Root folder path
        format_category: 'A', 'B', 'C' or None (auto-detect)

    Returns:
        dict: {source_path, folder_name, format_category, sessions: [...]}
    """
    if format_category is None:
        format_category = detect_format(source_path)

    if format_category is None:
        return {
            'source_path': source_path,
            'folder_name': os.path.basename(source_path.rstrip('/\\')),
            'format_category': None,
            'sessions': [],
            'error': 'Could not detect data format. Please specify format manually.',
        }

    scanners = {
        'A': scan_format_a,
        'B': scan_format_b,
        'C': scan_format_c,
    }

    scanner = scanners.get(format_category)
    if not scanner:
        return {
            'source_path': source_path,
            'folder_name': os.path.basename(source_path.rstrip('/\\')),
            'format_category': format_category,
            'sessions': [],
            'error': f'Unknown format category: {format_category}',
        }

    files = scanner(source_path)
    if not files:
        return {
            'source_path': source_path,
            'folder_name': os.path.basename(source_path.rstrip('/\\')),
            'format_category': format_category,
            'sessions': [],
            'error': 'No data files found',
        }

    # Group by aircraft_serial, then cluster by time within each aircraft
    from collections import defaultdict
    by_aircraft = defaultdict(list)
    for f in files:
        by_aircraft[f['aircraft_serial']].append(f)

    sessions = []
    for serial in sorted(by_aircraft.keys()):
        clusters = _build_clusters(by_aircraft[serial])

        for session_key, cluster_files in clusters:
            data_types = defaultdict(int)
            for f in cluster_files:
                data_types[f['data_type_key']] += 1

            sessions.append({
                'aircraft_serial': serial,
                'session_key': session_key,
                'data_types': dict(data_types),
                'file_count': len(cluster_files),
            })

    return {
        'source_path': source_path,
        'folder_name': os.path.basename(source_path.rstrip('/\\')),
        'format_category': format_category,
        'format_detected': True,
        'sessions': sessions,
    }


def scan_folder_sessions(source_path, conn=None):
    """Scan and return session-grouped preview with import status.

    Used by the API endpoint for the scan preview in the Import page.
    """
    from backend.database import get_db
    from backend.format_configs import get_table_name

    result = scan_folder(source_path)

    if conn is None:
        conn = get_db()
        close_conn = True
    else:
        close_conn = False

    # Find suggested model based on format_category
    fmt = result.get('format_category')
    if fmt:
        existing_models = conn.execute(
            "SELECT id, name FROM aircraft_models WHERE format_category=?",
            (fmt,)
        ).fetchall()
        if existing_models:
            result['suggested_model_id'] = existing_models[0]['id']
            result['suggested_model_name'] = existing_models[0]['name']
            result['matching_models'] = [{'id': m['id'], 'name': m['name']} for m in existing_models]

    # Check import status for each session
    for sess in result.get('sessions', []):
        sess['import_status'] = 'new'
        serial = sess['aircraft_serial']

        if fmt:
            # Format A: serial comes from directory name → look up aircraft by serial
            if serial:
                ac = conn.execute(
                    """SELECT a.id, a.serial_number, am.name as model_name
                       FROM aircraft a JOIN aircraft_models am ON am.id = a.model_id
                       WHERE am.format_category=? AND a.serial_number=?""",
                    (fmt, serial)
                ).fetchone()
                if ac:
                    existing = conn.execute(
                        "SELECT id, name FROM flights WHERE aircraft_id=? AND source_path=? AND session_key=?",
                        (ac['id'], source_path, sess['session_key'])
                    ).fetchone()
                    if existing:
                        sess['import_status'] = 'imported'
                        sess['existing_flight_id'] = existing['id']
                        sess['existing_flight_name'] = existing['name']
                        sess['aircraft_id'] = ac['id']

            # Format B/C: serial is empty → check flights table by (source_path, session_key) directly
            else:
                existing = conn.execute(
                    """SELECT f.id, f.name, f.aircraft_id, a.serial_number as aircraft_serial
                       FROM flights f
                       LEFT JOIN aircraft a ON a.id = f.aircraft_id
                       WHERE f.source_path=? AND f.session_key=?""",
                    (source_path, sess['session_key'])
                ).fetchone()
                if existing:
                    sess['import_status'] = 'imported'
                    sess['existing_flight_id'] = existing['id']
                    sess['existing_flight_name'] = existing['name']
                    sess['aircraft_id'] = existing['aircraft_id']
                    # Set the aircraft serial from the existing flight so the
                    # frontend shows which aircraft this session belongs to
                    if existing['aircraft_serial']:
                        sess['aircraft_serial'] = existing['aircraft_serial']

    if close_conn:
        conn.close()

    return result
