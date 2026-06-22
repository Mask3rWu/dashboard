"""Sync model_1.json column metadata to the database column_registry."""
import json, sqlite3, os, sys

db_path = os.path.join(os.environ['APPDATA'], 'FlightAnalyzer', 'data.db')
print(f'Database: {db_path}')

conn = sqlite3.connect(db_path)
conn.row_factory = sqlite3.Row

# Load updated config
with open(r'backend\configs\model_1.json', 'r', encoding='utf-8') as f:
    config = json.load(f)

updated = 0
for dt_key, tdef in config['data_types'].items():
    for col in tdef['columns']:
        col_name = col['name']
        new_label = col['label']
        new_unit = col.get('unit', '')
        new_type = col.get('type', 'REAL')

        existing = conn.execute(
            "SELECT display_label, unit, data_type FROM column_registry "
            "WHERE model_id=1 AND data_type_key=? AND column_name=?",
            (dt_key, col_name)
        ).fetchone()

        if not existing:
            print(f'  SKIP (not in DB): {dt_key}.{col_name}')
            continue

        old_label, old_unit, old_type = existing
        if old_label != new_label or old_unit != new_unit or old_type != new_type:
            conn.execute(
                "UPDATE column_registry SET display_label=?, unit=?, data_type=? "
                "WHERE model_id=1 AND data_type_key=? AND column_name=?",
                (new_label, new_unit, new_type, dt_key, col_name)
            )
            changes = []
            if old_label != new_label:
                changes.append(f'label "{old_label}" → "{new_label}"')
            if old_unit != new_unit:
                changes.append(f'unit "{old_unit}" → "{new_unit}"')
            if old_type != new_type:
                changes.append(f'type "{old_type}" → "{new_type}"')
            print(f'  {dt_key}.{col_name}: {", ".join(changes)}')
            updated += 1

conn.commit()
conn.close()
print(f'\nDone! {updated} columns updated.')
