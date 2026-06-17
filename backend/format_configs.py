"""Format config loader, dynamic table creation, and column registry management.

Each aircraft model has its own format config JSON (1:1 relationship).
Configs are stored as configs/model_{id}.json.

Legacy format configs (configs/format_A.json, etc.) are supported for backward
compatibility — they are migrated to per-model configs during v3 migration.
"""

import os
import json
import logging

logger = logging.getLogger(__name__)

CONFIG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'configs')


# ── Config path helpers ──

def model_config_path(model_id):
    """Generate the config filename for a model: model_{id}.json"""
    return f"model_{model_id}.json"


def legacy_format_path(format_category):
    """Generate legacy config filename: format_{cat}.json"""
    return f"format_{format_category}.json"


# ── Load functions ──

def load_format_config(format_category):
    """Load a format config by category string.

    First tries configs/format_{cat}.json (legacy), then configs/model_{cat}.json
    (if category happens to be a model name that matches a file).

    Args:
        format_category: e.g. 'A', 'B', 'C' (legacy) or a model name

    Returns:
        dict: The parsed format config

    Raises:
        FileNotFoundError: if no config file found
    """
    # Try legacy format_{cat}.json
    legacy = os.path.join(CONFIG_DIR, legacy_format_path(format_category))
    if os.path.exists(legacy):
        with open(legacy, 'r', encoding='utf-8') as f:
            return json.load(f)

    # Try model_{cat}.json
    model_file = os.path.join(CONFIG_DIR, f"model_{format_category}.json")
    if os.path.exists(model_file):
        with open(model_file, 'r', encoding='utf-8') as f:
            return json.load(f)

    raise FileNotFoundError(
        f"Format config not found for '{format_category}': "
        f"tried {legacy} and {model_file}"
    )


def load_format_config_by_model(conn, model_id):
    """Load format config for a model by looking up config_path from DB.

    Args:
        conn: SQLite connection
        model_id: aircraft_models.id

    Returns:
        dict: The parsed format config, or None if not found
    """
    row = conn.execute(
        "SELECT config_path, format_category FROM aircraft_models WHERE id=?",
        (model_id,)
    ).fetchone()
    if not row:
        return None

    # Try config_path first
    if row['config_path']:
        path = os.path.join(CONFIG_DIR, row['config_path'])
        if os.path.exists(path):
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
        logger.warning(f"Config path not found: {path}, falling back to format_category")

    # Fallback: load by format_category
    return load_format_config(row['format_category'])


def load_format_config_from_path(config_path):
    """Load a format config from a named path relative to CONFIG_DIR."""
    path = os.path.join(CONFIG_DIR, config_path)
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)


def load_all_format_configs():
    """Load all known format configs from configs/ directory.

    Returns:
        dict: {format_category: config_dict, ...}
    """
    configs = {}
    if not os.path.isdir(CONFIG_DIR):
        return configs

    for fname in os.listdir(CONFIG_DIR):
        if not fname.endswith('.json'):
            continue
        path = os.path.join(CONFIG_DIR, fname)
        try:
            with open(path, 'r', encoding='utf-8') as f:
                config = json.load(f)
        except Exception as e:
            logger.warning(f"Failed to load config {fname}: {e}")
            continue

        # Determine key: format_A.json → 'A', model_1.json → config['format']
        if fname.startswith('format_'):
            # format_A.json → 'A' (takes priority over model files for same key)
            key = fname.replace('format_', '').replace('.json', '')
            configs[key] = config
        elif fname.startswith('model_'):
            # model_{id}.json — use the format field from the config
            # Only add if no format_*.json exists for this key (format files take priority)
            key = config.get('format', fname.replace('.json', ''))
            if key not in configs:
                configs[key] = config
        else:
            key = fname.replace('.json', '')
            configs[key] = config

    return configs


# ── Config comparison ──

def compare_configs(generated, existing):
    """Compare an auto-generated config against an existing model config.

    Returns a similarity score 0.0–1.0.  Score >= 0.95 is considered a match.

    Components (data types are the core dimension):
      - Data type Jaccard (0.60): overlap of data_type_keys
      - Structure flags (0.25): has_header + has_uav_send_id exact match
      - Column count agreement (0.15): average ratio per shared data type
    """
    # ── Data type Jaccard (0.60) — core dimension ──
    gen_keys = set(generated.get('data_types', {}).keys())
    exist_keys = set(existing.get('data_types', {}).keys())
    if gen_keys | exist_keys:
        jaccard = len(gen_keys & exist_keys) / len(gen_keys | exist_keys)
    else:
        jaccard = 0.0

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

    return 0.60 * jaccard + 0.25 * structure_score + 0.15 * column_score


def load_all_model_configs_with_ids(conn):
    """Load all existing model configs with their database IDs.

    Returns:
        list of (model_id, model_name, format_category, config_dict)
    """
    models = []
    rows = conn.execute(
        "SELECT id, name, format_category, config_path FROM aircraft_models"
    ).fetchall()
    for row in rows:
        try:
            config = load_format_config_by_model(conn, row['id'])
            if config:
                models.append((row['id'], row['name'], row['format_category'], config))
        except Exception:
            pass
    return models


# ── Config CRUD ──

def save_model_config(model_id, config_data):
    """Write a format config JSON to configs/model_{model_id}.json.

    Args:
        model_id: aircraft_models.id
        config_data: dict with format config structure

    Returns:
        str: relative config path (e.g. 'model_1.json')
    """
    rel_path = model_config_path(model_id)
    full_path = os.path.join(CONFIG_DIR, rel_path)
    with open(full_path, 'w', encoding='utf-8') as f:
        json.dump(config_data, f, ensure_ascii=False, indent=2)
    logger.info(f"Saved config: {full_path}")
    return rel_path


def delete_model_config(config_path):
    """Delete a model's config file from disk.

    Args:
        config_path: relative path (e.g. 'model_1.json')
    """
    if not config_path:
        return
    full_path = os.path.join(CONFIG_DIR, config_path)
    if os.path.exists(full_path):
        try:
            os.remove(full_path)
            logger.info(f"Deleted config: {full_path}")
        except Exception as e:
            logger.warning(f"Failed to delete config {full_path}: {e}")


# ── Auto-generate config from scan ──

def _discover_file_patterns(source_path):
    """Scan a folder and discover unique file type patterns.

    Returns:
        list of (pattern_name, sample_filepath, token_count, header_names_or_none)
    """
    patterns = {}
    for root, _dirs, files in os.walk(source_path):
        for fname in files:
            if not fname.endswith('.txt'):
                continue
            # Extract base pattern: "DroneStateData_114430.txt" → "DroneStateData"
            base = fname.rsplit('.txt', 1)[0]
            # Find the type marker — everything before the last underscore-separated timestamp
            import re
            match = re.match(r'^(.+?)_(\d{4,})', base)
            pattern_name = match.group(1) if match else base

            if pattern_name not in patterns:
                filepath = os.path.join(root, fname)
                try:
                    from backend.scanner import detect_encoding, has_header, parse_lines
                    lines = parse_lines(filepath)
                    if lines:
                        hdr = has_header(filepath)
                        start = 1 if hdr else 0
                        if start < len(lines):
                            token_count = len(lines[start].split())
                            # Read header names if available
                            header_names = None
                            if hdr:
                                header_tokens = lines[0].split()
                                # Skip 'Time' and optional 'UAVSendID' for data columns
                                header_names = header_tokens
                            patterns[pattern_name] = (pattern_name, filepath, token_count, header_names)
                except Exception:
                    patterns[pattern_name] = (pattern_name, filepath, 0, None)

    return list(patterns.values())


def _detect_has_header(source_path, sample_patterns):
    """Detect whether data files in this folder have headers."""
    for entry in sample_patterns[:3]:
        _name, filepath = entry[0], entry[1]
        try:
            from backend.scanner import has_header
            if has_header(filepath):
                return True
        except Exception:
            pass
    return False


def _detect_has_uav_send_id(source_path, sample_patterns, has_header_flag):
    """Detect if data files have a UAVSendID column.

    Strategy:
      - If headers exist: ONLY check header names for UAV/droneid keywords.
        Do NOT fallback to data-value checks — a numeric second column
        (e.g. GPS velocity = 0) is NOT a UAV ID.
      - If no headers: check whether the second data token is a pure integer
        (not a float/decimal) AND is reasonably small (<= 65535), which is
        consistent with a UAV ID rather than a sensor reading.
    """
    for entry in sample_patterns[:5]:
        _name, filepath = entry[0], entry[1]

        # If headers exist, ONLY trust header names
        if has_header_flag:
            try:
                from backend.scanner import parse_lines
                lines = parse_lines(filepath)
                if lines:
                    header_tokens = lines[0].split()
                    if len(header_tokens) >= 2:
                        second_header = header_tokens[1].lower()
                        if 'uav' in second_header or 'droneid' in second_header:
                            return True
                # No fallback — if header doesn't say UAV, there is no UAV column
            except Exception:
                pass
            continue

        # No headers: check if second data token is a small pure integer (UAV ID)
        try:
            from backend.scanner import parse_lines
            lines = parse_lines(filepath)
            if lines:
                start = 1 if has_header_flag else 0
                if start < len(lines):
                    tokens = lines[start].split()
                    if len(tokens) >= 2:
                        v = tokens[1]
                        if v.lower().startswith('uav') or v.lower().startswith('drone'):
                            return True
                        # Must be a pure integer (no decimal, no scientific notation)
                        if v.lstrip('-').isdigit():
                            val = int(v)
                            if 0 <= val <= 65535:
                                return True
        except Exception:
            pass
    return False


def _sanitize_column_name(name):
    """Sanitize a column name for SQL: only allow alphanumeric and underscore.
    Replaces non-alphanumeric chars with '_', strips leading digits."""
    import re
    cleaned = re.sub(r'[^\w]', '_', name)
    cleaned = re.sub(r'_+', '_', cleaned)
    cleaned = re.sub(r'^(\d+)', r'c\1', cleaned)
    return cleaned


def generate_config_from_scan(source_path):
    """Analyze a folder and generate a format config JSON from discovered file patterns.

    This is used when scanning an unknown format — the config is a best-effort
    auto-detection that the user can refine later.

    Args:
        source_path: Root folder to analyze

    Returns:
        dict: A format config structure
    """
    # Map known patterns to data type keys
    KNOWN_TYPES = {
        'DroneStateData': 'drone_state',
        'GPSData': 'gps',
        'IMUData': 'imu',
        'PosData': 'pos',
        'EngineData': 'engine',
        'PowerBoxData': 'powerbox',
        'DualAntennaData': 'dual_antenna',
        'AvionicsData': 'avionics',
        'ControllerData': 'controller',
        'FanControlData': 'fan_control',
        'FlightAlertInfo': 'alert',
        'GPSCompareData': 'gps_compare',
    }

    # Step 1: discover all file patterns
    all_patterns = _discover_file_patterns(source_path)

    # Step 2: filter to known data types only
    filtered = []
    for p_name, fp, tc, hdr in all_patterns:
        matched = None
        for kt in KNOWN_TYPES:
            if p_name.endswith(kt):
                matched = kt
                break
        if matched:
            filtered.append((p_name, fp, tc, hdr))
        else:
            logger.info(f"Skipping unknown file pattern: {p_name}")

    # Step 3: detect header and UAV from FILTERED, non-alert patterns
    non_alert = [(n, fp, tc, hdr) for n, fp, tc, hdr in filtered
                 if not n.endswith('FlightAlertInfo')]
    has_header_flag = _detect_has_header(source_path, non_alert) if non_alert else False
    has_uav = _detect_has_uav_send_id(source_path, non_alert, has_header_flag) if non_alert else False

    # Step 4: build data types from filtered patterns
    data_types = {}
    for pattern_name, filepath, token_count, header_names in filtered:
        matched_type = None
        for kt in KNOWN_TYPES:
            if pattern_name.endswith(kt):
                matched_type = kt
                break
        dt_key = KNOWN_TYPES[matched_type]

        display_label = {
            'drone_state': '飞控状态',
            'gps': 'GPS',
            'imu': 'IMU',
            'pos': '位置',
            'engine': '发动机',
            'powerbox': '电源',
            'dual_antenna': '双天线',
            'avionics': '航电',
            'controller': '舵机',
            'fan_control': '风扇',
            'alert': '告警',
            'gps_compare': 'GPS对比',
        }.get(dt_key, pattern_name)

        is_alert = (matched_type == 'FlightAlertInfo')

        # Determine column names from header if available
        # Header format: Time [UAVSendID] col1 col2 ... colN
        # Data tokens:    time [uavid] val1 val2 ... valN
        offset = 1 if has_uav else 0
        if has_header_flag and header_names and len(header_names) >= 2:
            # Skip Time(0) and optional UAVSendID(1), rest are data columns
            hdr_offset = 1 + (1 if has_uav else 0)
            columns = []
            for i in range(hdr_offset, len(header_names)):
                col_name = _sanitize_column_name(header_names[i])
                col_label = header_names[i]  # Keep original for display
                columns.append({
                    'name': col_name,
                    'label': col_label,
                    'unit': '',
                    'type': 'REAL',
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

        data_types[dt_key] = {
            'display_label': display_label,
            'file_patterns': [matched_type],  # generic name, not folder-specific prefix
            'is_alert': is_alert,
            'columns': columns,
        }

    config = {
        'format': os.path.basename(source_path),
        'description': f'Auto-generated from {os.path.basename(source_path)}',
        'has_header': has_header_flag,
        'has_uav_send_id': has_uav,
        'extract_serial_from_path': has_uav,  # heuristic: if UAVSendID, likely Format A style
        'has_aircraft_prefix': has_uav,
        'encoding': 'gbk',
        'data_types': data_types,
    }

    return config


# ── Data type key helpers ──

def get_data_type_key(filename, format_config):
    """Determine which data_type_key a filename matches.

    Uses word-boundary matching: the pattern must appear as a complete word
    in the filename, followed by an underscore and a digit (the session key).
    This prevents "GPSData" from incorrectly matching "SendGPSData".

    Returns (data_type_key, config_entry) or (None, None).
    """
    import re
    for tk, tdef in format_config['data_types'].items():
        for pattern in tdef.get('file_patterns', []):
            # Pattern must be a complete word: preceded by start-of-string or
            # underscore, followed by underscore + digit (session key).
            if re.search(rf'(?:^|_){re.escape(pattern)}_\d', filename):
                return tk, tdef
    return None, None


# ── Table name helpers ──

def data_table_name(model_id, data_type_key):
    """Generate the per-model data table name."""
    return f"model_{model_id}_{data_type_key}_data"


# ── Dynamic table creation ──

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


# ── Column registry population ──

def register_model_tables(conn, model_id, format_category, config_path=None):
    """Create all data tables and populate registry for a new model.

    Args:
        conn: SQLite connection
        model_id: aircraft_models.id
        format_category: format category string (used as fallback when no config_path)
        config_path: optional path to per-model config file

    Returns:
        int: Number of tables created
    """
    if config_path:
        config = load_format_config_from_path(config_path)
    else:
        config = load_format_config(format_category)

    count = 0

    for data_type_key, tdef in config['data_types'].items():
        table_name = data_table_name(model_id, data_type_key)

        # Create the data table
        create_sql = generate_create_table_sql(model_id, data_type_key, config)
        conn.execute(create_sql)

        # Create index
        index_sql = generate_index_sql(model_id, data_type_key)
        conn.execute(index_sql)

        # Register in data_table_registry
        conn.execute(
            """INSERT OR REPLACE INTO data_table_registry
               (model_id, data_type_key, table_name, display_label)
               VALUES (?, ?, ?, ?)""",
            (model_id, data_type_key, table_name, tdef['display_label'])
        )

        # Register each column in column_registry
        for col in tdef['columns']:
            col_type = col.get('type', 'REAL')
            ordinal = col.get('ordinal')
            is_numeric = 1 if col_type.upper() in ('REAL', 'INTEGER', 'FLOAT') else 0

            conn.execute(
                """INSERT OR REPLACE INTO column_registry
                   (model_id, data_type_key, table_name, column_name,
                    display_label, unit, data_type, ordinal, is_numeric)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    model_id, data_type_key, table_name, col['name'],
                    col['label'], col.get('unit', ''), col_type,
                    ordinal, is_numeric,
                )
            )

        count += 1

    conn.commit()
    logger.info(f"Registered {count} tables for model {model_id}")
    return count


# ── Query helpers ──

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
                  cr.column_name, cr.display_label as col_label, cr.unit
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
                  cr.column_name, cr.display_label as col_label, cr.unit
           FROM data_table_registry dtr
           JOIN column_registry cr ON cr.model_id = dtr.model_id
               AND cr.data_type_key = dtr.data_type_key
           WHERE dtr.model_id = ?
           ORDER BY dtr.data_type_key, cr.ordinal""",
        (model_id,)
    ).fetchall()

    groups = OrderedDict()
    for row in rows:
        # Check if this table has data
        cnt = conn.execute(
            f"SELECT COUNT(*) as cnt FROM {row['table_name']} WHERE flight_id=? LIMIT 1",
            (flight_id,)
        ).fetchone()
        if not cnt or cnt['cnt'] == 0:
            continue

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
        })

    return list(groups.values())
