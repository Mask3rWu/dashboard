"""TSV data file parsing and data import pipeline.

Refactored: delegates format detection and scanning to scanner.py,
config-driven import to importer.py, config loading to format_configs.py.
"""

import json
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
from backend.raw_storage import attach_raw_files_to_flight
from backend import flight_repository

RECORD_FIELD_COLUMNS = flight_repository.RECORD_COLUMNS


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


def import_session(source_path, aircraft_id, session_key, record_fields=None):
    """Import a single flight session into the hierarchy.

    Args:
        source_path: Root folder path
        aircraft_id: aircraft.id (must exist)
        session_key: Target session key
        record_fields: Optional manual flight record field values

    Returns:
        {flight_id, aircraft_id, session_key, name, rows, details} or {error: ...}
    """
    conn = get_db()
    record_fields = record_fields or {}

    # Normalize path for cross-platform consistency (matches scanner.py:449)
    source_path = os.path.normpath(source_path)

    # Validate directory structure
    path_error = _validate_source_path(source_path)
    if path_error:
        conn.close()
        return {'error': path_error}

    # Resolve aircraft → model
    aircraft = flight_repository.get_aircraft_with_model(conn, aircraft_id)

    if not aircraft:
        conn.close()
        return {'error': f'Aircraft {aircraft_id} not found'}

    model_id = aircraft['model_id']

    # Load the model's STORED config and use it for BOTH scanning and importing.
    # Serial-prefix stripping (in _discover_file_patterns) plus word-boundary
    # matching (in get_data_type_key) means the stored file_patterns
    # (e.g. "DroneStateData") match any aircraft's prefixed file
    # (e.g. "24DroneStateData_…"), so there is no longer a reason to rescan with
    # a freshly-generated config. Using the stored config here also means types
    # the user deselected at model-creation time (raw byte dumps) are never
    # scanned in — their files are skipped cleanly instead of producing 0-row
    # imports and "No config for data type" noise.
    stored_config = load_format_config_by_model(conn, model_id)
    if not stored_config or not stored_config.get('data_types'):
        conn.close()
        return {'error': f'Format config not found for model {model_id}'}

    from backend.scanner import scan_files_recursive
    try:
        all_files = scan_files_recursive(source_path, stored_config)
    except FileNotFoundError as e:
        conn.close()
        return {'error': str(e)}

    # Filter to this aircraft and cluster by time. The aircraft's `name` is
    # matched against the serial extracted from the directory hierarchy.
    target_serial = aircraft['name']
    if stored_config.get('extract_serial_from_path', False):
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

    # Reject if already imported — duplicate boundary is aircraft + flight_date
    # + session_key. source_path is stored for provenance only and deliberately
    # NOT used for dedup (a folder may be moved/re-imported from a new path).
    if flight_date:
        existing = flight_repository.find_duplicate_flight(
            conn, aircraft_id, flight_date, session_key
        )
        if existing:
            conn.close()
            return {'error': f'飞机已有日期 {flight_date} 的架次 {session_key}（flight #{existing["id"]}）'}

    # Flight name: use session_key by default (can be renamed by user later)
    flight_name = session_key if session_key else folder_name

    # Insert flight record. Manual record fields are separate from parsed
    # duration/start/end values that importer.py fills later.
    flight_id = flight_repository.insert_flight(
        conn, aircraft_id, flight_name, source_path, session_key, flight_date, record_fields
    )

    try:
        # Import all files for this session. The scan above used the stored config,
        # so files_info's data_type_keys line up with the stored config's
        # definitions. Types the user deselected at creation time are not in the
        # stored config's file_patterns and were never picked up by the scan.
        # Cross-model import (writing A's data into B's tables) is no longer
        # supported — the import target is the model the scan resolved to.
        import_result = import_files_for_session(
            conn, flight_id, matching, model_id, format_config=stored_config
        )

        if isinstance(import_result, dict) and 'error' in import_result:
            conn.rollback()
            return import_result

        raw_result = attach_raw_files_to_flight(conn, flight_id, source_path, matching)
        raw_warnings = raw_result.get('warnings', [])
        if raw_warnings:
            flight_repository.set_raw_import_warnings(
                conn, flight_id, json.dumps(raw_warnings, ensure_ascii=False)
            )

        conn.commit()
    except Exception as e:
        logger.exception("Import session failed; rolling back flight %s", flight_id)
        conn.rollback()
        return {'error': str(e)}
    finally:
        try:
            conn.close()
        except Exception:
            pass

    return {
        'flight_id': flight_id,
        'aircraft_id': aircraft_id,
        'session_key': session_key,
        'name': flight_name,
        'rows': import_result.get('rows', 0),
        'details': import_result.get('details', {}),
        'raw_files': raw_result.get('attached', 0),
        'raw_warnings': raw_warnings,
    }
