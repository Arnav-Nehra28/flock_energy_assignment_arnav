"""Meter service — business logic between the API layer and the portal client.

Handles data normalisation, caching, filtering, and hierarchy reconstruction.
"""

from __future__ import annotations

import json
import logging
import math
from typing import Any, Optional

from app.config import settings
from app.models import (
    EnergyReading,
    GeoLocation,
    MeterDetail,
    MeterExport,
    MeterHierarchy,
    MeterNameplate,
    MeterSummary,
    HierarchyNode,
    HierarchyTreeNode,
    PaginatedResponse,
)
from app.portal_client import PortalNotFoundError, portal_client
from app.services.cache import cache

logger = logging.getLogger(__name__)

PAGE_SIZE = 20  # Portal's fixed page size


# ── helpers ────────────────────────────────────────────────────────────

def _parse_energy_timestamp(raw: str) -> str:
    """Convert portal's DD/MM/YYYY HH:mm to ISO-8601."""
    try:
        parts = raw.split(" ")
        day, month, year = parts[0].split("/")
        time_part = parts[1] if len(parts) > 1 else "00:00"
        return f"{year}-{month}-{day}T{time_part}:00"
    except (IndexError, ValueError):
        return raw  # Return as-is if parsing fails


def _normalise_nameplate_legacy(data: list[dict]) -> MeterNameplate:
    """Parse legacy format: [{ parameterName, parameterValue }, ...]."""
    params = {item["parameterName"]: item["parameterValue"] for item in data}
    return MeterNameplate(
        meter_id=params.get("Meter ID", ""),
        serial_no=params.get("Serial No", ""),
        make=params.get("Make", ""),
        phase_type=params.get("Phase Type", ""),
        install_status=params.get("Installation Status", ""),
        install_type=params.get("Installation Type", ""),
    )


def _normalise_nameplate_v2(class_data_str: str) -> MeterNameplate:
    """Parse v2 format: JSON string with installed_meter object."""
    try:
        parsed = json.loads(class_data_str)
        meter = parsed.get("installed_meter", {})
        return MeterNameplate(
            meter_id=meter.get("MeterId", ""),
            serial_no=meter.get("SerialNo", ""),
            make=meter.get("Make", ""),
            phase_type=meter.get("PhaseType", ""),
            install_status=meter.get("InstallationStatus", ""),
            install_type=meter.get("InstallationType", ""),
        )
    except (json.JSONDecodeError, KeyError) as exc:
        logger.warning("Failed to parse v2 classData: %s", exc)
        return MeterNameplate(
            meter_id="", serial_no="", make="",
            phase_type="", install_status="", install_type="",
        )


def _decode_sveltekit_data(nodes: list) -> dict:
    """Decode SvelteKit's compact __data.json format into a usable dict.

    SvelteKit uses a positional encoding where objects reference values
    by index in a flat array. This recursively resolves references.
    """
    if len(nodes) < 3:
        raise PortalNotFoundError("Incomplete SvelteKit response")

    detail_node = nodes[2]  # Index 2 is the page-specific data node
    if not detail_node:
        raise PortalNotFoundError("Empty page data node")

    if detail_node.get("type") == "error":
        msg = detail_node.get("error", {}).get("message", "Meter not found")
        raise PortalNotFoundError(msg)

    if detail_node.get("type") != "data":
        raise PortalNotFoundError("Expected data node")

    flat = detail_node.get("data", [])
    if not flat:
        raise PortalNotFoundError("Empty node data")
    root = flat[0]  # The root object with index references

    def resolve(ref: Any) -> Any:
        if isinstance(ref, int):
            val = flat[ref]
            return resolve(val)
        if isinstance(ref, dict):
            return {k: resolve(v) for k, v in ref.items()}
        if isinstance(ref, list):
            return [resolve(item) for item in ref]
        return ref

    return resolve(root)


def _parse_hierarchy_from_sveltekit(hierarchy_dict: dict) -> MeterHierarchy:
    """Parse hierarchy from decoded SvelteKit data.

    Format: {"Zone": "Jaipur Zone 1 (Z-01)", "Circle": "Circle 1 (C-01)", ...}
    """
    def parse_node(value: str) -> HierarchyNode:
        # Format: "Name (CODE)"
        if "(" in value and value.endswith(")"):
            name_part = value[:value.rfind("(")].strip()
            code_part = value[value.rfind("(") + 1 : -1]
            return HierarchyNode(name=name_part, code=code_part)
        return HierarchyNode(name=value, code="")

    level_map = {
        "Zone": "zone", "Circle": "circle", "Division": "division",
        "Subdivision": "subdivision", "Sub Station": "substation",
        "Feeder": "feeder", "DT": "dt",
    }
    nodes = {}
    for portal_key, model_key in level_map.items():
        val = hierarchy_dict.get(portal_key, "")
        nodes[model_key] = parse_node(val) if val else HierarchyNode(name="", code="")
    return MeterHierarchy(**nodes)


# ── public service functions ───────────────────────────────────────────

async def search_meters(
    query: str = "",
    page: int = 1,
) -> PaginatedResponse:
    """Search/list meters with pagination."""
    cache_key = f"meters:search:{query}:{page}"
    cached = cache.get(cache_key)
    if cached:
        return cached

    raw = await portal_client.search_meters(query=query, page=page)
    total = raw.get("total", 0)
    items = [MeterSummary(**m) for m in raw.get("data", [])]

    result = PaginatedResponse(
        data=[m.model_dump() for m in items],
        total=total,
        page=page,
        page_size=PAGE_SIZE,
        total_pages=max(1, math.ceil(total / PAGE_SIZE)),
    )
    cache.set(cache_key, result, settings.cache_ttl_seconds)
    return result


async def get_meter_detail(meter_id: str) -> MeterDetail:
    """Get full meter detail: nameplate + hierarchy + location."""
    cache_key = f"meter:detail:{meter_id}"
    cached = cache.get(cache_key)
    if cached:
        return cached

    # Fetch SvelteKit data and geo in parallel
    import asyncio
    sk_task = portal_client.get_meter_detail_sveltekit(meter_id)
    geo_task = portal_client.get_meter_geo(meter_id)
    sk_raw, geo_raw = await asyncio.gather(sk_task, geo_task, return_exceptions=True)

    # Parse SvelteKit data
    if isinstance(sk_raw, Exception):
        raise sk_raw
    decoded = _decode_sveltekit_data(sk_raw.get("nodes", []))

    # Parse nameplate (handle both legacy and v2 formats)
    detail_obj = decoded.get("detail", {})
    if "classData" in detail_obj:
        nameplate = _normalise_nameplate_v2(detail_obj["classData"])
    elif "data" in detail_obj:
        nameplate = _normalise_nameplate_legacy(detail_obj["data"])
    else:
        nameplate = MeterNameplate(
            meter_id=meter_id, serial_no="", make="",
            phase_type="", install_status="", install_type="",
        )

    # Parse hierarchy
    hierarchy = _parse_hierarchy_from_sveltekit(decoded.get("hierarchy", {}))

    # Parse geo
    location = None
    if not isinstance(geo_raw, Exception) and geo_raw.get("data"):
        geo = geo_raw["data"]
        try:
            location = GeoLocation(
                latitude=float(geo["latitude"]),
                longitude=float(geo["longitude"]),
            )
        except (ValueError, KeyError):
            pass

    result = MeterDetail(
        meter_id=meter_id,
        nameplate=nameplate,
        hierarchy=hierarchy,
        location=location,
    )
    cache.set(cache_key, result, settings.cache_ttl_seconds)
    return result


async def get_meter_consumption(meter_id: str) -> list[EnergyReading]:
    """Get energy/consumption readings for a meter."""
    cache_key = f"meter:energy:{meter_id}"
    cached = cache.get(cache_key)
    if cached:
        return cached

    raw = await portal_client.get_meter_energy(meter_id)
    readings = []
    for r in raw.get("data", []):
        readings.append(EnergyReading(
            timestamp=_parse_energy_timestamp(r["timestamp"]),
            kwh=float(r["kwh"]) if r.get("kwh") else None,
            kvah=float(r["kvah"]) if r.get("kvah") else None,
            volt_r=int(r["voltR"]) if r.get("voltR") else None,
        ))

    cache.set(cache_key, readings, settings.cache_ttl_seconds)
    return readings


async def get_meter_location(meter_id: str) -> GeoLocation:
    """Get geographic coordinates for a meter."""
    cache_key = f"meter:geo:{meter_id}"
    cached = cache.get(cache_key)
    if cached:
        return cached

    raw = await portal_client.get_meter_geo(meter_id)
    geo = raw.get("data", {})
    if not geo or "latitude" not in geo or "longitude" not in geo:
        raise PortalNotFoundError(f"Location not found for meter {meter_id}")
    location = GeoLocation(
        latitude=float(geo["latitude"]),
        longitude=float(geo["longitude"]),
    )
    cache.set(cache_key, location, settings.cache_ttl_seconds)
    return location


async def export_all_meters(
    status: Optional[str] = None,
    make: Optional[str] = None,
    phase: Optional[str] = None,
    dt_code: Optional[str] = None,
    zone: Optional[str] = None,
) -> list[dict]:
    """Bulk export all meters with optional server-side filtering.

    Filters are applied locally after fetching the full dataset from the
    portal (since the portal export has no filtering).
    """
    cache_key = "meters:export:all"
    cached = cache.get(cache_key)
    if cached:
        all_meters = cached
    else:
        raw = await portal_client.export_all_meters()
        all_meters = raw.get("data", [])
        cache.set(cache_key, all_meters, settings.export_cache_ttl_seconds)

    # Normalise geo field
    def normalise(m: dict) -> dict:
        geo = m.get("geo")
        if geo:
            m["location"] = {"latitude": geo.get("lat"), "longitude": geo.get("lng")}
        else:
            m["location"] = None
        m.pop("geo", None)
        return m

    result = [normalise(dict(m)) for m in all_meters]

    # Apply filters
    if status:
        result = [m for m in result if m.get("installStatus", "").lower() == status.lower()]
    if make:
        result = [m for m in result if m.get("make", "").lower() == make.lower()]
    if phase:
        result = [m for m in result if m.get("phaseType", "").lower() == phase.lower()]
    if dt_code:
        result = [m for m in result if m.get("dtCode", "").lower() == dt_code.lower()]
    if zone:
        result = [
            m for m in result
            if m.get("hierarchy", {}).get("zone", {}).get("code", "").lower() == zone.lower()
            or m.get("hierarchy", {}).get("zone", {}).get("name", "").lower() == zone.lower()
        ]

    return result


async def get_hierarchy_tree() -> list[HierarchyTreeNode]:
    """Reconstruct the full network hierarchy tree from export data.

    Returns a tree: Zone → Circle → Division → Subdivision → Substation → Feeder → DT,
    with meter counts at each level.
    """
    cache_key = "hierarchy:tree"
    cached = cache.get(cache_key)
    if cached:
        return cached

    meters = await export_all_meters()

    levels = ["zone", "circle", "division", "subdivision", "substation", "feeder", "dt"]

    # Build a nested dict for de-duplication
    tree_data: dict = {}

    for meter in meters:
        h = meter.get("hierarchy", {})
        current = tree_data
        for level in levels:
            node_data = h.get(level, {})
            code = node_data.get("code", "unknown")
            name = node_data.get("name", "Unknown")
            key = code
            if key not in current:
                current[key] = {
                    "name": name,
                    "code": code,
                    "level": level,
                    "children": {},
                    "meter_count": 0,
                }
            current[key]["meter_count"] += 1
            current = current[key]["children"]

    # Convert nested dicts to HierarchyTreeNode models
    def to_tree(node_dict: dict) -> list[HierarchyTreeNode]:
        nodes = []
        for _key, data in sorted(node_dict.items()):
            children = to_tree(data["children"])
            nodes.append(HierarchyTreeNode(
                name=data["name"],
                code=data["code"],
                level=data["level"],
                children=children,
                meter_count=data["meter_count"],
            ))
        return nodes

    result = to_tree(tree_data)
    cache.set(cache_key, result, settings.export_cache_ttl_seconds)
    return result

