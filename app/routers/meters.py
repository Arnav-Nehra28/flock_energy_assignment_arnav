"""Meter API routes."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from app.models import (
    EnergyReading,
    GeoLocation,
    MeterDetail,
    PaginatedResponse,
)
from app.services import meter_service

router = APIRouter(prefix="/api/meters", tags=["Meters"])


@router.get(
    "",
    response_model=PaginatedResponse,
    summary="Search / list meters",
    description=(
        "Search meters by meter ID or serial number. Returns paginated results "
        "(20 per page). Leave `q` empty to list all meters."
    ),
)
async def search_meters(
    q: str = Query("", description="Search by meter ID or serial number"),
    page: int = Query(1, ge=1, description="Page number (1-indexed)"),
):
    return await meter_service.search_meters(query=q, page=page)


@router.get(
    "/export",
    summary="Bulk export all meters",
    description=(
        "Export all meters with full detail (nameplate, hierarchy, location). "
        "Supports optional filtering by status, make, phase, DT code, or zone. "
        "Returns the full filtered dataset in a single response."
    ),
)
async def export_meters(
    status: Optional[str] = Query(None, description="Filter by install status: Installed, Faulty, Decommissioned"),
    make: Optional[str] = Query(None, description="Filter by manufacturer: HPL, L&T, Genus, Allied, Secure"),
    phase: Optional[str] = Query(None, description="Filter by phase type: single, three"),
    dt_code: Optional[str] = Query(None, description="Filter by DT code, e.g. DT-001"),
    zone: Optional[str] = Query(None, description="Filter by zone code (e.g. Z-01) or name"),
):
    data = await meter_service.export_all_meters(
        status=status, make=make, phase=phase, dt_code=dt_code, zone=zone,
    )
    return {"data": data, "total": len(data)}


@router.get(
    "/{meter_id}",
    response_model=MeterDetail,
    summary="Get meter detail",
    description=(
        "Returns full detail for a single meter: nameplate data, "
        "network hierarchy (Zone → Circle → Division → Subdivision → "
        "Substation → Feeder → DT), and geographic location."
    ),
)
async def get_meter(meter_id: str):
    try:
        return await meter_service.get_meter_detail(meter_id)
    except Exception as exc:
        raise HTTPException(status_code=404, detail=f"Meter {meter_id} not found") from exc


@router.get(
    "/{meter_id}/location",
    response_model=GeoLocation,
    summary="Get meter location",
    description="Returns the geographic coordinates (lat/lng) of a meter.",
)
async def get_meter_location(meter_id: str):
    try:
        return await meter_service.get_meter_location(meter_id)
    except Exception as exc:
        raise HTTPException(status_code=404, detail=f"Location not found for meter {meter_id}") from exc


@router.get(
    "/{meter_id}/consumption",
    response_model=list[EnergyReading],
    summary="Get meter consumption readings",
    description=(
        "Returns 30-minute interval energy readings for a meter. "
        "Includes cumulative kWh, kVAh, and R-phase voltage. "
        "Timestamps are normalised to ISO-8601 format."
    ),
)
async def get_meter_consumption(meter_id: str):
    try:
        return await meter_service.get_meter_consumption(meter_id)
    except Exception as exc:
        raise HTTPException(
            status_code=404,
            detail=f"Consumption data not found for meter {meter_id}",
        ) from exc

