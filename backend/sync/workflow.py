"""Local synchronization orchestration independent of the HTTP layer."""

from __future__ import annotations

import os
from datetime import datetime

from backend import runtime_context
from backend.database import DATA_DIR, get_db
from backend.repositories import flights as flight_repository
from backend.sync import client, repository
from backend.sync.local_import import (
    apply_pull_manifest_metadata,
    import_pull_bundle,
    preview_pull_manifest,
)
from backend.sync.package import export_package
from backend.sync.progress import byte_callback, fail, percent, update


class WorkflowError(Exception):
    def __init__(self, status_code: int, detail):
        super().__init__(str(detail))
        self.status_code = status_code
        self.detail = detail


SERVER_BACKED_DELETE_STATES = {"synced", "server_cache", "dirty"}


def _delete_scope(row, requested_scope: str | None) -> str:
    scope = (requested_scope or "auto").strip()
    if scope not in {"auto", "local_cache", "local_unsynced", "server"}:
        raise WorkflowError(400, "Unsupported delete scope")
    if scope != "auto":
        return scope
    sync_state = row["sync_state"] if "sync_state" in row.keys() else None
    server_id = row["server_id"] if "server_id" in row.keys() else None
    if server_id is not None and sync_state in SERVER_BACKED_DELETE_STATES:
        return "server"
    if sync_state == "server_deleted" or server_id is not None:
        return "local_cache"
    return "local_unsynced"


def _mark_local_server_deleted(conn, entity_type: str, local_id: int, result: dict) -> None:
    deleted_at = result.get("deleted_at") or datetime.now().isoformat(timespec="seconds")
    version = int(result.get("version") or 1)
    if entity_type == "model":
        conn.execute(
            """UPDATE aircraft_models
               SET sync_state='server_deleted', server_deleted_at=?, server_version=?,
                   last_sync_at=datetime('now','localtime')
               WHERE id=?""",
            (deleted_at, version, local_id),
        )
        conn.execute(
            """UPDATE aircraft
               SET sync_state='server_deleted', server_deleted_at=?,
                   last_sync_at=datetime('now','localtime')
               WHERE model_id=?""",
            (deleted_at, local_id),
        )
        conn.execute(
            """UPDATE flights
               SET sync_state='server_deleted', server_deleted_at=?,
                   last_sync_at=datetime('now','localtime')
               WHERE aircraft_id IN (SELECT id FROM aircraft WHERE model_id=?)""",
            (deleted_at, local_id),
        )
    elif entity_type == "aircraft":
        conn.execute(
            """UPDATE aircraft
               SET sync_state='server_deleted', server_deleted_at=?, server_version=?,
                   last_sync_at=datetime('now','localtime')
               WHERE id=?""",
            (deleted_at, version, local_id),
        )
        conn.execute(
            """UPDATE flights
               SET sync_state='server_deleted', server_deleted_at=?,
                   last_sync_at=datetime('now','localtime')
               WHERE aircraft_id=?""",
            (deleted_at, local_id),
        )
    else:
        conn.execute(
            """UPDATE flights
               SET sync_state='server_deleted', server_deleted_at=?, server_version=?,
                   last_sync_at=datetime('now','localtime')
               WHERE id=?""",
            (deleted_at, version, local_id),
        )


def _delete_local_model(conn, model_id: int) -> None:
    tables = conn.execute(
        "SELECT table_name FROM data_table_registry WHERE model_id=?", (model_id,)
    ).fetchall()
    conn.execute("DELETE FROM aircraft_models WHERE id=?", (model_id,))
    for table in tables:
        try:
            conn.execute(f"DROP TABLE IF EXISTS {table['table_name']}")
        except Exception:
            pass


def _empty_preview(ok: bool) -> dict:
    return {
        "ok": ok,
        "status": "empty",
        "selected_flights": [],
        "selected_models": [],
        "selected_aircraft": [],
        "skipped_dirty": [],
        "preflight": None,
        "items": [],
        "models": [],
        "aircraft": [],
        "summary": {"total": 0, "create": 0, "existing": 0, "conflict": 0},
    }


def _preview_upload(conn, flight_ids: list[int] | None, token: str | None) -> dict:
    model_ids: list[int] = []
    aircraft_ids: list[int] = []
    selected_models: list[dict] = []
    selected_aircraft: list[dict] = []
    if flight_ids is None:
        flight_ids = [
            int(item["id"])
            for item in repository.list_upload_queue(conn, repository.UPLOAD_QUEUE_STATES)
        ]
        base_queue = repository.list_upload_base_queue(conn, repository.UPLOAD_QUEUE_STATES)
        selected_models = base_queue["models"]
        selected_aircraft = base_queue["aircraft"]
        model_ids = [int(item["id"]) for item in selected_models]
        aircraft_ids = [int(item["id"]) for item in selected_aircraft]
    if not flight_ids and not model_ids and not aircraft_ids:
        return _empty_preview(True)

    selected = repository.validate_uploadable_flights(conn, flight_ids) if flight_ids else []
    selected_ids = [int(item["id"]) for item in selected]
    if not selected_ids and not model_ids and not aircraft_ids:
        return _empty_preview(False)

    server_base_url = client.normalize_base_url(runtime_context.get_server_base_url(conn))
    bundle = export_package(
        conn,
        selected_ids,
        model_ids=model_ids,
        aircraft_ids=aircraft_ids,
        bundle_kind="push_batch",
    )
    manifest = client.read_bundle_manifest(bundle["path"])
    preflight = client.preflight(server_base_url, manifest, token=token)
    model_plan_by_source = {
        int(item["source_id"]): item
        for item in (preflight.get("models") or [])
        if item.get("source_id") is not None
    }
    aircraft_plan_by_source = {
        int(item["source_id"]): item
        for item in (preflight.get("aircraft") or [])
        if item.get("source_id") is not None
    }
    plan_by_source = {
        int(item["source_id"]): item
        for item in (preflight.get("flights") or [])
        if item.get("source_id") is not None
    }
    model_items = []
    for item in selected_models:
        plan = model_plan_by_source.get(int(item["id"]), {})
        model_items.append(
            {
                **item,
                "action": plan.get("action") or "unknown",
                "reason": plan.get("reason"),
                "matched_by": plan.get("matched_by"),
                "server_id": plan.get("server_id"),
                "server_name": plan.get("server_name"),
                "server_version": plan.get("server_version"),
            }
        )
    aircraft_items = []
    for item in selected_aircraft:
        plan = aircraft_plan_by_source.get(int(item["id"]), {})
        aircraft_items.append(
            {
                **item,
                "action": plan.get("action") or "unknown",
                "reason": plan.get("reason"),
                "matched_by": plan.get("matched_by"),
                "server_id": plan.get("server_id"),
                "server_name": plan.get("server_name"),
                "server_version": plan.get("server_version"),
            }
        )
    items = []
    for item in selected:
        plan = plan_by_source.get(int(item["id"]), {})
        items.append(
            {
                **item,
                "action": plan.get("action") or "unknown",
                "reason": plan.get("reason"),
                "matched_by": plan.get("matched_by"),
                "server_id": plan.get("server_id"),
                "server_version": plan.get("server_version"),
            }
        )
    return {
        "ok": not bool(preflight.get("conflicts")),
        "status": preflight.get("status") or "ready",
        "selected_flights": selected,
        "selected_models": selected_models,
        "selected_aircraft": selected_aircraft,
        "skipped_dirty": [],
        "bundle": bundle,
        "preflight": preflight,
        "items": items,
        "models": model_items,
        "aircraft": aircraft_items,
        "summary": preflight.get("summary", {}).get("flights") or {},
    }


def _preview_pull(conn, since: str | None, token: str | None) -> dict:
    server_base_url = client.normalize_base_url(runtime_context.get_server_base_url(conn))
    if since is None:
        since = repository.get_setting(conn, "last_pull_cursor", "")
    manifest = client.pull_preview(server_base_url, since, token=token)
    return preview_pull_manifest(conn, manifest)


def preview(
    mode: str,
    *,
    flight_ids: list[int] | None,
    since: str | None,
    token: str | None,
) -> dict:
    if mode not in {"run", "push", "pull"}:
        raise WorkflowError(400, "Unsupported sync preview mode")
    conn = get_db()
    try:
        result = {"mode": mode, "upload": None, "pull": None}
        if mode in {"run", "push"}:
            result["upload"] = _preview_upload(conn, flight_ids, token)
        if mode in {"run", "pull"}:
            result["pull"] = _preview_pull(conn, since, token)
        return result
    except client.SyncClientError as exc:
        raise WorkflowError(502, exc.to_error_json("preview")) from exc
    except ValueError as exc:
        raise WorkflowError(400, str(exc)) from exc
    finally:
        conn.close()


def build_push_batch(flight_ids: list[int] | None) -> dict:
    conn = get_db()
    try:
        model_ids: list[int] = []
        aircraft_ids: list[int] = []
        if flight_ids is None:
            flight_ids = [
                int(item["id"])
                for item in repository.list_upload_queue(conn, repository.UPLOAD_QUEUE_STATES)
            ]
            base_queue = repository.list_upload_base_queue(conn, repository.UPLOAD_QUEUE_STATES)
            model_ids = [int(item["id"]) for item in base_queue["models"]]
            aircraft_ids = [int(item["id"]) for item in base_queue["aircraft"]]
        selected = repository.validate_uploadable_flights(conn, flight_ids) if flight_ids else []
        result = export_package(
            conn,
            [int(item["id"]) for item in selected],
            model_ids=model_ids,
            aircraft_ids=aircraft_ids,
            bundle_kind="push_batch",
        )
        return {
            **result,
            "status": "bundle_generated",
            "selected_flights": selected,
            "summary": repository.upload_queue_summary(conn),
        }
    except ValueError as exc:
        raise WorkflowError(400, str(exc)) from exc
    finally:
        conn.close()


def push(
    flight_ids: list[int] | None,
    *,
    token: str | None,
    operation_id: str | None = None,
    progress_start: float = 0,
    progress_end: float = 100,
    progress_finalize: bool = True,
) -> dict:
    conn = get_db()
    run_id = None
    selected_ids: list[int] = []
    selected_model_ids: list[int] = []
    selected_aircraft_ids: list[int] = []
    try:
        update(
            operation_id,
            phase="准备上传",
            message="正在检查本地上传队列",
            percent=percent(progress_start, progress_end, 3),
        )
        if flight_ids is None:
            flight_ids = [
                int(item["id"])
                for item in repository.list_upload_queue(conn, repository.UPLOAD_QUEUE_STATES)
            ]
            base_queue = repository.list_upload_base_queue(conn, repository.UPLOAD_QUEUE_STATES)
            selected_model_ids = [int(item["id"]) for item in base_queue["models"]]
            selected_aircraft_ids = [int(item["id"]) for item in base_queue["aircraft"]]
        selected = repository.validate_uploadable_flights(conn, flight_ids) if flight_ids else []
        selected_ids = [int(item["id"]) for item in selected]
        selected_total = len(selected_ids) + len(selected_model_ids) + len(selected_aircraft_ids)
        if selected_total == 0:
            raise ValueError("没有可上传的同步项目")
        update(
            operation_id,
            phase="准备上传",
            message=f"已选择 {selected_total} 个同步项目，正在生成同步包",
            percent=percent(progress_start, progress_end, 10),
            current=0,
            total=selected_total,
        )
        server_base_url = client.normalize_base_url(runtime_context.get_server_base_url(conn))
        run_id = repository.create_sync_run(conn, "push")
        bundle = export_package(
            conn,
            selected_ids,
            model_ids=selected_model_ids,
            aircraft_ids=selected_aircraft_ids,
            bundle_kind="push_batch",
        )
        conn.commit()
        update(
            operation_id,
            phase="服务器预检",
            message="同步包已生成，正在提交服务器预检",
            percent=percent(progress_start, progress_end, 25),
            current=0,
            total=selected_total,
        )

        manifest = client.read_bundle_manifest(bundle["path"])
        preflight = client.preflight(server_base_url, manifest, token=token)
        if preflight.get("status") == "conflict" or preflight.get("conflicts"):
            repository.mark_conflict(conn, selected_ids, preflight)
            repository.mark_base_conflict(conn, preflight)
            summary = {
                "status": "conflict",
                "selected_flight_ids": selected_ids,
                "bundle": bundle,
                "preflight": preflight,
            }
            repository.finish_sync_run(
                conn,
                run_id,
                "failed",
                summary=summary,
                error={"phase": "preflight", "report": preflight},
            )
            conn.commit()
            update(
                operation_id,
                phase="服务器预检",
                message="服务器预检发现冲突，需要人工处理",
                percent=percent(progress_start, progress_end, 100),
                status="failed" if progress_finalize else "running",
                current=0,
                total=selected_total,
            )
            return {
                "ok": False,
                "status": "conflict",
                "run_id": run_id,
                "selected_flights": selected,
                "skipped_dirty": [],
                "bundle": bundle,
                "preflight": preflight,
                "summary": repository.upload_queue_summary(conn),
            }

        update(
            operation_id,
            phase="上传同步包",
            message="服务器预检通过，开始上传同步包",
            percent=percent(progress_start, progress_end, 35),
            current=0,
            total=selected_total,
        )
        server_report = client.push_bundle(
            server_base_url,
            bundle["path"],
            token=token,
            progress_callback=byte_callback(
                operation_id,
                percent(progress_start, progress_end, 35),
                percent(progress_start, progress_end, 72),
                "上传同步包",
                "已上传",
            ),
        )
        update(
            operation_id,
            phase="服务器导入",
            message="同步包已上传，等待服务器导入并返回结果",
            percent=percent(progress_start, progress_end, 78),
            current=len(selected_ids),
            total=selected_total,
        )
        if not server_report.get("ok"):
            error = {"phase": "push", "report": server_report}
            if server_report.get("status") == "conflict" or server_report.get("conflicts"):
                repository.mark_conflict(conn, selected_ids, server_report)
                repository.mark_base_conflict(conn, server_report)
            else:
                repository.mark_upload_failed(conn, selected_ids, error)
                repository.mark_base_upload_failed(
                    conn, selected_model_ids, selected_aircraft_ids, error
                )
            repository.finish_sync_run(
                conn,
                run_id,
                "failed",
                summary={
                    "selected_flight_ids": selected_ids,
                    "bundle": bundle,
                    "preflight": preflight,
                },
                error=error,
            )
            conn.commit()
            update(
                operation_id,
                phase="服务器导入",
                message="服务器未能完成导入，已标记本地上传失败",
                percent=percent(progress_start, progress_end, 100),
                status="failed" if progress_finalize else "running",
                current=selected_total,
                total=selected_total,
            )
            return {
                "ok": False,
                "status": server_report.get("status") or "failed",
                "run_id": run_id,
                "selected_flights": selected,
                "skipped_dirty": [],
                "bundle": bundle,
                "preflight": preflight,
                "server_report": server_report,
                "summary": repository.upload_queue_summary(conn),
            }

        update(
            operation_id,
            phase="写回本地",
            message="服务器已接收数据，正在写回本地同步标记",
            percent=percent(progress_start, progress_end, 88),
            current=len(selected_ids),
            total=selected_total,
        )
        writeback = repository.apply_push_report(conn, server_report, selected_ids)
        status = "success" if not writeback["missing_flight_ids"] else "partial"
        run_status = "success" if status == "success" else "failed"
        response_summary = {
            "selected_flight_ids": selected_ids,
            "bundle": bundle,
            "preflight_summary": preflight.get("summary"),
            "server_imported": server_report.get("imported"),
            "server_existing": server_report.get("existing"),
            "writeback": writeback,
        }
        repository.finish_sync_run(conn, run_id, run_status, summary=response_summary)
        conn.commit()
        update(
            operation_id,
            phase="上传完成",
            message=(
                f"上传完成：{selected_total - len(writeback['missing_flight_ids'])}/"
                f"{selected_total} 个同步项目已同步"
            ),
            percent=percent(progress_start, progress_end, 100),
            status="completed" if progress_finalize else "running",
            current=selected_total,
            total=selected_total,
        )
        return {
            "ok": status == "success",
            "status": status,
            "run_id": run_id,
            "selected_flights": selected,
            "skipped_dirty": [],
            "bundle": bundle,
            "preflight": preflight,
            "server_report": server_report,
            "writeback": writeback,
            "summary": repository.upload_queue_summary(conn),
        }
    except client.SyncClientError as exc:
        error = exc.to_error_json("push")
        if selected_ids:
            repository.mark_upload_failed(conn, selected_ids, error)
        if selected_model_ids or selected_aircraft_ids:
            repository.mark_base_upload_failed(
                conn, selected_model_ids, selected_aircraft_ids, error
            )
        if run_id is not None:
            repository.finish_sync_run(conn, run_id, "failed", error=error)
        conn.commit()
        fail(operation_id, phase="上传失败", message=str(exc))
        raise WorkflowError(502, error) from exc
    except ValueError as exc:
        if run_id is not None:
            repository.finish_sync_run(
                conn,
                run_id,
                "failed",
                error={"phase": "local", "message": str(exc)},
            )
            conn.commit()
        fail(operation_id, phase="上传失败", message=str(exc))
        raise WorkflowError(400, str(exc)) from exc
    finally:
        conn.close()


def pull(
    *,
    since: str | None,
    token: str | None,
    operation_id: str | None = None,
    progress_start: float = 0,
    progress_end: float = 100,
    progress_finalize: bool = True,
    package_path: str | None = None,
    conflict_resolutions: dict[str, str] | None = None,
    exclude_source_node_id: str | None = None,
) -> dict:
    conn = get_db()
    run_id = None
    bundle_path = None
    try:
        update(
            operation_id,
            phase="准备拉取",
            message="正在读取本地拉取游标",
            percent=percent(progress_start, progress_end, 5),
        )
        server_base_url = client.normalize_base_url(runtime_context.get_server_base_url(conn))
        if since is None:
            since = repository.get_setting(conn, "last_pull_cursor", "")
        run_id = repository.create_sync_run(conn, "pull")
        conn.commit()

        if not package_path:
            update(
                operation_id,
                phase="检查同步清单",
                message="正在判断是否只需要更新架次信息",
                percent=percent(progress_start, progress_end, 12),
            )
            preview_manifest = client.pull_preview(
                server_base_url,
                since,
                token=token,
                exclude_source_node_id=exclude_source_node_id,
            )
            preview_result = preview_pull_manifest(conn, preview_manifest)
            preview_summary = preview_result.get("summary") or {}
            if int(preview_summary.get("bundle_required") or 0) == 0:
                metadata_total = int(preview_summary.get("metadata_only") or 0)
                update(
                    operation_id,
                    phase="更新本地信息",
                    message=f"无需下载同步包，正在更新 {metadata_total} 个架次信息",
                    percent=percent(progress_start, progress_end, 55),
                    current=0,
                    total=metadata_total,
                )
                report = apply_pull_manifest_metadata(
                    conn,
                    preview_manifest,
                    {"flight_resolutions": conflict_resolutions or {}},
                )
                ok = report.get("status") in {"success", "partial"}
                summary = {
                    "since": since,
                    "server_cursor": preview_manifest.get("server_cursor"),
                    "bundle_path": None,
                    "manifest_counts": {
                        "models": len(preview_manifest.get("models") or []),
                        "aircraft": len(preview_manifest.get("aircraft") or []),
                        "flights": len(preview_manifest.get("flights") or []),
                        "raw_files": len(preview_manifest.get("raw_files") or []),
                    },
                    "report": report,
                }
                repository.finish_sync_run(
                    conn,
                    run_id,
                    "success" if ok else "failed",
                    summary=summary,
                    error=None if ok else {"phase": "metadata", "report": report},
                )
                conn.commit()
                update(
                    operation_id,
                    phase="拉取完成",
                    message=f"本地信息已更新：{report.get('updated', {}).get('flights', 0)} 个架次",
                    percent=percent(progress_start, progress_end, 100),
                    status="completed" if progress_finalize else "running",
                    current=metadata_total,
                    total=metadata_total,
                )
                return {
                    "ok": ok,
                    "status": report.get("status"),
                    "run_id": run_id,
                    "bundle": {
                        "path": None,
                        "package_id": preview_manifest.get("package_id"),
                        "server_cursor": preview_manifest.get("server_cursor"),
                    },
                    "report": report,
                    "summary": repository.upload_queue_summary(conn),
                }

        cache_dir = os.path.join(DATA_DIR, "sync_cache")
        os.makedirs(cache_dir, exist_ok=True)
        if package_path:
            cache_root = os.path.abspath(cache_dir)
            bundle_path = os.path.abspath(package_path)
            if os.path.commonpath([bundle_path, cache_root]) != cache_root:
                raise ValueError("同步预览包路径不在 sync_cache 目录内")
            manifest = client.read_bundle_manifest(bundle_path)
            update(
                operation_id,
                phase="读取预览包",
                message="正在使用预览时下载的同步包",
                percent=percent(progress_start, progress_end, 60),
            )
        else:
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            bundle_path = os.path.join(cache_dir, f"server_pull_{stamp}.fapkg")
            update(
                operation_id,
                phase="下载同步包",
                message="正在从服务器下载同步包",
                percent=percent(progress_start, progress_end, 15),
            )
            manifest = client.download_bundle(
                server_base_url,
                since,
                bundle_path,
                token=token,
                exclude_source_node_id=exclude_source_node_id,
                progress_callback=byte_callback(
                    operation_id,
                    percent(progress_start, progress_end, 15),
                    percent(progress_start, progress_end, 60),
                    "下载同步包",
                    "已下载",
                ),
            )
        manifest_counts = {
            "models": len(manifest.get("models") or []),
            "aircraft": len(manifest.get("aircraft") or []),
            "flights": len(manifest.get("flights") or []),
            "raw_files": len(manifest.get("raw_files") or []),
        }
        update(
            operation_id,
            phase="导入本地",
            message=(
                f"同步包已下载，正在导入 {manifest_counts['flights']} 个架次、"
                f"{manifest_counts['raw_files']} 个原始文件"
            ),
            percent=percent(progress_start, progress_end, 72),
            current=0,
            total=manifest_counts["flights"],
        )
        report = import_pull_bundle(
            conn, bundle_path, {"flight_resolutions": conflict_resolutions or {}}
        )
        ok = report.get("status") in {"success", "partial"}
        summary = {
            "since": since,
            "server_cursor": manifest.get("server_cursor"),
            "bundle_path": bundle_path,
            "manifest_counts": manifest_counts,
            "report": report,
        }
        repository.finish_sync_run(
            conn,
            run_id,
            "success" if ok else "failed",
            summary=summary,
            error=None if ok else {"phase": "import", "report": report},
        )
        conn.commit()
        update(
            operation_id,
            phase="拉取完成",
            message=f"拉取完成：导入状态 {report.get('status')}",
            percent=percent(progress_start, progress_end, 100),
            status="completed" if progress_finalize else "running",
            current=manifest_counts["flights"],
            total=manifest_counts["flights"],
        )
        return {
            "ok": ok,
            "status": report.get("status"),
            "run_id": run_id,
            "bundle": {
                "path": bundle_path,
                "package_id": manifest.get("package_id"),
                "server_cursor": manifest.get("server_cursor"),
            },
            "report": report,
            "summary": repository.upload_queue_summary(conn),
        }
    except client.SyncClientError as exc:
        error = exc.to_error_json("pull")
        if run_id is not None:
            repository.finish_sync_run(conn, run_id, "failed", error=error)
            conn.commit()
        fail(operation_id, phase="拉取失败", message=str(exc))
        raise WorkflowError(502, error) from exc
    except ValueError as exc:
        if run_id is not None:
            repository.finish_sync_run(
                conn,
                run_id,
                "failed",
                error={"phase": "local", "message": str(exc)},
            )
            conn.commit()
        fail(operation_id, phase="拉取失败", message=str(exc))
        raise WorkflowError(400, str(exc)) from exc
    finally:
        conn.close()


def run(
    flight_ids: list[int] | None,
    *,
    since: str | None,
    token: str | None,
    operation_id: str | None,
    pull_conflict_resolutions: dict[str, str] | None,
) -> dict:
    steps = []
    push_result = None
    update(
        operation_id,
        phase="准备同步",
        message="正在检查本地上传队列",
        percent=2,
    )

    conn = get_db()
    try:
        local_node_id = runtime_context.get_local_node_id(conn)
        conn.commit()
        base_count = 0
        if flight_ids is None:
            queue_rows = repository.list_upload_queue(conn, repository.UPLOAD_QUEUE_STATES)
            push_ids = [
                int(item["id"])
                for item in queue_rows
                if item.get("sync_state")
                in {"local_only", "pending_upload", "dirty", "upload_failed"}
            ]
            base_queue = repository.list_upload_base_queue(conn, repository.UPLOAD_QUEUE_STATES)
            base_count = len(base_queue["models"]) + len(base_queue["aircraft"])
            dirty_count = 0
        else:
            clean_ids = sorted({int(flight_id) for flight_id in flight_ids})
            if clean_ids:
                placeholders = ",".join("?" for _ in clean_ids)
                rows = conn.execute(
                    f"SELECT id, sync_state FROM flights WHERE id IN ({placeholders})",
                    clean_ids,
                ).fetchall()
                push_ids = [
                    int(row["id"])
                    for row in rows
                    if row["sync_state"]
                    in {"local_only", "pending_upload", "dirty", "upload_failed"}
                ]
                dirty_count = 0
            else:
                push_ids = []
                dirty_count = 0
    finally:
        conn.close()

    if push_ids or base_count:
        update(
            operation_id,
            phase="上传阶段",
            message=f"发现 {len(push_ids) + base_count} 个待上传同步项目，开始上传",
            percent=6,
            current=0,
            total=len(push_ids) + base_count,
        )
        push_result = push(
            None if flight_ids is None else push_ids,
            token=token,
            operation_id=operation_id,
            progress_start=6,
            progress_end=58,
            progress_finalize=False,
        )
        steps.append({"name": "push", "status": push_result.get("status", "unknown")})
        if not push_result.get("ok"):
            update(
                operation_id,
                phase="同步失败",
                message=f"上传阶段未完成：{push_result.get('status') or 'failed'}",
                percent=100,
                status="failed",
            )
            return {
                "ok": False,
                "status": push_result.get("status") or "push_failed",
                "steps": steps,
                "push": push_result,
                "pull": None,
                "summary": push_result.get("summary"),
            }
    else:
        detail = "无可上传项"
        if dirty_count:
            detail = f"跳过 {dirty_count} 个 dirty 项，当前阶段需人工处理后上传"
        steps.append({"name": "push", "status": "skipped", "detail": detail})
        update(operation_id, phase="上传阶段", message=detail, percent=20)

    update(
        operation_id,
        phase="拉取阶段",
        message="开始从服务器拉取最新数据",
        percent=60,
    )
    pull_result = pull(
        since=since,
        token=token,
        operation_id=operation_id,
        progress_start=60,
        progress_end=98,
        progress_finalize=False,
        conflict_resolutions=pull_conflict_resolutions,
        exclude_source_node_id=(
            local_node_id if push_result and push_result.get("ok") else None
        ),
    )
    steps.append({"name": "pull", "status": pull_result.get("status", "unknown")})
    ok = bool(pull_result.get("ok"))
    update(
        operation_id,
        phase="同步完成" if ok else "同步失败",
        message="同步一次已完成" if ok else f"拉取阶段未完成：{pull_result.get('status') or 'failed'}",
        percent=100,
        status="completed" if ok else "failed",
    )
    return {
        "ok": ok,
        "status": "success" if ok else (pull_result.get("status") or "pull_failed"),
        "steps": steps,
        "push": push_result,
        "pull": pull_result,
        "summary": pull_result.get("summary"),
    }


def abandon(flight_ids: list[int]) -> dict:
    conn = get_db()
    try:
        changed = repository.abandon_uploads(conn, flight_ids)
        summary = repository.upload_queue_summary(conn)
        conn.commit()
        return {"ok": True, "status": "abandoned", "abandoned": changed, "summary": summary}
    except ValueError as exc:
        raise WorkflowError(400, str(exc)) from exc
    finally:
        conn.close()


def delete_entity(
    entity_type: str,
    local_id: int,
    *,
    requested_scope: str | None,
    reason: str | None,
    token: str | None,
) -> dict:
    table = {
        "model": "aircraft_models",
        "aircraft": "aircraft",
        "flight": "flights",
    }[entity_type]
    label = {"model": "Model", "aircraft": "Aircraft", "flight": "Flight"}[entity_type]
    conn = get_db()
    try:
        row = conn.execute(f"SELECT * FROM {table} WHERE id=?", (local_id,)).fetchone()
        if not row:
            raise WorkflowError(404, f"{label} not found")
        scope = _delete_scope(row, requested_scope)
        if scope == "server":
            server_id = row["server_id"]
            if server_id is None:
                raise WorkflowError(400, f"{label} has not been synced to the server")
            server_base_url = client.normalize_base_url(
                runtime_context.get_server_base_url(conn)
            )
            try:
                result = client.delete_entity(
                    server_base_url,
                    entity_type,
                    int(server_id),
                    reason=reason,
                    token=token,
                )
            except client.SyncClientError as exc:
                raise WorkflowError(
                    exc.status_code or 502,
                    exc.to_error_json(f"delete_{entity_type}"),
                ) from exc
            _mark_local_server_deleted(conn, entity_type, local_id, result)
            conn.commit()
            return {"ok": True, "scope": "server", "server": result}
        if scope == "local_unsynced" and row["server_id"] is not None:
            raise WorkflowError(
                400, f"Server-backed {entity_type} cannot be deleted as local_unsynced"
            )
        if entity_type == "model":
            _delete_local_model(conn, local_id)
        elif entity_type == "aircraft":
            conn.execute("DELETE FROM aircraft WHERE id=?", (local_id,))
        else:
            flight_repository.delete_flight(conn, local_id)
        conn.commit()
        return {"ok": True, "scope": scope}
    finally:
        conn.close()
