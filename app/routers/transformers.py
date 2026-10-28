"""Transformer API routes."""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query

from app.models import PaginatedResponse, TransformerListResponse
from app.portal_client import PortalAuthError, PortalUpstreamError
from app.services import transformer_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/transformers", tags=["Transformers"])


@router.get(
    "",
    response_model=PaginatedResponse,
    summary="List distribution transformers",
    description=(
        "Returns a paginated list of distribution transformers (DTs). "
        "Each DT has a code, name, feeder code, and capacity in kVA."
    ),
    responses={
        502: {"description": "Portal returned an error or is unreachable"},
        503: {"description": "Portal authentication failed"},
    },
)
async def list_transformers(
    page: int = Query(1, ge=1, description="Page number (1-indexed)"),
):
    try:
        return await transformer_service.list_transformers(page=page)
    except PortalAuthError as exc:
        logger.error("Auth failure listing transformers: %s", exc)
        raise HTTPException(status_code=503, detail="Portal authentication failed") from exc
    except PortalUpstreamError as exc:
        logger.error("Upstream error listing transformers: %s", exc)
        raise HTTPException(status_code=502, detail="Portal upstream error") from exc
    except Exception as exc:
        logger.exception("Unexpected error listing transformers")
        raise HTTPException(status_code=500, detail="Internal server error") from exc


@router.get(
    "/all",
    response_model=TransformerListResponse,
    summary="Get all transformers",
    description="Returns the complete list of all distribution transformers (unpaginated).",
    responses={
        502: {"description": "Portal returned an error or is unreachable"},
        503: {"description": "Portal authentication failed"},
    },
)
async def get_all_transformers():
    try:
        data = await transformer_service.get_all_transformers()
        return {"data": data, "total": len(data)}
    except PortalAuthError as exc:
        logger.error("Auth failure fetching all transformers: %s", exc)
        raise HTTPException(status_code=503, detail="Portal authentication failed") from exc
    except PortalUpstreamError as exc:
        logger.error("Upstream error fetching all transformers: %s", exc)
        raise HTTPException(status_code=502, detail="Portal upstream error") from exc
    except Exception as exc:
        logger.exception("Unexpected error fetching all transformers")
        raise HTTPException(status_code=500, detail="Internal server error") from exc
