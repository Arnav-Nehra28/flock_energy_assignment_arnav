"""Hierarchy API routes."""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException

from app.models import HierarchyTreeNode
from app.portal_client import PortalAuthError, PortalUpstreamError
from app.services import meter_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/hierarchy", tags=["Hierarchy"])


@router.get(
    "",
    response_model=list[HierarchyTreeNode],
    summary="Get network hierarchy tree",
    description=(
        "Returns the full network hierarchy reconstructed from meter data: "
        "Zone -> Circle -> Division -> Subdivision -> Substation -> Feeder -> DT. "
        "Each node includes a meter count."
    ),
    responses={
        502: {"description": "Portal returned an error or is unreachable"},
        503: {"description": "Portal authentication failed"},
    },
)
async def get_hierarchy():
    try:
        return await meter_service.get_hierarchy_tree()
    except PortalAuthError as exc:
        logger.error("Auth failure building hierarchy: %s", exc)
        raise HTTPException(status_code=503, detail="Portal authentication failed") from exc
    except PortalUpstreamError as exc:
        logger.error("Upstream error building hierarchy: %s", exc)
        raise HTTPException(status_code=502, detail="Portal upstream error") from exc
    except Exception as exc:
        logger.exception("Unexpected error building hierarchy tree")
        raise HTTPException(status_code=500, detail="Internal server error") from exc
