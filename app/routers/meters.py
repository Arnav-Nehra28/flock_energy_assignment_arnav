"""Meter API routes."""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from app.models import (
    EnergyReading,
    ExportResponse,
    GeoLocation,
    MeterDetail,
    PaginatedResponse,
)
from app.portal_client import PortalAuthError, PortalNotFoundError, PortalUpstreamError
from app.services import meter_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/meters", tags=["Meters"])


@router.get(
    "",
    response_model=PaginatedResponse,
    summary="Search / list meters",
    description=(
        "Search meters by meter ID or serial number. Returns paginated results "
        "(20 per page). Leave `q` empty to list all meters."
    ),
    responses={
        502: {"description": "Portal returned an error or is unreachable"},
        503: {"description": "Portal authentication failed"},
    },
)
async def search_meters(
    q: str = Query("", description="Search by meter ID or serial number"),
    page: int = Query(1, ge=1, description="Page number (1-indexed)"),
):
    try:
        return await meter_service.search_meters(query=q, page=page)
    except PortalAuthError as exc:
        logger.error("Auth failure on search meters: %s", exc)
        raise HTTPException(status_code=503, detail="Portal authentication failed") from exc
    except PortalUpstreamError as exc:
        logger.error("Upstream error on search meters: %s", exc)
        raise HTTPException(status_code=502, detail="Portal upstream error") from exc
    except Exception as exc:
        logger.exception("Unexpected error searching meters")
        raise HTTPException(status_code=500, detail="Internal server error") from exc


@router.get(
    "/export",
    response_model=ExportResponse,
    summary="Bulk export all meters",
    description=(
        "Export all meters with full detail (nameplate, hierarchy, location). "
        "Supports optional filtering by status, make, phase, DT code, or zone. "
        "Returns the full filtered dataset in a single response."
    ),
    responses={
        502: {"description": "Portal returned an error or is unreachable"},
        503: {"description": "Portal authentication failed"},
    },
)
async def export_meters(
    status: Optional[str] = Query(None, description="Filter by install status: Installed, Faulty, Decommissioned"),
    make: Optional[str] = Query(None, description="Filter by manufacturer: HPL, L&T, Genus, Allied, Secure"),
    phase: Optional[str] = Query(None, description="Filter by phase type: single, three"),
    dt_code: Optional[str] = Query(None, description="Filter by DT code, e.g. DT-001"),
    zone: Optional[str] = Query(None, description="Filter by zone code (e.g. Z-01) or name"),
):
    try:
        data = await meter_service.export_all_meters(
            status=status, make=make, phase=phase, dt_code=dt_code, zone=zone,
        )
        return {"data": data, "total": len(data)}
    except PortalAuthError as exc:
        logger.error("Auth failure on export: %s", exc)
        raise HTTPException(status_code=503, detail="Portal authentication failed") from exc
    except PortalUpstreamError as exc:
        logger.error("Upstream error on export: %s", exc)
        raise HTTPException(status_code=502, detail="Portal upstream error") from exc
    except Exception as exc:
        logger.exception("Unexpected error exporting meters")
        raise HTTPException(status_code=500, detail="Internal server error") from exc


@router.get(
    "/{meter_id}",
    response_model=MeterDetail,
    summary="Get meter detail",
    description=(
        "Returns full detail for a single meter: nameplate data, "
        "network hierarchy (Zone -> Circle -> Division -> Subdivision -> "
        "Substation -> Feeder -> DT), and geographic location."
    ),
    responses={
        404: {"description": "Meter not found"},
        502: {"description": "Portal returned an error or is unreachable"},
        503: {"description": "Portal authentication failed"},
    },
)
async def get_meter(meter_id: str):
    try:
        return await meter_service.get_meter_detail(meter_id)
    except PortalNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"Meter {meter_id} not found") from exc
    except PortalAuthError as exc:
        logger.error("Auth failure on meter detail %s: %s", meter_id, exc)
        raise HTTPException(status_code=503, detail="Portal authentication failed") from exc
    except PortalUpstreamError as exc:
        logger.error("Upstream error on meter detail %s: %s", meter_id, exc)
        raise HTTPException(status_code=502, detail="Portal upstream error") from exc
    except Exception as exc:
        logger.exception("Unexpected error retrieving meter %s", meter_id)
        raise HTTPException(status_code=500, detail="Internal server error") from exc


@router.get(
    "/{meter_id}/location",
    response_model=GeoLocation,
    summary="Get meter location",
    description="Returns the geographic coordinates (lat/lng) of a meter.",
    responses={
        404: {"description": "Meter or location not found"},
        502: {"description": "Portal returned an error or is unreachable"},
        503: {"description": "Portal authentication failed"},
    },
)
async def get_meter_location(meter_id: str):
    try:
        return await meter_service.get_meter_location(meter_id)
    except PortalNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"Location not found for meter {meter_id}") from exc
    except PortalAuthError as exc:
        logger.error("Auth failure on meter location %s: %s", meter_id, exc)
        raise HTTPException(status_code=503, detail="Portal authentication failed") from exc
    except PortalUpstreamError as exc:
        logger.error("Upstream error on meter location %s: %s", meter_id, exc)
        raise HTTPException(status_code=502, detail="Portal upstream error") from exc
    except Exception as exc:
        logger.exception("Unexpected error retrieving location for %s", meter_id)
        raise HTTPException(status_code=500, detail="Internal server error") from exc


@router.get(
    "/{meter_id}/consumption",
    response_model=list[EnergyReading],
    summary="Get meter consumption readings",
    description=(
        "Returns 30-minute interval energy readings for a meter. "
        "Includes cumulative kWh, kVAh, and R-phase voltage. "
        "Timestamps are normalised to ISO-8601 format with IST (+05:30) offset."
    ),
    responses={
        404: {"description": "Meter not found or no consumption data"},
        502: {"description": "Portal returned an error or is unreachable"},
        503: {"description": "Portal authentication failed"},
    },
)
async def get_meter_consumption(meter_id: str):
    try:
        return await meter_service.get_meter_consumption(meter_id)
    except PortalNotFoundError as exc:
        raise HTTPException(
            status_code=404,
            detail=f"Consumption data not found for meter {meter_id}",
        ) from exc
    except PortalAuthError as exc:
        logger.error("Auth failure on consumption %s: %s", meter_id, exc)
        raise HTTPException(status_code=503, detail="Portal authentication failed") from exc
    except PortalUpstreamError as exc:
        logger.error("Upstream error on consumption %s: %s", meter_id, exc)
        raise HTTPException(status_code=502, detail="Portal upstream error") from exc
    except Exception as exc:
        logger.exception("Unexpected error retrieving consumption for %s", meter_id)
        raise HTTPException(status_code=500, detail="Internal server error") from exc
