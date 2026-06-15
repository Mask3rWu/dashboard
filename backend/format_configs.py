"""Format config loader, dynamic table creation, and column registry management.

Three format categories (A/B/C) correspond to three aircraft models.
Each format config JSON defines:
  - format metadata (encoding, has_uav_send_id, has_header)
  - data_types: {key: {display_label, file_patterns, columns: [{name, label, unit, type, ordinal}]}}
"""

import os
import json
import logging

logger = logging.getLogger(__name__)

CONFIG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'configs')
FORMAT_FILES = {
    'A': 'format_A.json',
    'B': 'format_B.json',
    'C': 'format_C.json',
}


def load_format_config(format_category):
    """Load a format configuration JSON file.

    Args:
        format_category: 'A', 'B', or 'C'

    Returns:
        dict: The parsed format config
    """
    if format_category not in FORMAT_FILES:
        raise ValueError(f"Unknown format category: {format_category}")

    path = os.path.join(CONFIG_DIR, FORMAT_FILES[format_category])
    if not os.path.exists(path):
        raise FileNotFoundError(f"Format config not found: {path}")

    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)


def get_data_type_key(filename, format_config):
    """Determine which data_type_key a filename matches.

    Returns (data_type_key, config_entry) or (None, None).
    """
    for tk, tdef in format_config['data_types'].items():
        for pattern in tdef.get('file_patterns', []):
            if pattern in filename:
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

def register_model_tables(conn, model_id, format_category):
    """Create all data tables and populate registry for a new model.

    Args:
        conn: SQLite connection
        model_id: aircraft_models.id
        format_category: 'A', 'B', or 'C'

    Returns:
        int: Number of tables created
    """
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
    logger.info(f"Registered {count} tables for model {model_id} (format {format_category})")
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
