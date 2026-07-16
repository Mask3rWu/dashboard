"""Shared sync protocol versions, manifest validation, and archive safety."""

from __future__ import annotations

import hashlib
import re
import zipfile
from pathlib import PurePosixPath
from typing import Any


PACKAGE_VERSION = 2
SYNC_PROTOCOL_VERSION = 1
CURRENT_SCHEMA_VERSION = 4

_WINDOWS_DRIVE = re.compile(r"^[A-Za-z]:")
_LOCAL_REQUIRED_FIELDS = (
    "package_version",
    "schema_version",
    "models",
    "aircraft",
    "flights",
    "raw_files",
    "parsed_data",
)


class SyncProtocolError(ValueError):
    """Raised when a package violates the shared synchronization protocol."""


def safe_zip_path(path: str) -> str:
    raw = str(path or "").replace("\\", "/")
    if raw.startswith("/") or _WINDOWS_DRIVE.match(raw):
        raise SyncProtocolError(f"Unsafe zip path: {path}")
    parts = PurePosixPath(raw).parts
    if not parts or any(part in ("", ".", "..") for part in parts):
        raise SyncProtocolError(f"Unsafe zip path: {path}")
    return "/".join(parts)


def assert_safe_zip(archive: zipfile.ZipFile) -> None:
    for info in archive.infolist():
        if not info.is_dir():
            safe_zip_path(info.filename)


def sha256_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as file:
        while chunk := file.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def is_local_manifest_compatible(manifest: dict, schema_version: int) -> bool:
    return (
        int(manifest.get("package_version") or 0) == PACKAGE_VERSION
        and int(manifest.get("schema_version") or 0) == schema_version
        and manifest.get("parsed_data", {}).get("format") == "sqlite"
    )


def validate_local_manifest(manifest: dict, schema_version: int, require_compatible: bool) -> None:
    for key in _LOCAL_REQUIRED_FIELDS:
        if key not in manifest:
            raise SyncProtocolError(f"manifest 缺少字段: {key}")
    for key in ("models", "aircraft", "flights", "raw_files"):
        if not isinstance(manifest.get(key), list):
            raise SyncProtocolError(f"manifest 字段格式无效: {key}")
        if any(not isinstance(item, dict) for item in manifest.get(key, [])):
            raise SyncProtocolError(f"manifest 字段包含无效条目: {key}")
    if not isinstance(manifest.get("parsed_data"), dict):
        raise SyncProtocolError("manifest 字段格式无效: parsed_data")
    if require_compatible and not is_local_manifest_compatible(manifest, schema_version):
        raise SyncProtocolError("当前版本暂不支持该同步包的原始文件重解析导入路径")


def validate_server_manifest(
    manifest: dict[str, Any], *, require_push_batch: bool = True
) -> dict[str, Any]:
    if not isinstance(manifest, dict):
        raise SyncProtocolError("manifest must be a JSON object")
    if int(manifest.get("package_version") or 0) < PACKAGE_VERSION:
        raise SyncProtocolError("Only package_version >= 2 is supported")
    if int(manifest.get("sync_protocol_version") or 0) != SYNC_PROTOCOL_VERSION:
        raise SyncProtocolError("Unsupported sync_protocol_version")
    package_id = str(manifest.get("package_id") or "").strip()
    source_node_id = str(manifest.get("source_node_id") or "").strip()
    if not package_id:
        raise SyncProtocolError("manifest.package_id is required")
    if not source_node_id:
        raise SyncProtocolError("manifest.source_node_id is required")
    if require_push_batch and str(manifest.get("bundle_kind") or "") != "push_batch":
        raise SyncProtocolError("Only bundle_kind=push_batch can be pushed to the server")
    parsed = manifest.get("parsed_data") or {}
    if parsed.get("format") != "sqlite":
        raise SyncProtocolError("manifest.parsed_data.format must be sqlite")
    parsed["path"] = safe_zip_path(parsed.get("path") or "data/parsed.sqlite")
    for raw in manifest.get("raw_files") or []:
        if raw.get("package_path"):
            raw["package_path"] = safe_zip_path(raw["package_path"])
        if raw.get("storage_rel_path"):
            raw["storage_rel_path"] = safe_zip_path(raw["storage_rel_path"])
    return manifest
