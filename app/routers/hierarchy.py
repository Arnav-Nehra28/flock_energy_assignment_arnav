"""Hierarchy API routes."""

from __future__ import annotations

from fastapi import APIRouter

from app.models import HierarchyTreeNode
from app.services import meter_service

router = APIRouter(prefix="/api/hierarchy", tags=["Hierarchy"])


@router.get(
    "",
    response_model=list[HierarchyTreeNode],
    summary="Get network hierarchy tree",
    description=(
        "Returns the full network hierarchy reconstructed from meter data: "
        "Zone → Circle → Division → Subdivision → Substation → Feeder → DT. "
        "Each node includes a meter count."
    ),
)
async def get_hierarchy():
    return await meter_service.get_hierarchy_tree()

