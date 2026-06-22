"""TSV data file parsing and data import pipeline.

Refactored: delegates format detection and scanning to scanner.py,
config-driven import to importer.py, config loading to format_configs.py.
"""

import os
import logging
from backend.database import get_db

logger = logging.getLogger(__name__)

# Re-export from scanner for backward compatibility
from backend.scanner import (
    detect_encoding, has_header, parse_lines, time_to_sec,
    scan_folder, scan_folder_sessions, parse_session_key,
    _validate_source_path,
)

# Re-export config helpers
from backend.format_configs import (
    load_format_config_by_model, get_data_type_key, data_table_name,
    register_model_tables, get_columns_for_model, get_columns_for_flight,
)

from backend.importer import (
    import_data_type, import_alerts, import_files_for_session,
)


def _extract_flight_date(source_path):
    """Extract flight date from directory hierarchy.

    Walks up from source_path to find the first directory whose name
    starts with an 8-digit YYYYMMDD prefix. Returns 'YYYY-MM-DD' or None.
    """
    path = os.path.normpath(source_path)
    while True:
        dirname = os.path.basename(path)
        if len(dirname) >= 8 and dirname[:8].isdigit():
            ds = dirname[:8]
            return f"{ds[:4]}-{ds[4:6]}-{ds[6:8]}"
        parent = os.path.dirname(path)
        if parent == path:
            break
        path = parent
    return None


def import_session(source_path, aircraft_id, session_key):
    """Import a single flight session into the hierarchy.

    Args:
        source_path: Root folder path
        aircraft_id: aircraft.id (must exist)
        session_key: Target session key

    Returns:
        {flight_id, aircraft_id, session_key, name, rows, details} or {error: ...}
    """
    conn = get_db()

    # Normalize path for cross-platform consistency (matches scanner.py:449)
    source_path = os.path.normpath(source_path)

    # Validate directory structure
    path_error = _validate_source_path(source_path)
    if path_error:
        conn.close()
        return {'error': path_error}

    # Resolve aircraft → model → format
    aircraft = conn.execute(
        """SELECT a.id, a.serial_number, am.id as model_id, am.format_category, am.name as model_name
           FROM aircraft a JOIN aircraft_models am ON am.id = a.model_id
           WHERE a.id=?""",
        (aircraft_id,)
    ).fetchone()

    if not aircraft:
        conn.close()
        return {'error': f'Aircraft {aircraft_id} not found'}

    model_id = aircraft['model_id']
    format_category = aircraft['format_category']

    # Load model-specific config (model_{id}.json), not raw format_category
    fmt_config = load_format_config_by_model(conn, model_id)
    if not fmt_config:
        conn.close()
        return {'error': f'Format config not found for model {model_id}'}

    # Scan files using the model's config
    from backend.scanner import scan_files_recursive
    try:
        all_files = scan_files_recursive(source_path, fmt_config)
    except FileNotFoundError as e:
        conn.close()
        return {'error': str(e)}

    # Filter to this aircraft and cluster by time
    target_serial = aircraft['serial_number']
    if fmt_config.get('extract_serial_from_path', False):
        drone_files = [f for f in all_files if f['aircraft_serial'] == target_serial]
        # Fallback: if source_path IS the aircraft folder, serial extraction
        # returns empty; use all files in this case
        if not drone_files:
            drone_files = all_files
    else:
        drone_files = all_files  # All files belong to the assigned aircraft

    if not drone_files:
        conn.close()
        return {'error': f'No files found for aircraft {target_serial}'}

    from backend.scanner import _build_clusters
    clusters = _build_clusters(drone_files)

    # Find the cluster matching session_key
    matching = None
    canonical_key = session_key
    for canon_key, cluster_files in clusters:
        found = (canon_key == session_key)
        if not found:
            for f in cluster_files:
                if f['session_key'] == session_key:
                    found = True
                    break
        if found:
            matching = cluster_files
            canonical_key = canon_key
            break

    if not matching:
        conn.close()
        return {'error': f'No matching session found for key {session_key}'}

    session_key = canonical_key
    folder_name = os.path.basename(source_path.rstrip('/\\'))

    # Determine flight_date from directory hierarchy
    flight_date = _extract_flight_date(source_path)

    # Reject if already imported — check by aircraft + flight_date + session_key
    if flight_date:
        existing = conn.execute(
            "SELECT id FROM flights WHERE aircraft_id=? AND flight_date=? AND session_key=?",
            (aircraft_id, flight_date, session_key)
        ).fetchone()
        if existing:
            conn.close()
            return {'error': f'飞机已有日期 {flight_date} 的架次 {session_key}（flight #{existing["id"]}）'}

    # Fallback: check by source_path (legacy data without flight_date)
    existing = conn.execute(
        "SELECT id FROM flights WHERE aircraft_id=? AND source_path=? AND session_key=?",
        (aircraft_id, source_path, session_key)
    ).fetchone()
    if existing:
        conn.close()
        return {'error': f'Flight already exists for session {session_key}'}

    # Flight name: use session_key by default (can be renamed by user later)
    flight_name = session_key if session_key else folder_name

    # Insert flight record
    conn.execute(
        """INSERT INTO flights (aircraft_id, name, source_path, session_key, flight_date)
           VALUES (?, ?, ?, ?, ?)""",
        (aircraft_id, flight_name, source_path, session_key, flight_date)
    )
    flight_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    # Import all files for this session
    import_result = import_files_for_session(conn, flight_id, matching, model_id)

    conn.close()

    if isinstance(import_result, dict) and 'error' in import_result:
        return import_result

    return {
        'flight_id': flight_id,
        'aircraft_id': aircraft_id,
        'session_key': session_key,
        'name': flight_name,
        'rows': import_result.get('rows', 0),
        'details': import_result.get('details', {}),
    }


def import_flight(source_path):
    """Import all sessions in a folder (backward-compatible bulk import).

    This requires the folder structure to already have aircraft/model info
    (Format A only). For Format B/C, use import_session with explicit aircraft_id.
    """
    conn = get_db()
    preview = scan_folder_sessions(source_path, conn=conn)
    conn.close()

    if not preview.get('sessions'):
        return {'error': preview.get('error', 'No sessions found in folder')}

    # For Format A: auto-resolve aircraft_id from serial
    imported = []
    for sess in preview['sessions']:
        if sess.get('aircraft_id'):
            result = import_session(
                source_path, sess['aircraft_id'], sess['session_key'],
                mode='overwrite'
            )
        else:
            # Format B/C without pre-assigned aircraft — need user to provide
            result = {'error': f"No aircraft assigned for session {sess['session_key']}. Use import_session with aircraft_id."}

        if 'error' not in result:
            imported.append(result)

    if not imported:
        return {'error': 'No sessions could be imported'}
    return {'imported': imported}
