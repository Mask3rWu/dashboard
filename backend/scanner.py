"""Format auto-detection and per-format directory scanning.

Supports any format category string — not just A/B/C. Config-driven file
pattern matching and session key extraction via format config JSONs.
"""

import os
import re
from backend.format_configs import get_data_type_key

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



def time_to_sec(t_str):
    """Convert HH:MM:SS[.f] to seconds."""
    try:
        parts = t_str.strip().split(':')
        return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
    except (ValueError, IndexError):
        return 0.0


def _get_config_file_patterns(config):
    """Get the union of all file_patterns from a config's data_types."""
    patterns = set()
    for tdef in config.get('data_types', {}).values():
        for p in tdef.get('file_patterns', []):
            patterns.add(p)
    return patterns


def parse_session_key(filename, config):
    """Extract session key (timestamp[_seq]) from a filename.

    Uses the format config's file_patterns to locate the type marker,
    then extracts everything after it.

    Args:
        filename: The file basename
        config: The format config dict

    Returns:
        str: session key, or '' if not found
    """
    base = filename.rsplit('.txt', 1)[0]

    patterns = _get_config_file_patterns(config)
    for pattern in patterns:
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


# ── Model resolution (auto-detect + compare + auto-create) ──

MATCH_THRESHOLD = 0.95


def resolve_model_for_scan(conn, source_path):
    """Analyze a folder and compare against all existing model configs.

    This is a PREVIEW only — it never persists anything. If an existing model
    matches (score >= MATCH_THRESHOLD) it is returned as the resolved model; if
    nothing matches, the folder is reported as a new-format candidate with the
    discovered data types listed (each tagged is_raw so the UI can default-deselect
    raw byte dumps). Creating the new model is a separate, explicit step done by
    the caller via create_model_from_scan() once the user confirms the name and
    which data types to keep.

    Returns:
        dict with keys:
            model_id, model_name, is_new,
            match_confidence (None for new), config (generated config dict),
            matching_models (list of {id, name, score}),
            suggested_name, discovered_types (new only)
    """
    from backend.format_configs import (
        generate_config_from_scan, load_all_model_configs_with_ids,
        compare_configs, load_format_config_by_model,
    )

    # Step 1 — auto-generate config from the folder
    generated = generate_config_from_scan(source_path)
    if not generated or not generated.get('data_types'):
        return None

    # Step 2 — load all existing model configs
    all_models = load_all_model_configs_with_ids(conn)

    # Step 3 — compare against every existing model
    best_score = 0.0
    best_model = None
    all_scores = []

    for model_id, model_name, existing_config in all_models:
        score = compare_configs(generated, existing_config)
        all_scores.append({
            'id': model_id, 'name': model_name, 'score': round(score, 3),
        })
        if score > best_score:
            best_score = score
            best_model = (model_id, model_name)

    all_scores.sort(key=lambda x: x['score'], reverse=True)

    # Step 4a — match found: resolve to the existing model (no write)
    if best_model and best_score >= MATCH_THRESHOLD:
        model_id, model_name = best_model
        stored_config = load_format_config_by_model(conn, model_id)
        if not stored_config or not stored_config.get('data_types'):
            return None
        return {
            'model_id': model_id,
            'model_name': model_name,
            'is_new': False,
            'match_confidence': round(best_score, 3),
            'config': stored_config,
            'matching_models': all_scores[:5],
        }

    # Step 4b — no match: report as a new-format candidate (no write).
    # Surface the discovered types so the UI can let the user pick which to
    # keep; is_raw=True types default to deselected.
    folder_name = os.path.basename(source_path.rstrip('/\\'))
    discovered = [
        {
            'data_type_key': key,
            'display_label': tdef.get('display_label', key),
            'is_alert': bool(tdef.get('is_alert', False)),
            'is_raw': bool(tdef.get('is_raw', False)),
            'column_count': len(tdef.get('columns', [])),
        }
        for key, tdef in generated['data_types'].items()
    ]

    return {
        'model_id': None,
        'model_name': None,
        'is_new': True,
        'match_confidence': None,
        'config': generated,
        'matching_models': all_scores[:5],
        'suggested_name': f"机型-{folder_name}",
        'discovered_types': discovered,
    }


def _extract_aircraft_serial_from_path(filepath, source_path):
    """Extract aircraft serial from standardized directory hierarchy.

    Standard: YYYYMMDD*/aircraft_serial/...
    Finds the date directory (starts with 8 digits) in the path;
    the immediate subdirectory below it is the aircraft serial.

    Returns the aircraft serial string, or '' if no date dir found.
    """
    path = os.path.normpath(filepath)
    parts = path.split(os.sep)
    for i, part in enumerate(parts):
        if len(part) >= 8 and part[:8].isdigit():
            if i + 1 < len(parts):
                return parts[i + 1]
            break
    return ''


def scan_files_recursive(source_path, config):
    """Unified recursive scanner: find all .txt files at any depth.

    Unlike the old per-format scanners which assumed fixed directory
    structures, this function uses os.walk() to discover files regardless
    of folder layout.

    Args:
        source_path: Root directory to scan
        config: Format config dict (already loaded by caller)

    Returns:
        list of dicts with keys: aircraft_serial, data_type_key, filepath,
                                  filename, is_alert, session_key
    """
    results = []

    if not os.path.isdir(source_path):
        return results

    extract_serial = config.get('extract_serial_from_path', True)

    for root, _dirs, files in os.walk(source_path):
        for fname in files:
            if not fname.endswith('.txt'):
                continue

            filepath = os.path.join(root, fname)

            # Classify the file by matching its name against the config's
            # file_patterns (word-boundary regex). is_alert comes from the
            # matched data-type definition (set during config generation when
            # the pattern name contains 'alert'), so no directory-name or
            # key-name special-casing is needed here.
            dt_key, tdef = get_data_type_key(fname, config)
            if not dt_key:
                continue
            is_alert = tdef.get('is_alert', False) if tdef else False

            # Determine aircraft_serial
            if extract_serial:
                aircraft_serial = _extract_aircraft_serial_from_path(filepath, source_path)
            else:
                aircraft_serial = ''

            session_key = parse_session_key(fname, config)

            results.append({
                'aircraft_serial': aircraft_serial,
                'data_type_key': dt_key,
                'filepath': filepath,
                'filename': fname,
                'is_alert': is_alert,
                'session_key': session_key,
            })

    return results


def scan_folder(source_path, config):
    """Scan a folder for flight data files using the given config.

    This is a helper that does NOT auto-detect — the caller is responsible
    for resolving the format config (via resolve_model_for_scan()).

    Args:
        source_path: Root folder path
        config: Format config dict (required)

    Returns:
        dict: {source_path, folder_name, sessions: [...]}
    """
    if not os.path.isdir(source_path):
        return {
            'source_path': source_path,
            'folder_name': os.path.basename(source_path.rstrip('/\\')),
            'sessions': [],
            'error': 'Source path is not a directory',
        }

    try:
        files = scan_files_recursive(source_path, config)
    except FileNotFoundError as e:
        return {
            'source_path': source_path,
            'folder_name': os.path.basename(source_path.rstrip('/\\')),
            'sessions': [],
            'error': str(e),
        }

    if not files:
        return {
            'source_path': source_path,
            'folder_name': os.path.basename(source_path.rstrip('/\\')),
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
        'format_detected': True,
        'sessions': sessions,
    }


def _validate_source_path(source_path):
    """Validate that source_path follows the standard directory hierarchy.

    Standard: YYYYMMDD*/... (date directory must exist somewhere in the path)
    The aircraft serial is extracted from subdirectories within the date dir
    by _extract_aircraft_serial_from_path(); it does NOT need to be part of
    the source_path itself (e.g. source_path may be the date dir itself).

    Returns an error message string if invalid, or None if valid.
    """
    path = os.path.normpath(source_path)
    folders = [p for p in path.split(os.sep) if p]

    # Find date directory
    date_idx = -1
    for i, folder in enumerate(folders):
        if len(folder) >= 8 and folder[:8].isdigit():
            date_idx = i
            break

    if date_idx < 0:
        return (
            "目录结构不符合规范。\n"
            "第一层目录需以 YYYYMMDD（8位日期）开头，例如 20250323_test_flight/。\n"
            "当前路径未检测到日期目录。请整理目录结构后重试。"
        )

    return None


def scan_folder_sessions(source_path, conn=None):
    """Scan, auto-detect format, resolve model (auto-create if new), and
    return session-grouped preview with import status.

    This is the main entry point used by the scan API endpoint.
    """
    from backend.database import get_db
    from backend.format_configs import get_table_name

    if conn is None:
        conn = get_db()
        close_conn = True
    else:
        close_conn = False

    source_path = os.path.normpath(source_path)

    # Step 0 — validate directory structure
    path_error = _validate_source_path(source_path)
    if path_error:
        result = {
            'source_path': source_path,
            'folder_name': os.path.basename(source_path.rstrip('/\\')),
            'format_detected': False,
            'model': None,
            'sessions': [],
            'error': path_error,
        }
        if close_conn:
            conn.close()
        return result

    # Step 1 — resolve model: auto-generate config, compare, auto-create if no match
    model_info = resolve_model_for_scan(conn, source_path)

    if model_info is None:
        result = {
            'source_path': source_path,
            'folder_name': os.path.basename(source_path.rstrip('/\\')),
            'format_detected': False,
            'model': None,
            'sessions': [],
            'error': 'No recognizable data files found in this folder.',
        }
        if close_conn:
            conn.close()
        return result

    # Step 2 — scan files using the resolved config.
    scan_result = scan_folder(source_path, model_info['config'])

    # The folder date is part of every session preview, including the
    # new-model flow where duplicate checks are skipped until a model exists.
    from backend.parser import _extract_flight_date
    flight_date = _extract_flight_date(source_path)
    for sess in scan_result.get('sessions', []):
        sess['flight_date'] = flight_date
        sess.setdefault('import_status', 'new')

    # When no existing model matches, the user must create one (choosing which
    # data types to keep) before any flight can be imported. We surface the
    # discovered types and return model=null so the UI prompts for creation;
    # the import-status / duplicate check below needs a model_id, so it is
    # skipped until a model exists.
    if model_info.get('is_new'):
        result = {
            'source_path': source_path,
            'folder_name': scan_result.get('folder_name', os.path.basename(source_path.rstrip('/\\'))),
            'format_detected': True,
            'model': None,
            'suggested_model_id': None,
            'suggested_model_name': None,
            'matching_models': model_info.get('matching_models', []),
            'suggested_name': model_info.get('suggested_name'),
            'discovered_types': model_info.get('discovered_types', []),
            'sessions': scan_result.get('sessions', []),
            'error': scan_result.get('error'),
        }
        if close_conn:
            conn.close()
        return result

    # Build the model descriptor
    model_desc = {
        'id': model_info['model_id'],
        'name': model_info['model_name'],
        'is_new': model_info['is_new'],
        'match_confidence': model_info['match_confidence'],
    }

    # Step 3 — merge scan result with model info and import status
    result = {
        'source_path': source_path,
        'folder_name': scan_result.get('folder_name', os.path.basename(source_path.rstrip('/\\'))),
        'format_detected': True,
        'model': model_desc,
        'suggested_model_id': model_info['model_id'],
        'suggested_model_name': model_info['model_name'],
        'matching_models': model_info.get('matching_models', []),
        'sessions': scan_result.get('sessions', []),
        'error': scan_result.get('error'),
    }

    # Step 4 — check import status for each session
    # Duplicate boundary: aircraft + flight_date + session_key.
    # A session is only a duplicate if the SAME aircraft already has this
    # date+time combination. Different aircraft with same time = new flights.
    for sess in result['sessions']:
        auto_serial = sess['aircraft_serial']

        if not flight_date:
            continue

        # Find ALL flights under this model with the same date+session_key
        conflicts = conn.execute(
            """SELECT f.id, f.name, a.name as aircraft_name
               FROM flights f
               JOIN aircraft a ON a.id = f.aircraft_id
               WHERE a.model_id = ? AND f.flight_date = ? AND f.session_key = ?""",
            (model_info['model_id'], flight_date, sess['session_key'])
        ).fetchall()

        if not conflicts:
            continue

        # Build conflict list for frontend to use in dynamic status check
        sess['conflicting_aircraft'] = [
            {'aircraft_serial': r['aircraft_name'],
             'flight_id': r['id'],
             'flight_name': r['name']}
            for r in conflicts
        ]

        # If auto-detected serial matches one of the conflicting aircraft,
        # mark as already imported
        for r in conflicts:
            if r['aircraft_name'] == auto_serial:
                sess['import_status'] = 'imported'
                sess['existing_flight_id'] = r['id']
                sess['existing_flight_name'] = r['name']
                sess['aircraft_id'] = conn.execute(
                    "SELECT id FROM aircraft WHERE model_id=? AND name=?",
                    (model_info['model_id'], auto_serial)
                ).fetchone()['id']
                break
        # Otherwise import_status stays 'new' — frontend will re-check
        # when user switches aircraft, using the conflicting_aircraft list.

    if close_conn:
        conn.close()

    return result
