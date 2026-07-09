"""Local client helpers for pushing sync bundles to the collaboration server."""

from __future__ import annotations

import json
import mimetypes
import os
import uuid
import zipfile
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


class SyncClientError(RuntimeError):
    """Raised when the local sync proxy cannot complete a server request."""

    def __init__(self, message: str, *, status_code: int | None = None, response: Any = None):
        super().__init__(message)
        self.status_code = status_code
        self.response = response

    def to_error_json(self, phase: str) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "phase": phase,
            "message": str(self),
        }
        if self.status_code is not None:
            payload["status_code"] = self.status_code
        if self.response is not None:
            payload["response"] = self.response
        return payload


def normalize_base_url(base_url: str | None) -> str:
    text = (base_url or "").strip().rstrip("/")
    if not text:
        raise SyncClientError("SERVER_BASE_URL is not configured")
    return text


def read_bundle_manifest(bundle_path: str) -> dict[str, Any]:
    try:
        with zipfile.ZipFile(bundle_path, "r") as zf:
            with zf.open("manifest.json") as f:
                manifest = json.loads(f.read().decode("utf-8"))
    except FileNotFoundError as exc:
        raise SyncClientError(f"Bundle does not exist: {bundle_path}") from exc
    except KeyError as exc:
        raise SyncClientError("Bundle is missing manifest.json") from exc
    except (zipfile.BadZipFile, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise SyncClientError(f"Cannot read bundle manifest: {exc}") from exc
    if not isinstance(manifest, dict):
        raise SyncClientError("Bundle manifest must be a JSON object")
    return manifest


def _auth_headers(token: str | None) -> dict[str, str]:
    token = (token or "").strip()
    if not token:
        return {}
    if token.lower().startswith("bearer "):
        return {"Authorization": token}
    return {"Authorization": f"Bearer {token}"}


def _decode_response(raw: bytes) -> Any:
    text = raw.decode("utf-8", errors="replace")
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"raw": text}


def _request_json(
    url: str,
    payload: dict[str, Any],
    *,
    token: str | None,
    timeout: float,
    method: str = "POST",
) -> dict[str, Any]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        **_auth_headers(token),
    }
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            parsed = _decode_response(resp.read())
    except urllib.error.HTTPError as exc:
        parsed = _decode_response(exc.read())
        message = parsed.get("detail") if isinstance(parsed, dict) else None
        raise SyncClientError(message or f"Server returned HTTP {exc.code}", status_code=exc.code, response=parsed) from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise SyncClientError(f"Cannot reach sync server: {exc}") from exc
    if not isinstance(parsed, dict):
        raise SyncClientError("Server returned a non-object JSON response", response=parsed)
    return parsed


def _request_get_json(
    url: str,
    *,
    token: str | None,
    timeout: float,
) -> dict[str, Any]:
    headers = {
        "Accept": "application/json",
        **_auth_headers(token),
    }
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            parsed = _decode_response(resp.read())
    except urllib.error.HTTPError as exc:
        parsed = _decode_response(exc.read())
        message = parsed.get("detail") if isinstance(parsed, dict) else None
        raise SyncClientError(message or f"Server returned HTTP {exc.code}", status_code=exc.code, response=parsed) from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise SyncClientError(f"Cannot reach sync server: {exc}") from exc
    if not isinstance(parsed, dict):
        raise SyncClientError("Server returned a non-object JSON response", response=parsed)
    return parsed


def auth_me(
    base_url: str,
    *,
    token: str | None = None,
    timeout: float = 30.0,
) -> dict[str, Any]:
    url = f"{normalize_base_url(base_url)}/auth/me"
    return _request_get_json(url, token=token, timeout=timeout)


def login(
    base_url: str,
    username: str,
    password: str,
    *,
    timeout: float = 30.0,
) -> dict[str, Any]:
    url = f"{normalize_base_url(base_url)}/auth/login"
    return _request_json(url, {"username": username, "password": password}, token=None, timeout=timeout)


def logout(
    base_url: str,
    *,
    token: str | None = None,
    timeout: float = 30.0,
) -> dict[str, Any]:
    url = f"{normalize_base_url(base_url)}/auth/logout"
    return _request_json(url, {}, token=token, timeout=timeout)


def list_users(
    base_url: str,
    *,
    token: str | None = None,
    timeout: float = 30.0,
) -> dict[str, Any]:
    url = f"{normalize_base_url(base_url)}/users"
    return _request_get_json(url, token=token, timeout=timeout)


def change_password(
    base_url: str,
    old_password: str,
    new_password: str,
    *,
    token: str | None = None,
    timeout: float = 30.0,
) -> dict[str, Any]:
    url = f"{normalize_base_url(base_url)}/auth/change-password"
    return _request_json(
        url,
        {"old_password": old_password, "new_password": new_password},
        token=token,
        timeout=timeout,
    )


def preflight(
    base_url: str,
    manifest: dict[str, Any],
    *,
    token: str | None = None,
    timeout: float = 30.0,
) -> dict[str, Any]:
    url = f"{normalize_base_url(base_url)}/sync/preflight"
    return _request_json(url, {"manifest": manifest, "client_cursor": manifest.get("base_server_cursor")}, token=token, timeout=timeout)


def delete_entity(
    base_url: str,
    entity_type: str,
    server_id: int,
    *,
    reason: str | None = None,
    token: str | None = None,
    timeout: float = 30.0,
) -> dict[str, Any]:
    endpoint = {
        "model": "models",
        "aircraft": "aircraft",
        "flight": "flights",
    }.get(entity_type)
    if endpoint is None:
        raise SyncClientError(f"Unsupported delete entity type: {entity_type}")
    url = f"{normalize_base_url(base_url)}/{endpoint}/{int(server_id)}"
    return _request_json(url, {"reason": reason or ""}, token=token, timeout=timeout, method="DELETE")


def _multipart_body(field_name: str, file_path: str) -> tuple[bytes, str]:
    boundary = f"----FlightAnalyzerSync{uuid.uuid4().hex}"
    filename = os.path.basename(file_path) or "bundle.fapkg"
    content_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    parts = [
        f"--{boundary}\r\n".encode("ascii"),
        (
            f'Content-Disposition: form-data; name="{field_name}"; filename="{filename}"\r\n'
            f"Content-Type: {content_type}\r\n\r\n"
        ).encode("utf-8"),
    ]
    with open(file_path, "rb") as f:
        parts.append(f.read())
    parts.extend([b"\r\n", f"--{boundary}--\r\n".encode("ascii")])
    return b"".join(parts), boundary


def push_bundle(
    base_url: str,
    bundle_path: str,
    *,
    token: str | None = None,
    timeout: float = 300.0,
) -> dict[str, Any]:
    url = f"{normalize_base_url(base_url)}/sync/push"
    body, boundary = _multipart_body("bundle", bundle_path)
    headers = {
        "Content-Type": f"multipart/form-data; boundary={boundary}",
        "Accept": "application/json",
        **_auth_headers(token),
    }
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            parsed = _decode_response(resp.read())
    except urllib.error.HTTPError as exc:
        parsed = _decode_response(exc.read())
        message = parsed.get("detail") if isinstance(parsed, dict) else None
        raise SyncClientError(message or f"Server returned HTTP {exc.code}", status_code=exc.code, response=parsed) from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise SyncClientError(f"Cannot reach sync server: {exc}") from exc
    if not isinstance(parsed, dict):
        raise SyncClientError("Server returned a non-object JSON response", response=parsed)
    return parsed


def changes(
    base_url: str,
    since: str | int | None = None,
    *,
    token: str | None = None,
    timeout: float = 30.0,
) -> dict[str, Any]:
    query = "" if since in (None, "") else f"?since={urllib.parse.quote(str(since))}"
    url = f"{normalize_base_url(base_url)}/sync/changes{query}"
    return _request_get_json(url, token=token, timeout=timeout)


def download_bundle(
    base_url: str,
    since: str | int | None,
    destination_path: str,
    *,
    token: str | None = None,
    timeout: float = 300.0,
) -> dict[str, Any]:
    query = "" if since in (None, "") else f"?since={urllib.parse.quote(str(since))}"
    url = f"{normalize_base_url(base_url)}/sync/bundle{query}"
    headers = {
        "Accept": "application/octet-stream",
        **_auth_headers(token),
    }
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            os.makedirs(os.path.dirname(os.path.abspath(destination_path)), exist_ok=True)
            with open(destination_path, "wb") as f:
                while True:
                    chunk = resp.read(1024 * 1024)
                    if not chunk:
                        break
                    f.write(chunk)
    except urllib.error.HTTPError as exc:
        parsed = _decode_response(exc.read())
        message = parsed.get("detail") if isinstance(parsed, dict) else None
        raise SyncClientError(message or f"Server returned HTTP {exc.code}", status_code=exc.code, response=parsed) from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise SyncClientError(f"Cannot reach sync server: {exc}") from exc
    manifest = read_bundle_manifest(destination_path)
    if manifest.get("bundle_kind") != "pull_bundle":
        raise SyncClientError("Server returned a non-pull bundle", response=manifest)
    return manifest
