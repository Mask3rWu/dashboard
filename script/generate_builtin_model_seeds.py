"""Generate backend/builtin_model_seeds.json from all models on the server."""

from __future__ import annotations

import argparse
import configparser
import json
import os
import sys
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.config import load_app_config
from backend import runtime_context
from backend.sync import client as sync_client


DEFAULT_OUTPUT = ROOT / "backend" / "builtin_model_seeds.json"


def _read_ini_auth() -> dict[str, str]:
    path = load_app_config()
    if path is None:
        return {}
    parser = configparser.ConfigParser(interpolation=None)
    parser.read(path, encoding="utf-8")
    if not parser.has_section("server_auth"):
        return {}
    return {
        "username": parser.get("server_auth", "username", fallback="").strip(),
        "password": parser.get("server_auth", "password", fallback=""),
        "token": parser.get("server_auth", "token", fallback="").strip(),
    }


def _default_server_url() -> str:
    value = os.environ.get("SERVER_BASE_URL", "").strip()
    if value:
        return runtime_context._normalize_base_url(value)
    raise SystemExit("SERVER_BASE_URL is not configured. Pass --server-url or fill flight_analyzer.ini.")


def _read_models_from_bundle(bundle_path: Path) -> list[dict]:
    with zipfile.ZipFile(bundle_path, "r") as zf:
        manifest = json.loads(zf.read("manifest.json").decode("utf-8"))
        models = []
        for row in manifest.get("models") or []:
            server_id = int(row["id"])
            config_path = f"models/model_{server_id}.json"
            try:
                config = json.loads(zf.read(config_path).decode("utf-8"))
            except KeyError:
                config = {
                    "has_header": bool(row.get("has_header", 1)),
                    "has_uav_send_id": bool(row.get("has_uav_send_id", 0)),
                    "extract_serial_from_path": bool(row.get("extract_serial_from_path", 0)),
                    "data_types": {},
                }
            models.append(
                {
                    "name": row.get("name") or f"server_model_{server_id}",
                    "server_id": server_id,
                    "server_version": row.get("version") or 1,
                    "client_uid": row.get("client_uid"),
                    "source_node_id": row.get("source_node_id") or manifest.get("source_node_id") or "server",
                    "created_at": row.get("created_at"),
                    "updated_at": row.get("updated_at"),
                    "config": config,
                }
            )
        return models


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--server-url", default=None, help="Server API base URL, for example http://host:9000/api")
    parser.add_argument("--username", default=None, help="Server username. If omitted, --token is required.")
    parser.add_argument("--password", default=None, help="Server password.")
    parser.add_argument("--token", default=None, help="Existing server bearer token.")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT), help="Output JSON path.")
    args = parser.parse_args()

    ini_auth = _read_ini_auth()
    server_url = sync_client.normalize_base_url(args.server_url or _default_server_url())
    token = args.token or ini_auth.get("token")
    if not token:
        username = args.username or ini_auth.get("username")
        password = args.password if args.password is not None else ini_auth.get("password")
        if not username or password is None:
            raise SystemExit(
                "Server auth is not configured. Fill [server_auth] in flight_analyzer.ini, "
                "or pass --token, or pass both --username and --password."
            )
        auth = sync_client.login(server_url, username, password)
        token = auth.get("token")
        if not token:
            raise SystemExit("Server login succeeded but no token was returned.")

    with tempfile.TemporaryDirectory(prefix="flightanalyzer_seed_") as tmp:
        bundle_path = Path(tmp) / "server_models.fapkg"
        sync_client.download_bundle(server_url, 0, str(bundle_path), token=token)
        models = _read_models_from_bundle(bundle_path)
    users_payload = sync_client.list_users(server_url, token=token)
    users = []
    for item in users_payload.get("users") or []:
        if not item.get("disabled_at"):
            users.append(
                {
                    "id": item.get("id"),
                    "username": item.get("username"),
                    "password_hash": item.get("password_hash"),
                    "role": item.get("role"),
                    "created_at": item.get("created_at"),
                    "password_changed_at": item.get("password_changed_at"),
                }
            )

    payload = {
        "version": 1,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "source_server_url": server_url,
        "source_node_id": "server",
        "users": users,
        "models": models,
    }
    output = Path(args.output).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote {len(models)} model seed(s) and {len(users)} user seed(s) to {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
