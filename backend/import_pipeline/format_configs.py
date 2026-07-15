"""Format config management — pure database-driven, no JSON files.

Each aircraft model's configuration is stored in the SQLite database
(aircraft_models + data_table_registry + column_registry tables).

The ``build_model_config_from_db()`` function constructs a config dict
from the database that is 100% compatible with the legacy JSON file format.
All consumers (scanner, importer, parser) receive the same dict shape.
"""

import json
import logging
import os
import re

from backend.import_pipeline.file_reader import has_header, parse_lines

logger = logging.getLogger(__name__)

HEADER_SAMPLE_LIMIT_PER_PATTERN = 20


# ══════════════════════════════════════════════════════════════════════════════
# Build config dict from database (replaces JSON file loading)
# ══════════════════════════════════════════════════════════════════════════════

def build_model_config_from_db(conn, model_id):
    """Construct a format config dict from the database for a given model.

    The returned dict has the same structure as the legacy model_{id}.json files.

    Args:
        conn: SQLite connection
        model_id: aircraft_models.id

    Returns:
        dict with keys: has_header, has_uav_send_id,
                        extract_serial_from_path, data_types
        Returns None if model not found.
    """
    # ── Model-level settings ──
    model = conn.execute(
        """SELECT name, has_header, has_uav_send_id, extract_serial_from_path
           FROM aircraft_models WHERE id = ?""",
        (model_id,)
    ).fetchone()
    if not model:
        return None

    config = {
        'has_header': bool(model['has_header']),
        'has_uav_send_id': bool(model['has_uav_send_id']),
        'extract_serial_from_path': bool(model['extract_serial_from_path']),
        'data_types': {},
    }

    # ── Data types and columns ──
    dtr_rows = conn.execute(
        """SELECT data_type_key, display_label, file_patterns, is_alert
           FROM data_table_registry
           WHERE model_id = ?
           ORDER BY data_type_key""",
        (model_id,)
    ).fetchall()

    for dtr in dtr_rows:
        dt_key = dtr['data_type_key']
        try:
            patterns = json.loads(dtr['file_patterns'] or '[]')
        except (json.JSONDecodeError, TypeError):
            patterns = []

        data_type_def = {
            'display_label': dtr['display_label'],
            'file_patterns': patterns,
            'is_alert': bool(dtr['is_alert']),
            'columns': [],
        }

        # ── Columns for this data type ──
        col_rows = conn.execute(
            """SELECT column_name, display_label, unit, data_type, ordinal, scale_factor
               FROM column_registry
               WHERE model_id = ? AND data_type_key = ?
               ORDER BY ordinal""",
            (model_id, dt_key)
        ).fetchall()

        for cr in col_rows:
            data_type_def['columns'].append({
                'name': cr['column_name'],
                'label': cr['display_label'],
                'unit': cr['unit'] or '',
                'type': cr['data_type'] or 'REAL',
                'ordinal': cr['ordinal'],
                'scale_factor': cr['scale_factor'] if cr['scale_factor'] is not None else 1.0,
            })

        config['data_types'][dt_key] = data_type_def

    return config


# ══════════════════════════════════════════════════════════════════════════════
# Save config dict to database (replaces save_model_config JSON file write)
# ══════════════════════════════════════════════════════════════════════════════

def save_model_config_to_db(conn, model_id, config):
    """Write model-level and per-data-type config values to the database.

    Column-level config is handled by ``register_model_tables()``.

    Args:
        conn: SQLite connection
        model_id: aircraft_models.id
        config: format config dict (as returned by generate_config_from_scan
                or build_model_config_from_db)
    """
    # ── Model-level settings ──
    conn.execute(
        """UPDATE aircraft_models SET
           has_header = ?, has_uav_send_id = ?,
           extract_serial_from_path = ?
           WHERE id = ?""",
        (
            1 if config.get('has_header') else 0,
            1 if config.get('has_uav_send_id') else 0,
            1 if config.get('extract_serial_from_path') else 0,
            model_id,
        )
    )

    # ── Per-data-type settings ──
    for dt_key, dt_def in config.get('data_types', {}).items():
        patterns_json = json.dumps(
            dt_def.get('file_patterns', []), ensure_ascii=False
        )
        is_alert = 1 if dt_def.get('is_alert') else 0
        conn.execute(
            """UPDATE data_table_registry SET
               file_patterns = ?, is_alert = ?
               WHERE model_id = ? AND data_type_key = ?""",
            (patterns_json, is_alert, model_id, dt_key)
        )


# ══════════════════════════════════════════════════════════════════════════════
# Config load functions (API-compatible with legacy JSON-based versions)
# ══════════════════════════════════════════════════════════════════════════════

def load_format_config_by_model(conn, model_id):
    """Load format config for a model from the database.

    Args:
        conn: SQLite connection
        model_id: aircraft_models.id

    Returns:
        dict: The format config, or None if model not found
    """
    return build_model_config_from_db(conn, model_id)


def load_all_model_configs_with_ids(conn):
    """Load all existing model configs with their database IDs.

    Returns:
        list of (model_id, model_name, config_dict)
    """
    models = []
    rows = conn.execute("SELECT id, name FROM aircraft_models").fetchall()
    for row in rows:
        config = build_model_config_from_db(conn, row['id'])
        if config:
            models.append((row['id'], row['name'], config))
    return models


# ══════════════════════════════════════════════════════════════════════════════
# Config comparison
# ══════════════════════════════════════════════════════════════════════════════

def compare_configs(generated, existing):
    """Compare an auto-generated config against an existing model config.

    Returns a similarity score 0.0–1.0.  Score >= 0.95 is considered a match.

    Components (data types are the core dimension):
      - Data type recall (0.60): fraction of the existing model's data types
        present in the generated config. Extra unknown types in the generated
        config do NOT penalize — this is what lets new .txt types be accepted
        on equal footing. Missing existing types still lower the score.
      - Structure flags (0.25): has_header + has_uav_send_id exact match
      - Column count agreement (0.15): average ratio per shared data type
    """
    # ── Data type recall (0.60) — core dimension ──
    gen_keys = set(generated.get('data_types', {}).keys())
    exist_keys = set(existing.get('data_types', {}).keys())
    if exist_keys:
        recall = len(gen_keys & exist_keys) / len(exist_keys)
    else:
        recall = 0.0

    # ── Structure flags (0.25) ──
    hh_match = 1.0 if generated.get('has_header') == existing.get('has_header') else 0.0
    uav_match = 1.0 if generated.get('has_uav_send_id') == existing.get('has_uav_send_id') else 0.0
    structure_score = (hh_match + uav_match) / 2.0

    # ── Column count agreement (0.15) ──
    shared = gen_keys & exist_keys
    col_ratios = []
    for key in shared:
        gen_cols = len(generated['data_types'][key].get('columns', []))
        exist_cols = len(existing['data_types'][key].get('columns', []))
        if max(gen_cols, exist_cols) > 0:
            col_ratios.append(min(gen_cols, exist_cols) / max(gen_cols, exist_cols))
    column_score = sum(col_ratios) / len(col_ratios) if col_ratios else 0.0

    return 0.60 * recall + 0.25 * structure_score + 0.15 * column_score


# ══════════════════════════════════════════════════════════════════════════════
# Column metadata update (database only — no JSON file sync)
# ══════════════════════════════════════════════════════════════════════════════

def update_column_metadata(conn, model_id, data_type_key, column_name,
                           display_label=None, unit=None, scale_factor=None):
    """Update display_label, unit, and/or scale_factor for a column.

    Writes ONLY to column_registry in SQLite.  No JSON file sync needed.

    Args:
        conn: SQLite connection
        model_id: aircraft_models.id
        data_type_key: e.g. 'gps', 'imu'
        column_name: SQL column name (internal identifier, not editable)
        display_label: new display label, or None to keep unchanged
        unit: new unit string, or None to keep unchanged
        scale_factor: new scale factor, or None to keep unchanged

    Returns:
        dict: {column_name, display_label, unit, scale_factor} with updated values

    Raises:
        ValueError: if model or column not found
    """
    if display_label is None and unit is None and scale_factor is None:
        raise ValueError("At least one of display_label, unit, or scale_factor must be provided")

    # Verify column exists in registry
    col_row = conn.execute(
        """SELECT display_label, unit, scale_factor FROM column_registry
           WHERE model_id=? AND data_type_key=? AND column_name=?""",
        (model_id, data_type_key, column_name)
    ).fetchone()
    if not col_row:
        raise ValueError(
            f"Column '{column_name}' not found in registry for model {model_id}"
        )

    new_label = display_label if display_label is not None else col_row['display_label']
    new_unit = unit if unit is not None else col_row['unit']
    new_scale = scale_factor if scale_factor is not None else (col_row['scale_factor'] or 1.0)

    # Update column_registry only
    conn.execute(
        """UPDATE column_registry SET display_label=?, unit=?, scale_factor=?
           WHERE model_id=? AND data_type_key=? AND column_name=?""",
        (new_label, new_unit, new_scale, model_id, data_type_key, column_name)
    )

    conn.commit()
    logger.info(
        f"Updated column {model_id}/{data_type_key}/{column_name}: "
        f"label='{new_label}', unit='{new_unit}', scale_factor={new_scale}"
    )

    return {
        'column_name': column_name,
        'display_label': new_label,
        'unit': new_unit,
        'scale_factor': new_scale,
    }


# ══════════════════════════════════════════════════════════════════════════════
# Auto-generate config from scan
# ══════════════════════════════════════════════════════════════════════════════

def _discover_file_patterns(source_path):
    """Scan a folder and discover unique file type patterns.

    Returns:
        list of (pattern_name, sample_filepath, token_count, header_names_or_none)
    """
    patterns = {}
    sample_counts = {}

    def read_pattern_sample(filepath):
        lines = parse_lines(filepath)
        if not lines:
            return None

        hdr = has_header(filepath)
        start = 1 if hdr else 0
        if start >= len(lines):
            return None

        token_count = len(lines[start].split())
        header_names = lines[0].split() if hdr else None
        return token_count, header_names

    def is_better_pattern_sample(existing, candidate):
        """Prefer header samples, then wider data rows for generic fallback."""
        if existing is None:
            return True

        _name, _filepath, existing_token_count, existing_header_names = existing
        _candidate_filepath, candidate_token_count, candidate_header_names = candidate

        existing_has_header = bool(existing_header_names)
        candidate_has_header = bool(candidate_header_names)
        if candidate_has_header != existing_has_header:
            return candidate_has_header

        return candidate_token_count > existing_token_count

    for root, _dirs, files in os.walk(source_path):
        for fname in files:
            if not fname.endswith('.txt'):
                continue
            # Extract base pattern: "DroneStateData_114430.txt" → "DroneStateData"
            base = fname.rsplit('.txt', 1)[0]
            # Find the type marker — everything before the last underscore-separated timestamp
            match = re.match(r'^(.+?)_(\d{4,})', base)
            pattern_name = match.group(1) if match else base
            # Strip a leading aircraft-serial prefix (digits before a CamelCase
            # type name: "24DroneStateData" → "DroneStateData"). The serial is
            # already encoded in the directory hierarchy, so the filename prefix
            # is redundant; keeping it would split one logical type into one key
            # per aircraft (drone_state, drone_state_1, …) and inflate the
            # comparison union. Keying the dict on the stripped name also
            # merges per-aircraft samples into a single entry.
            pattern_name = re.sub(r'^\d+(?=[A-Z])', '', pattern_name)

            existing = patterns.get(pattern_name)
            existing_has_header = bool(existing and existing[3])
            checked = sample_counts.get(pattern_name, 0)
            if existing_has_header or checked >= HEADER_SAMPLE_LIMIT_PER_PATTERN:
                continue

            filepath = os.path.join(root, fname)
            sample_counts[pattern_name] = checked + 1
            try:
                sample = read_pattern_sample(filepath)
                if sample is None:
                    continue
                token_count, header_names = sample
            except Exception:
                token_count, header_names = 0, None

            candidate = (filepath, token_count, header_names)
            if is_better_pattern_sample(existing, candidate):
                patterns[pattern_name] = (pattern_name, filepath, token_count, header_names)

    return list(patterns.values())


def _detect_has_header(source_path, sample_patterns):
    """Detect whether data files in this folder have headers."""
    for entry in sample_patterns:
        _name, filepath = entry[0], entry[1]
        try:
            if has_header(filepath):
                return True
        except Exception:
            pass
    return False


def _detect_has_uav_send_id(source_path, sample_patterns, has_header_flag):
    """Detect if data files have a UAVSendID column.

    Only checks when headers are present — looks at the second header column
    name for 'uav' or 'droneid' keywords.  Without headers there is no
    reliable way to tell a UAV ID from a sensor reading (a single-line
    heuristic like "second token is a small integer" causes false positives
    on stationary data where PosN/Roll/GPSVel are zero), so we default to
    False.
    """
    if not has_header_flag:
        return False

    for entry in sample_patterns:
        _name, filepath = entry[0], entry[1]
        try:
            lines = parse_lines(filepath)
            if lines:
                header_tokens = lines[0].split()
                if len(header_tokens) >= 2:
                    second_header = header_tokens[1].lower()
                    if 'uav' in second_header or 'droneid' in second_header:
                        return True
        except Exception:
            pass
    return False


def _sanitize_column_name(name):
    """Sanitize a column name for SQL: only allow alphanumeric and underscore.
    Replaces non-alphanumeric chars with '_', strips leading digits."""
    cleaned = re.sub(r'[^\w]', '_', name)
    cleaned = re.sub(r'_+', '_', cleaned)
    cleaned = re.sub(r'^(\d+)', r'c\1', cleaned)
    return cleaned


def _sanitize_data_type_key(pattern_name):
    """Convert filename pattern to a lowercase SQL-safe data_type_key.
    Used for unknown file types where no known mapping exists."""
    cleaned = re.sub(r'[^\w]', '_', pattern_name)
    cleaned = re.sub(r'_+', '_', cleaned)
    cleaned = re.sub(r'^(\d+)', r'c\1', cleaned)
    return cleaned.lower()


# Threshold for the "1-2 digit hex token" ratio that flags a raw byte dump.
# Sensor data maxes out around 0.69; the three known dump types (AllReceivedData,
# HandlePacket, SendCommand) are all >= 0.90. 0.85 sits in the clean gap.
_RAW_HEX_RATIO_THRESHOLD = 0.85
# Bytes sampled for the binary check. Reading the whole multi-MB file just to
# sniff the first bytes is wasteful; the head carries enough signal.
_RAW_SNIFF_BYTES = 8192


def _is_raw_dump(filepath):
    """Detect whether a .txt file is a raw byte dump rather than sensor data.

    Dump files (e.g. HandlePacket, AllReceivedData, SendCommand) are textual
    hex byte listings like "EE FF 7E 01 18 3E ..." — they decode as ASCII (no
    NUL bytes), so a naive binary check misses them. They are recognized by two
    content-driven signals:

      1. Binary content: NUL bytes or a high share of non-printable bytes in the
         head (catches true binary .txt that may appear in future formats).
      2. Hex-dump structure: across sampled rows, the share of tokens that are
         1-2 hex-digit strings (matching [0-9A-Fa-f]{1,2}) is >= 0.85. Sensor
         readings max out around 0.69 because their numeric fields have more
         digits; raw dumps are dominated by byte pairs.

    This is content analysis, not a filename whitelist — future unknown dump
    types are flagged the same way. The flag is advisory only (drives the
    default checkbox state during model creation); the file is never silently
    dropped and the user can still opt in.

    Returns:
        bool: True if the file looks like a raw byte dump.
    """
    import re as _re
    hex_token = _re.compile(r'^[0-9A-Fa-f]{1,2}$')

    # ── Signal 1: binary content in the file head ──
    # NUL bytes are an unambiguous binary marker. For the share signal we count
    # only C0 control chars (< 0x20, excluding \t \r \n) — bytes >= 0x80 are
    # UTF-8 continuation bytes that legitimately appear in Chinese labels, so
    # counting them would misflag localized text files as binary.
    try:
        with open(filepath, 'rb') as bf:
            head = bf.read(_RAW_SNIFF_BYTES)
    except OSError:
        return False
    if b'\x00' in head:
        return True
    ctrl = sum(1 for b in head if b < 0x20 and b not in (0x09, 0x0a, 0x0d))
    if ctrl / max(len(head), 1) > 0.10:
        return True

    # ── Signal 2: hex-dump token ratio across sampled data rows ──
    try:
        lines = parse_lines(filepath)
    except Exception:
        return False
    if not lines:
        return False
    start = 1 if has_header(filepath) else 0
    sample = lines[start:start + 50]
    total = 0
    hexish = 0
    for line in sample:
        # Skip the leading Time (and optional UAVSendID) token — those are never
        # hex bytes and would dilute the ratio. A conservative skip of just the
        # first token (Time) keeps the dump signal strong while not over-stripping
        # for headerless files where the leading field is itself data.
        tokens = line.split()
        # Skip Time + an optional UAV id token if it's not hex-like
        body = tokens[1:]
        total += len(body)
        hexish += sum(1 for t in body if hex_token.match(t))
    if total == 0:
        return False
    return (hexish / total) >= _RAW_HEX_RATIO_THRESHOLD


def _detect_column_types(filepath, has_header, has_uav, num_columns):
    """Sample one data row and detect TEXT vs REAL columns.

    Reads the first non-header row from filepath and tries float() on
    each token.  Returns a list of types (one per column), defaulting to 'REAL'.
    Falls back to all 'REAL' on any read error.
    """
    types = ['REAL'] * num_columns
    try:
        lines = parse_lines(filepath)
        if not lines:
            return types
        start = 1 if has_header(filepath) else 0
        if start >= len(lines):
            return types
        tokens = lines[start].split()
        offset = 1 + (1 if has_uav else 0)  # skip Time + optional UAVSendID
        for i in range(num_columns):
            idx = offset + i
            if idx < len(tokens):
                try:
                    float(tokens[idx])
                except (ValueError, TypeError):
                    types[i] = 'TEXT'
    except Exception:
        pass
    return types


def generate_config_from_scan(source_path):
    """Analyze a folder and generate a format config dict from discovered file patterns.

    All discovered .txt patterns are treated uniformly: every pattern becomes a
    data type keyed by a deterministic sanitization of its filename. There is no
    hardcoded type whitelist — future unknown file types are accepted on equal
    footing with known ones, and their keys/labels derive from the filename. The
    user can refine labels later.

    Args:
        source_path: Root folder to analyze

    Returns:
        dict: A format config structure
    """
    # Step 1: discover all file patterns
    all_patterns = _discover_file_patterns(source_path)

    # Step 2: detect header and UAV from non-alert patterns.
    # A pattern is "alert-like" if its name contains 'alert' (case-insensitive);
    # alert files use multi-word descriptions and would skew token-count
    # heuristics, so they are excluded from header/UAV detection.
    non_alert = [(n, fp, tc, hdr) for n, fp, tc, hdr in all_patterns
                 if 'alert' not in n.lower()]
    has_header_flag = _detect_has_header(source_path, non_alert) if non_alert else False
    has_uav = _detect_has_uav_send_id(source_path, non_alert, has_header_flag) if non_alert else False

    # Step 3: build data types from ALL patterns uniformly.
    # Every discovered .txt pattern becomes a data type keyed by a deterministic
    # sanitization of its filename — there is no hardcoded type whitelist. Future
    # unknown file types are accepted on equal footing with known ones. is_alert
    # is derived from the pattern name (contains 'alert'), not from a lookup table.
    data_types = {}
    for pattern_name, filepath, token_count, header_names in all_patterns:
        dt_key = _sanitize_data_type_key(pattern_name)
        display_label = pattern_name
        is_alert = 'alert' in pattern_name.lower()
        file_pattern = pattern_name

        # Handle key collision: two patterns may sanitize to the same key
        if dt_key in data_types:
            suffix = 1
            while f"{dt_key}_{suffix}" in data_types:
                suffix += 1
            dt_key = f"{dt_key}_{suffix}"

        # Determine column names from header if available
        # Header format: Time [UAVSendID] col1 col2 ... colN
        # Data tokens:    time [uavid] val1 val2 ... valN
        offset = 1 if has_uav else 0
        used_col_names = set()
        if has_header_flag and header_names and len(header_names) >= 2:
            hdr_offset = 1 + (1 if has_uav else 0)
            num_cols = len(header_names) - hdr_offset
            # Sample first data row to detect TEXT vs REAL columns
            col_types = _detect_column_types(filepath, has_header_flag, has_uav, num_cols)
            columns = []
            for i in range(hdr_offset, len(header_names)):
                col_name = _sanitize_column_name(header_names[i])
                # Deduplicate column names
                if col_name in used_col_names:
                    suffix = 1
                    while f"{col_name}_{suffix}" in used_col_names:
                        suffix += 1
                    col_name = f"{col_name}_{suffix}"
                used_col_names.add(col_name)
                col_label = header_names[i]  # Keep original for display
                col_type = col_types[i - hdr_offset] if (i - hdr_offset) < len(col_types) else 'REAL'
                columns.append({
                    'name': col_name,
                    'label': col_label,
                    'unit': '',
                    'type': col_type,
                    'ordinal': i,
                })
        else:
            # No header — use generic col_N names
            columns = []
            for i in range(1, token_count - offset):
                ordinal = i + offset
                col_name = f"col_{ordinal}"
                col_label = f"列{ordinal}"
                columns.append({
                    'name': col_name,
                    'label': col_label,
                    'unit': '',
                    'type': 'REAL',
                    'ordinal': ordinal,
                })

        # Skip types with no usable columns
        if not columns:
            continue

        data_types[dt_key] = {
            'display_label': display_label,
            'file_patterns': [file_pattern],
            'is_alert': is_alert,
            'is_raw': _is_raw_dump(filepath),
            'columns': columns,
        }

    config = {
        'has_header': has_header_flag,
        'has_uav_send_id': has_uav,
        'extract_serial_from_path': True,  # always extract from standardized dir hierarchy
        'data_types': data_types,
    }

    return config


# ══════════════════════════════════════════════════════════════════════════════
# Data type key helpers
# ══════════════════════════════════════════════════════════════════════════════

def get_data_type_key(filename, format_config):
    """Determine which data_type_key a filename matches.

    Uses word-boundary matching: the pattern must appear as a complete word
    in the filename, followed by an underscore and a digit (the session key).
    This prevents "GPSData" from incorrectly matching "SendGPSData".

    Returns (data_type_key, config_entry) or (None, None).
    """
    for tk, tdef in format_config['data_types'].items():
        for pattern in tdef.get('file_patterns', []):
            # Pattern must be a complete word: preceded by start-of-string or
            # underscore, followed by underscore + digit (session key).
            if re.search(rf'(?:^\d*|_){re.escape(pattern)}_\d', filename):
                return tk, tdef
    return None, None


# ══════════════════════════════════════════════════════════════════════════════
# Table name helpers
# ══════════════════════════════════════════════════════════════════════════════

def data_table_name(model_id, data_type_key):
    """Generate the per-model data table name."""
    return f"model_{model_id}_{data_type_key}_data"


# ══════════════════════════════════════════════════════════════════════════════
# Dynamic table creation
# ══════════════════════════════════════════════════════════════════════════════

def _sql_type(col_type):
    """Map config type to SQLite type."""
    t = col_type.upper()
    if t in ('REAL', 'FLOAT', 'DOUBLE'):
        return 'REAL'
    elif t in ('INTEGER', 'INT', 'BOOL', 'BOOLEAN'):
        return 'INTEGER'
    return 'TEXT'


def generate_create_table_sql(model_id, data_type_key, format_config):
    """Generate CREATE TABLE SQL for a model's data type.

    Every table has: id, flight_id, time_str, time_sec + domain columns.
    """
    tdef = format_config['data_types'][data_type_key]
    table_name = data_table_name(model_id, data_type_key)

    cols_sql = [
        "id              INTEGER PRIMARY KEY AUTOINCREMENT",
        "flight_id       INTEGER NOT NULL REFERENCES flights(id) ON DELETE CASCADE",
        "time_str        TEXT NOT NULL",
        "time_sec        REAL NOT NULL",
    ]

    for col in tdef['columns']:
        col_type = _sql_type(col.get('type', 'REAL'))
        col_name = col['name']
        cols_sql.append(f"{col_name:30s} {col_type}")

    col_defs = ",\n    ".join(cols_sql)
    sql = f"""CREATE TABLE IF NOT EXISTS {table_name} (\n    {col_defs}\n)"""
    return sql


def generate_index_sql(model_id, data_type_key):
    """Generate index on (flight_id, time_sec) for fast queries."""
    table_name = data_table_name(model_id, data_type_key)
    idx_name = f"idx_m{model_id}_{data_type_key}_ft"
    return f"CREATE INDEX IF NOT EXISTS {idx_name} ON {table_name}(flight_id, time_sec)"


# ══════════════════════════════════════════════════════════════════════════════
# Column registry and data table registration
# ══════════════════════════════════════════════════════════════════════════════

def register_model_tables(conn, model_id, config=None, commit=True):
    """Create all data tables and populate registry for a new model.

    If a ``config`` dict is provided it is used directly; otherwise the
    config is built from the database (build_model_config_from_db).

    Args:
        conn: SQLite connection
        model_id: aircraft_models.id
        config: optional format config dict (e.g. from generate_config_from_scan)

    Returns:
        int: Number of tables created
    """
    if config is None:
        config = build_model_config_from_db(conn, model_id)

    if config is None or not config.get('data_types'):
        logger.warning(
            f"No config data for model {model_id} — skipping table registration. "
            f"Tables will be created when data is imported."
        )
        return 0

    count = 0

    for data_type_key, tdef in config['data_types'].items():
        table_name = data_table_name(model_id, data_type_key)

        # Create the data table
        create_sql = generate_create_table_sql(model_id, data_type_key, config)
        conn.execute(create_sql)

        # Create index
        index_sql = generate_index_sql(model_id, data_type_key)
        conn.execute(index_sql)

        # Serialize file_patterns for storage
        patterns_json = json.dumps(
            tdef.get('file_patterns', []), ensure_ascii=False
        )
        is_alert = 1 if tdef.get('is_alert') else 0

        # Register in data_table_registry
        conn.execute(
            """INSERT OR REPLACE INTO data_table_registry
               (model_id, data_type_key, table_name, display_label, file_patterns, is_alert)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (model_id, data_type_key, table_name,
             tdef['display_label'], patterns_json, is_alert)
        )

        # Register each column in column_registry
        for col in tdef['columns']:
            col_type = col.get('type', 'REAL')
            ordinal = col.get('ordinal')
            is_numeric = 1 if col_type.upper() in ('REAL', 'INTEGER', 'FLOAT') else 0
            scale_factor = col.get('scale_factor', 1.0)

            conn.execute(
                """INSERT OR REPLACE INTO column_registry
                   (model_id, data_type_key, table_name, column_name,
                    display_label, unit, data_type, ordinal, is_numeric, scale_factor)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    model_id, data_type_key, table_name, col['name'],
                    col['label'], col.get('unit', ''), col_type,
                    ordinal, is_numeric, scale_factor,
                )
            )

        count += 1

    if commit:
        conn.commit()
    logger.info(f"Registered {count} tables for model {model_id}")
    return count


# ══════════════════════════════════════════════════════════════════════════════
# Query helpers
# ══════════════════════════════════════════════════════════════════════════════

def get_table_name(conn, model_id, data_type_key):
    """Resolve data_type_key to actual table name for a model."""
    row = conn.execute(
        "SELECT table_name FROM data_table_registry WHERE model_id=? AND data_type_key=?",
        (model_id, data_type_key)
    ).fetchone()
    return row['table_name'] if row else None


def get_columns_for_model(conn, model_id):
    """Get all columns for a model, grouped by data_type_key.

    Returns:
        [{table, label, columns: [{key, label, unit}]}]
    """
    from collections import OrderedDict

    rows = conn.execute(
        """SELECT dtr.data_type_key, dtr.display_label, dtr.table_name,
                  cr.column_name, cr.display_label as col_label, cr.unit,
                  cr.scale_factor
           FROM data_table_registry dtr
           JOIN column_registry cr ON cr.model_id = dtr.model_id
               AND cr.data_type_key = dtr.data_type_key
           WHERE dtr.model_id = ?
           ORDER BY dtr.data_type_key, cr.ordinal""",
        (model_id,)
    ).fetchall()

    groups = OrderedDict()
    for row in rows:
        tk = row['data_type_key']
        if tk not in groups:
            groups[tk] = {
                'table': row['table_name'],
                'label': row['display_label'],
                'columns': [],
            }
        groups[tk]['columns'].append({
            'key': f"{row['data_type_key']}.{row['column_name']}",
            'label': row['col_label'],
            'unit': row['unit'] or '',
            'scale_factor': row['scale_factor'] if row['scale_factor'] is not None else 1.0,
        })

    return list(groups.values())


def get_columns_for_flight(conn, flight_id):
    """Get available columns for a specific flight, grouped by data type.
    Only returns data types that actually have data for this flight.
    """
    from collections import OrderedDict

    # Get flight's model_id
    flight = conn.execute(
        "SELECT a.model_id FROM flights f JOIN aircraft a ON a.id = f.aircraft_id WHERE f.id = ?",
        (flight_id,)
    ).fetchone()
    if not flight:
        return []

    model_id = flight['model_id']

    rows = conn.execute(
        """SELECT dtr.data_type_key, dtr.display_label, dtr.table_name,
                  cr.column_name, cr.display_label as col_label, cr.unit,
                  cr.scale_factor
           FROM data_table_registry dtr
           JOIN column_registry cr ON cr.model_id = dtr.model_id
               AND cr.data_type_key = dtr.data_type_key
           WHERE dtr.model_id = ?
           ORDER BY dtr.data_type_key, cr.ordinal""",
        (model_id,)
    ).fetchall()

    table_stats = {}
    groups = OrderedDict()
    for row in rows:
        tk = row['data_type_key']
        table_name = row['table_name']

        if tk not in table_stats:
            stat = conn.execute(
                f"""SELECT COUNT(*) as row_count,
                          MIN(time_sec) as start_sec,
                          MAX(time_sec) as end_sec
                   FROM {table_name} WHERE flight_id=?""",
                (flight_id,)
            ).fetchone()
            row_count = stat['row_count'] if stat else 0
            start_sec = stat['start_sec'] if stat else None
            end_sec = stat['end_sec'] if stat else None
            duration = (end_sec - start_sec) if start_sec is not None and end_sec is not None else 0
            table_stats[tk] = {
                'row_count': row_count,
                'start_sec': start_sec,
                'end_sec': end_sec,
                'duration_sec': duration,
            }

        stats = table_stats[tk]
        if stats['row_count'] == 0:
            continue

        if tk not in groups:
            groups[tk] = {
                'data_type_key': tk,
                'table': table_name,
                'label': row['display_label'],
                'row_count': stats['row_count'],
                'duration_sec': stats['duration_sec'],
                'columns': [],
            }
        groups[tk]['columns'].append({
            'key': f"{row['data_type_key']}.{row['column_name']}",
            'label': row['col_label'],
            'unit': row['unit'] or '',
            'scale_factor': row['scale_factor'] if row['scale_factor'] is not None else 1.0,
        })

    return list(groups.values())
