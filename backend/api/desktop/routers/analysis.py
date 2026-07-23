"""Flight analysis, registry, and preset routes."""

from fastapi import APIRouter, HTTPException

from backend import analysis
from backend.api.desktop.schemas import (
    AlignedRequest,
    AnomalyRequest,
    CompareRequest,
    CorrelationRequest,
    FlightDataMatchesRequest,
    FilterPresetCreate,
    PresetCreate,
)
from backend.database import get_db
from backend.import_pipeline.format_configs import get_columns_for_model
from backend.repositories import presets as preset_repository


router = APIRouter()


@router.post("/api/flights/data-matches")
def get_data_matching_flights(req: FlightDataMatchesRequest):
    try:
        flight_ids = analysis.match_flights_by_data(
            req.model_id,
            req.flight_ids,
            req.filter,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {
        "flight_ids": flight_ids,
        "evaluated_count": len(set(req.flight_ids)),
        "matched_count": len(flight_ids),
    }


@router.get("/api/flights/{flight_id}/columns")
def get_columns(flight_id: int):
    return {"columns": analysis.get_columns_for_flight_api(flight_id)}


@router.post("/api/flights/{flight_id}/aligned")
def get_aligned(flight_id: int, req: AlignedRequest):
    return analysis.get_aligned_data(flight_id, req.column_keys, filter_spec=req.filter)


@router.get("/api/flights/{flight_id}/alerts")
def get_alerts(flight_id: int):
    return analysis.get_alerts(flight_id)


@router.get("/api/flights/{flight_id}/stats")
def get_stats(flight_id: int):
    return analysis.get_flight_stats(flight_id)


@router.post("/api/flights/{flight_id}/correlation")
def correlation(flight_id: int, req: CorrelationRequest):
    return analysis.get_correlation(flight_id, req.column_keys)


@router.post("/api/flights/{flight_id}/anomaly")
def anomaly(flight_id: int, req: AnomalyRequest):
    return analysis.get_anomalies(flight_id, req.column_key, req.window_size, req.sigma)


@router.post("/api/compare")
def compare(req: CompareRequest):
    return {"series": analysis.get_compare(req.flight_ids, req.column_key)}


@router.get("/api/registry/columns")
def registry_columns(model_id: int):
    conn = get_db()
    try:
        return {"columns": get_columns_for_model(conn, model_id)}
    finally:
        conn.close()


@router.get("/api/presets")
def list_presets(model_id: int):
    conn = get_db()
    try:
        return {"presets": preset_repository.list_column_presets(conn, model_id)}
    finally:
        conn.close()


@router.post("/api/presets")
def create_preset(req: PresetCreate):
    conn = get_db()
    try:
        result = preset_repository.save_column_preset(conn, req.model_id, req.name, req.columns)
        conn.commit()
        return result
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc
    finally:
        conn.close()


@router.delete("/api/presets/{preset_id}")
def delete_preset(preset_id: int):
    conn = get_db()
    try:
        preset_repository.delete_column_preset(conn, preset_id)
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()


@router.get("/api/filter-presets")
def list_filter_presets(model_id: int):
    conn = get_db()
    try:
        return {"presets": preset_repository.list_filter_presets(conn, model_id)}
    finally:
        conn.close()


@router.post("/api/filter-presets")
def create_filter_preset(req: FilterPresetCreate):
    conn = get_db()
    try:
        result = preset_repository.save_filter_preset(conn, req.model_id, req.name, req.config)
        conn.commit()
        return result
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc
    finally:
        conn.close()


@router.delete("/api/filter-presets/{preset_id}")
def delete_filter_preset(preset_id: int):
    conn = get_db()
    try:
        preset_repository.delete_filter_preset(conn, preset_id)
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()
