"""Local client helpers for pushing sync bundles to the collaboration server."""

from __future__ import annotations

import json
import hashlib
import http.client
import mimetypes
import os
import threading
import time
import uuid
import zipfile
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable


ProgressCallback = Callable[[int, int | None], None]
OperationProgressCallback = Callable[[dict[str, Any]], None]
UPLOAD_CHUNK_MAX_ATTEMPTS = 3


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


def create_user(
    base_url: str,
    username: str,
    password: str,
    role: str,
    *,
    token: str | None = None,
    timeout: float = 30.0,
) -> dict[str, Any]:
    url = f"{normalize_base_url(base_url)}/users"
    return _request_json(
        url,
        {"username": username, "password": password, "role": role},
        token=token,
        timeout=timeout,
    )


def update_user(
    base_url: str,
    user_id: int,
    username: str,
    *,
    token: str | None = None,
    timeout: float = 30.0,
) -> dict[str, Any]:
    url = f"{normalize_base_url(base_url)}/users/{user_id}"
    return _request_json(url, {"username": username}, token=token, timeout=timeout, method="PATCH")


def reset_user_password(
    base_url: str,
    user_id: int,
    *,
    token: str | None = None,
    timeout: float = 30.0,
) -> dict[str, Any]:
    url = f"{normalize_base_url(base_url)}/users/{user_id}/reset-password"
    return _request_json(url, {}, token=token, timeout=timeout)


def delete_user(
    base_url: str,
    user_id: int,
    *,
    token: str | None = None,
    timeout: float = 30.0,
) -> dict[str, Any]:
    url = f"{normalize_base_url(base_url)}/users/{user_id}"
    return _request_json(url, {}, token=token, timeout=timeout, method="DELETE")


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


def operation_progress(
    base_url: str,
    operation_id: str,
    *,
    token: str | None = None,
    timeout: float = 5.0,
) -> dict[str, Any]:
    url = f"{normalize_base_url(base_url)}/sync/operations/{urllib.parse.quote(operation_id)}"
    return _request_get_json(url, token=token, timeout=timeout)


def create_upload_session(
    base_url: str,
    manifest: dict[str, Any],
    *,
    operation_id: str | None,
    token: str | None = None,
    timeout: float = 60.0,
) -> dict[str, Any]:
    url = f"{normalize_base_url(base_url)}/sync/sessions"
    return _request_json(
        url,
        {"manifest": manifest, "operation_id": operation_id},
        token=token,
        timeout=timeout,
    )


def get_upload_session(
    base_url: str,
    session_id: str,
    *,
    token: str | None = None,
    timeout: float = 30.0,
) -> dict[str, Any]:
    url = f"{normalize_base_url(base_url)}/sync/sessions/{urllib.parse.quote(session_id)}"
    return _request_get_json(url, token=token, timeout=timeout)


def upload_session_chunk(
    base_url: str,
    session_id: str,
    object_kind: str,
    object_sha256: str,
    chunk_index: int,
    offset_bytes: int,
    payload: bytes,
    *,
    token: str | None = None,
    timeout: float = 120.0,
) -> dict[str, Any]:
    chunk_sha = hashlib.sha256(payload).hexdigest()
    url = (
        f"{normalize_base_url(base_url)}/sync/sessions/{urllib.parse.quote(session_id)}"
        f"/objects/{urllib.parse.quote(object_kind)}/{object_sha256}/chunks/{int(chunk_index)}"
    )
    headers = {
        "Content-Type": "application/octet-stream",
        "Content-Length": str(len(payload)),
        "X-Chunk-Offset": str(int(offset_bytes)),
        "X-Chunk-SHA256": chunk_sha,
        "Accept": "application/json",
        **_auth_headers(token),
    }
    request = urllib.request.Request(url, data=payload, headers=headers, method="PUT")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            result = _decode_response(response.read())
    except urllib.error.HTTPError as exc:
        parsed = _decode_response(exc.read())
        message = parsed.get("detail") if isinstance(parsed, dict) else None
        raise SyncClientError(
            message or f"Server returned HTTP {exc.code}",
            status_code=exc.code,
            response=parsed,
        ) from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise SyncClientError(f"Cannot reach sync server: {exc}") from exc
    if not isinstance(result, dict):
        raise SyncClientError("Server returned a non-object chunk response", response=result)
    return result


def commit_upload_session(
    base_url: str,
    session_id: str,
    *,
    token: str | None = None,
    timeout: float = 600.0,
    operation_id: str | None = None,
    server_progress_callback: OperationProgressCallback | None = None,
) -> dict[str, Any]:
    url = f"{normalize_base_url(base_url)}/sync/sessions/{urllib.parse.quote(session_id)}/commit"
    stop, thread = _start_operation_poll(
        base_url, operation_id, token, server_progress_callback
    )
    try:
        return _request_json(url, {}, token=token, timeout=timeout)
    finally:
        _stop_operation_poll(stop, thread)


def upload_session_objects(
    base_url: str,
    state: dict[str, Any],
    local_objects: list[dict[str, Any]],
    *,
    token: str | None = None,
    progress_callback: ProgressCallback | None = None,
) -> dict[str, Any]:
    session_id = str(state["session_id"])
    local_by_identity = {
        (str(item["kind"]), str(item["sha256"]).lower()): item
        for item in local_objects
    }
    pending = [item for item in state.get("objects") or [] if item.get("status") != "complete"]
    remaining_total = sum(
        int(item["size_bytes"]) - int(item.get("received_bytes") or 0)
        for item in pending
    )
    uploaded = 0
    if progress_callback:
        progress_callback(0, remaining_total)
    latest = state
    for remote in pending:
        identity = (str(remote["object_kind"]), str(remote["sha256"]).lower())
        local = local_by_identity.get(identity)
        if not local:
            raise SyncClientError(f"Local upload object is missing: {identity[0]}/{identity[1]}")
        chunk_size = int(remote["chunk_size"])
        total_chunks = int(remote["total_chunks"])
        received = {int(item["chunk_index"]) for item in remote.get("received_chunks") or []}
        with open(str(local["path"]), "rb") as source:
            for chunk_index in range(total_chunks):
                offset = chunk_index * chunk_size
                expected_size = min(chunk_size, int(remote["size_bytes"]) - offset)
                if chunk_index in received:
                    continue
                source.seek(offset)
                payload = source.read(expected_size)
                if len(payload) != expected_size:
                    raise SyncClientError(f"Local upload object was truncated: {identity[1]}")
                for attempt in range(UPLOAD_CHUNK_MAX_ATTEMPTS):
                    try:
                        latest = upload_session_chunk(
                            base_url,
                            session_id,
                            identity[0],
                            identity[1],
                            chunk_index,
                            offset,
                            payload,
                            token=token,
                        )
                        break
                    except SyncClientError as exc:
                        retryable = exc.status_code is None or exc.status_code in {408, 429} or (
                            exc.status_code is not None and exc.status_code >= 500
                        )
                        if not retryable or attempt + 1 >= UPLOAD_CHUNK_MAX_ATTEMPTS:
                            raise
                        time.sleep(0.25 * (2**attempt))
                uploaded += len(payload)
                if progress_callback:
                    progress_callback(uploaded, remaining_total)
    return latest


def _start_operation_poll(
    base_url: str,
    operation_id: str | None,
    token: str | None,
    callback: OperationProgressCallback | None,
) -> tuple[threading.Event | None, threading.Thread | None]:
    if not operation_id or callback is None:
        return None, None
    stop = threading.Event()

    def poll() -> None:
        while not stop.is_set():
            try:
                item = operation_progress(base_url, operation_id, token=token)
                callback(item)
                if item.get("status") in {"completed", "failed"}:
                    return
            except SyncClientError:
                pass
            stop.wait(0.25)

    thread = threading.Thread(target=poll, name=f"sync-progress-{operation_id[:8]}", daemon=True)
    thread.start()
    return stop, thread


def _stop_operation_poll(
    stop: threading.Event | None,
    thread: threading.Thread | None,
) -> None:
    if stop is not None:
        stop.set()
    if thread is not None:
        thread.join(timeout=1.0)


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
    progress_callback: ProgressCallback | None = None,
    operation_id: str | None = None,
    server_progress_callback: OperationProgressCallback | None = None,
) -> dict[str, Any]:
    url = f"{normalize_base_url(base_url)}/sync/push"
    boundary = f"----FlightAnalyzerSync{uuid.uuid4().hex}"
    filename = os.path.basename(bundle_path) or "bundle.fapkg"
    content_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    prefix = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="bundle"; filename="{filename}"\r\n'
        f"Content-Type: {content_type}\r\n\r\n"
    ).encode("utf-8")
    suffix = f"\r\n--{boundary}--\r\n".encode("ascii")
    file_size = os.path.getsize(bundle_path)
    total_size = len(prefix) + file_size + len(suffix)

    parsed_url = urllib.parse.urlparse(url)
    if parsed_url.scheme not in {"http", "https"}:
        raise SyncClientError(f"Unsupported sync server scheme: {parsed_url.scheme}")
    path = parsed_url.path or "/"
    if parsed_url.query:
        path = f"{path}?{parsed_url.query}"
    headers = {
        "Content-Type": f"multipart/form-data; boundary={boundary}",
        "Accept": "application/json",
        "Content-Length": str(total_size),
        **({"X-Sync-Operation-Id": operation_id} if operation_id else {}),
        **_auth_headers(token),
    }
    conn_class = http.client.HTTPSConnection if parsed_url.scheme == "https" else http.client.HTTPConnection
    conn = conn_class(parsed_url.netloc, timeout=timeout)
    sent = 0
    request_started = time.perf_counter()
    upload_finished = request_started
    response_finished = request_started
    poll_stop = None
    poll_thread = None
    try:
        if progress_callback:
            progress_callback(0, total_size)
        conn.putrequest("POST", path)
        for key, value in headers.items():
            conn.putheader(key, value)
        conn.endheaders()

        conn.send(prefix)
        sent += len(prefix)
        if progress_callback:
            progress_callback(sent, total_size)
        with open(bundle_path, "rb") as f:
            while True:
                chunk = f.read(1024 * 1024)
                if not chunk:
                    break
                conn.send(chunk)
                sent += len(chunk)
                if progress_callback:
                    progress_callback(sent, total_size)
        conn.send(suffix)
        sent += len(suffix)
        if progress_callback:
            progress_callback(total_size, total_size)
        upload_finished = time.perf_counter()

        poll_stop, poll_thread = _start_operation_poll(
            base_url, operation_id, token, server_progress_callback
        )
        resp = conn.getresponse()
        raw = resp.read()
        response_finished = time.perf_counter()
        parsed = _decode_response(raw)
        if resp.status >= 400:
            message = parsed.get("detail") if isinstance(parsed, dict) else None
            raise SyncClientError(
                message or f"Server returned HTTP {resp.status}",
                status_code=resp.status,
                response=parsed,
            )
    except (TimeoutError, OSError, http.client.HTTPException) as exc:
        raise SyncClientError(f"Cannot reach sync server: {exc}") from exc
    finally:
        _stop_operation_poll(poll_stop, poll_thread)
        conn.close()
    if not isinstance(parsed, dict):
        raise SyncClientError("Server returned a non-object JSON response", response=parsed)
    upload_duration = max(upload_finished - request_started, 0.000001)
    parsed["client_transport_metrics"] = {
        "uploaded_bytes": total_size,
        "upload_duration_seconds": round(upload_duration, 6),
        "upload_bytes_per_second": round(total_size / upload_duration, 2),
        "server_wait_seconds": round(max(0.0, response_finished - upload_finished), 6),
    }
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


def pull_preview(
    base_url: str,
    since: str | int | None = None,
    *,
    token: str | None = None,
    exclude_source_node_id: str | None = None,
    timeout: float = 30.0,
) -> dict[str, Any]:
    params = {}
    if since not in (None, ""):
        params["since"] = str(since)
    if exclude_source_node_id:
        params["exclude_source_node_id"] = exclude_source_node_id
    query = f"?{urllib.parse.urlencode(params)}" if params else ""
    url = f"{normalize_base_url(base_url)}/sync/preview{query}"
    return _request_get_json(url, token=token, timeout=timeout)


def download_bundle(
    base_url: str,
    since: str | int | None,
    destination_path: str,
    *,
    token: str | None = None,
    exclude_source_node_id: str | None = None,
    timeout: float = 300.0,
    progress_callback: ProgressCallback | None = None,
    operation_id: str | None = None,
    server_progress_callback: OperationProgressCallback | None = None,
) -> dict[str, Any]:
    params = {}
    if since not in (None, ""):
        params["since"] = str(since)
    if exclude_source_node_id:
        params["exclude_source_node_id"] = exclude_source_node_id
    query = f"?{urllib.parse.urlencode(params)}" if params else ""
    url = f"{normalize_base_url(base_url)}/sync/bundle{query}"
    headers = {
        "Accept": "application/octet-stream",
        **({"X-Sync-Operation-Id": operation_id} if operation_id else {}),
        **_auth_headers(token),
    }
    req = urllib.request.Request(url, headers=headers, method="GET")
    download_started = time.perf_counter()
    received = 0
    poll_stop, poll_thread = _start_operation_poll(
        base_url, operation_id, token, server_progress_callback
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw_total = resp.headers.get("Content-Length")
            total = int(raw_total) if raw_total and raw_total.isdigit() else None
            if progress_callback:
                progress_callback(0, total)
            os.makedirs(os.path.dirname(os.path.abspath(destination_path)), exist_ok=True)
            with open(destination_path, "wb") as f:
                while True:
                    chunk = resp.read(1024 * 1024)
                    if not chunk:
                        break
                    f.write(chunk)
                    received += len(chunk)
                    if progress_callback:
                        progress_callback(received, total)
    except urllib.error.HTTPError as exc:
        parsed = _decode_response(exc.read())
        message = parsed.get("detail") if isinstance(parsed, dict) else None
        raise SyncClientError(message or f"Server returned HTTP {exc.code}", status_code=exc.code, response=parsed) from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise SyncClientError(f"Cannot reach sync server: {exc}") from exc
    finally:
        _stop_operation_poll(poll_stop, poll_thread)
    manifest = read_bundle_manifest(destination_path)
    if manifest.get("bundle_kind") != "pull_bundle":
        raise SyncClientError("Server returned a non-pull bundle", response=manifest)
    download_duration = max(time.perf_counter() - download_started, 0.000001)
    manifest["_transfer_metrics"] = {
        "downloaded_bytes": received,
        "download_duration_seconds": round(download_duration, 6),
        "download_bytes_per_second": round(received / download_duration, 2),
    }
    return manifest
