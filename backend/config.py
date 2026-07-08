"""File-based runtime configuration for local and server deployments."""

from __future__ import annotations

import configparser
import os
import sys
from pathlib import Path
from urllib.parse import quote_plus


CONFIG_FILENAME = "flight_analyzer.ini"


def _candidate_paths(explicit_path: str | os.PathLike[str] | None = None) -> list[Path]:
    paths: list[Path] = []
    if explicit_path:
        paths.append(Path(explicit_path))

    env_path = os.environ.get("FLIGHT_ANALYZER_CONFIG")
    if env_path:
        paths.append(Path(env_path))

    if getattr(sys, "frozen", False):
        paths.append(Path(sys.executable).resolve().parent / CONFIG_FILENAME)

    paths.append(Path.cwd() / CONFIG_FILENAME)
    paths.append(Path(__file__).resolve().parent.parent / CONFIG_FILENAME)

    seen: set[Path] = set()
    unique: list[Path] = []
    for path in paths:
        resolved = path.expanduser().resolve()
        if resolved not in seen:
            seen.add(resolved)
            unique.append(resolved)
    return unique


def _setenv_if_present(name: str, value: str | None) -> None:
    if name in os.environ:
        return
    text = (value or "").strip()
    if text:
        os.environ[name] = text


def _mysql_url(parser: configparser.ConfigParser) -> str:
    section = parser["mysql"]
    explicit_url = section.get("url", "").strip()
    if explicit_url:
        return explicit_url

    host = section.get("host", "127.0.0.1").strip() or "127.0.0.1"
    port = section.get("port", "3306").strip() or "3306"
    database = section.get("database", "flight_analyzer").strip() or "flight_analyzer"
    user = section.get("user", "flight").strip() or "flight"
    password = section.get("password", "").strip()
    charset = section.get("charset", "utf8mb4").strip() or "utf8mb4"
    return (
        f"mysql+pymysql://{quote_plus(user)}:{quote_plus(password)}"
        f"@{host}:{port}/{quote_plus(database)}?charset={quote_plus(charset)}"
    )


def load_app_config(path: str | os.PathLike[str] | None = None) -> Path | None:
    """Load ``flight_analyzer.ini`` into the existing environment contract.

    Existing environment variables win over file values so deployment scripts can
    still override individual settings.
    """

    config_path = next((candidate for candidate in _candidate_paths(path) if candidate.exists()), None)
    if config_path is None:
        return None

    parser = configparser.ConfigParser(interpolation=None)
    with config_path.open("r", encoding="utf-8") as f:
        parser.read_file(f)

    if parser.has_section("local"):
        _setenv_if_present("DATA_DIR", parser.get("local", "data_dir", fallback=""))
        _setenv_if_present("SERVER_BASE_URL", parser.get("local", "server_base_url", fallback=""))
        _setenv_if_present("SYNC_ENABLED", parser.get("local", "sync_enabled", fallback=""))

    if parser.has_section("server"):
        _setenv_if_present("SERVER_HOST", parser.get("server", "host", fallback=""))
        _setenv_if_present("SERVER_PORT", parser.get("server", "port", fallback=""))
        _setenv_if_present("SERVER_DATA_DIR", parser.get("server", "data_dir", fallback=""))

    if parser.has_section("mysql"):
        _setenv_if_present("SERVER_DB_URL", _mysql_url(parser))

    if parser.has_section("dev"):
        _setenv_if_present(
            "BUILTIN_MODEL_SEEDS_ENABLED",
            parser.get("dev", "load_local", fallback=""),
        )
        _setenv_if_present(
            "SERVER_BUILTIN_MODEL_SEEDS_ENABLED",
            parser.get("dev", "load_server", fallback=""),
        )

    return config_path


def server_host(default: str = "0.0.0.0") -> str:
    return (os.environ.get("SERVER_HOST") or default).strip() or default


def server_port(default: int = 9000) -> int:
    value = (os.environ.get("SERVER_PORT") or "").strip()
    if not value:
        return default
    try:
        return int(value)
    except ValueError as exc:
        raise RuntimeError(f"SERVER_PORT must be an integer, got {value!r}") from exc
