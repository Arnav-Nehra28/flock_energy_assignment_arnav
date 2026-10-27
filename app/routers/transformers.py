"""Transformer API routes."""

from __future__ import annotations

from fastapi import APIRouter, Query

from app.models import PaginatedResponse
from app.services import transformer_service

router = APIRouter(prefix="/api/transformers", tags=["Transformers"])


@router.get(
    "",
    response_model=PaginatedResponse,
    summary="List distribution transformers",
    description=(
        "Returns a paginated list of distribution transformers (DTs). "
        "Each DT has a code, name, feeder code, and capacity in kVA."
    ),
)
async def list_transformers(
    page: int = Query(1, ge=1, description="Page number (1-indexed)"),
):
    return await transformer_service.list_transformers(page=page)


@router.get(
    "/all",
    summary="Get all transformers",
    description="Returns the complete list of all distribution transformers (unpaginated).",
)
async def get_all_transformers():
    data = await transformer_service.get_all_transformers()
    return {"data": data, "total": len(data)}

