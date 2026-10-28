"""Meter service — business logic between the API layer and the portal client.

Handles data normalisation, caching, filtering, and hierarchy reconstruction.
"""

from __future__ import annotations

import json
import logging
import math
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from app.config import settings
from app.models import (
    EnergyReading,
    GeoLocation,
    HierarchyNode,
    HierarchyTreeNode,
    MeterDetail,
    MeterHierarchy,
    MeterNameplate,
    MeterSummary,
    PaginatedResponse,
)
from app.portal_client import PortalNotFoundError, PortalUpstreamError, portal_client
from app.services.cache import cache

logger = logging.getLogger(__name__)

PAGE_SIZE = 20  # Portal's fixed page size

# IST offset: UTC+05:30 — all meters are deployed in Jaipur, Rajasthan
_IST = timezone(timedelta(hours=5, minutes=30))


class MeterParsingError(PortalUpstreamError):
    """Raised when required meter data cannot be parsed from upstream."""


# ── helpers ────────────────────────────────────────────────────────────

def _parse_energy_timestamp(raw: str) -> str:
    """Convert portal's DD/MM/YYYY HH:mm to ISO-8601 with IST (+05:30) offset.

    Returns an aware ISO-8601 string, e.g. '2026-06-24T14:30:00+05:30'.
    """
    clean = raw.strip()
    try:
        dt = datetime.strptime(clean, "%d/%m/%Y %H:%M")
        dt = dt.replace(tzinfo=_IST)
        return dt.isoformat()
    except (ValueError, AttributeError):
        logger.warning("Unrecognised timestamp format: %r", raw)
        return clean


def _safe_float(val: Any) -> Optional[float]:
    """Safely convert string/number to float, returning None on failure."""
    if val is None or val == "":
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        logger.warning("Failed converting float value: %r", val)
        return None


def _safe_int(val: Any) -> Optional[int]:
    """Safely convert string/number to int, returning None on failure."""
    if val is None or val == "":
        return None
    try:
        return int(float(val))
    except (ValueError, TypeError):
        logger.warning("Failed converting int value: %r", val)
        return None


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
    """Parse v2 format: JSON string with installed_meter object.

    Raises MeterParsingError if classData cannot be decoded.
    """
    try:
        parsed = json.loads(class_data_str)
    except (json.JSONDecodeError, TypeError) as exc:
        raise MeterParsingError(f"Could not parse v2 classData JSON: {exc}") from exc

    meter = parsed.get("installed_meter")
    if not isinstance(meter, dict):
        raise MeterParsingError("Malformed v2 classData: missing 'installed_meter' object")

    return MeterNameplate(
        meter_id=meter.get("MeterId", ""),
        serial_no=meter.get("SerialNo", ""),
        make=meter.get("Make", ""),
        phase_type=meter.get("PhaseType", ""),
        install_status=meter.get("InstallationStatus", ""),
        install_type=meter.get("InstallationType", ""),
    )


def _decode_sveltekit_data(nodes: list) -> dict:
    """Decode SvelteKit's compact __data.json format into a usable dict.

    SvelteKit uses a positional encoding where objects reference values
    by index in a flat array. Includes cycle and bounds protection.
    """
    if not isinstance(nodes, list) or len(nodes) < 3:
        raise PortalNotFoundError("Incomplete SvelteKit response structure")

    detail_node = nodes[2]
    if not detail_node or not isinstance(detail_node, dict):
        raise PortalNotFoundError("Empty page data node in SvelteKit response")

    if detail_node.get("type") == "error":
        msg = detail_node.get("error", {}).get("message", "Meter not found")
        raise PortalNotFoundError(msg)

    if detail_node.get("type") != "data":
        raise PortalNotFoundError("Expected data node from SvelteKit")

    flat = detail_node.get("data", [])
    if not flat or not isinstance(flat, list):
        raise PortalNotFoundError("Empty node data array")

    root = flat[0]
    seen: set[int] = set()

    def resolve(ref: Any) -> Any:
        if isinstance(ref, int):
            if ref < 0 or ref >= len(flat):
                logger.warning("SvelteKit index %d out of bounds (max %d)", ref, len(flat))
                return None
            if ref in seen:
                logger.warning("SvelteKit circular reference at index %d", ref)
                return None
            seen.add(ref)
            val = flat[ref]
            res = resolve(val)
            seen.discard(ref)
            return res
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
        if "(" in value and value.endswith(")"):
            name_part = value[:value.rfind("(")].strip()
            code_part = value[value.rfind("(") + 1 : -1].strip()
            return HierarchyNode(name=name_part, code=code_part)
        return HierarchyNode(name=value.strip(), code="")

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
    if cache.has(cache_key):
        return cache.get(cache_key)

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
    if cache.has(cache_key):
        return cache.get(cache_key)

    import asyncio
    sk_task = portal_client.get_meter_detail_sveltekit(meter_id)
    geo_task = portal_client.get_meter_geo(meter_id)
    sk_raw, geo_raw = await asyncio.gather(sk_task, geo_task, return_exceptions=True)

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
        raise PortalUpstreamError(
            f"Meter {meter_id}: unrecognized detail structure in upstream response"
        )

    hierarchy = _parse_hierarchy_from_sveltekit(decoded.get("hierarchy", {}))

    location = None
    if not isinstance(geo_raw, Exception) and geo_raw.get("data"):
        geo = geo_raw["data"]
        lat = _safe_float(geo.get("latitude"))
        lng = _safe_float(geo.get("longitude"))
        if lat is not None and lng is not None:
            location = GeoLocation(latitude=lat, longitude=lng)

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
    if cache.has(cache_key):
        return cache.get(cache_key)

    raw = await portal_client.get_meter_energy(meter_id)
    readings = []
    for r in raw.get("data", []):
        readings.append(EnergyReading(
            timestamp=_parse_energy_timestamp(r.get("timestamp", "")),
            kwh=_safe_float(r.get("kwh")),
            kvah=_safe_float(r.get("kvah")),
            volt_r=_safe_int(r.get("voltR")),
        ))

    cache.set(cache_key, readings, settings.cache_ttl_seconds)
    return readings


async def get_meter_location(meter_id: str) -> GeoLocation:
    """Get geographic coordinates for a meter."""
    cache_key = f"meter:geo:{meter_id}"
    if cache.has(cache_key):
        return cache.get(cache_key)

    raw = await portal_client.get_meter_geo(meter_id)
    geo = raw.get("data", {})
    if not geo:
        raise PortalNotFoundError(f"Location not found for meter {meter_id}")

    lat = _safe_float(geo.get("latitude"))
    lng = _safe_float(geo.get("longitude"))
    if lat is None or lng is None:
        raise PortalNotFoundError(f"Invalid coordinates for meter {meter_id}")

    location = GeoLocation(latitude=lat, longitude=lng)
    cache.set(cache_key, location, settings.cache_ttl_seconds)
    return location


async def export_all_meters(
    status: Optional[str] = None,
    make: Optional[str] = None,
    phase: Optional[str] = None,
    dt_code: Optional[str] = None,
    zone: Optional[str] = None,
) -> list[dict]:
    """Bulk export all meters with optional server-side filtering."""
    cache_key = "meters:export:all"
    if cache.has(cache_key):
        all_meters = cache.get(cache_key)
    else:
        raw = await portal_client.export_all_meters()
        all_meters = raw.get("data", [])
        cache.set(cache_key, all_meters, settings.export_cache_ttl_seconds)

    def normalise(m: dict) -> dict:
        item = dict(m)
        geo = item.get("geo")
        if geo:
            item["location"] = {"latitude": geo.get("lat"), "longitude": geo.get("lng")}
        else:
            item["location"] = None
        item.pop("geo", None)
        return item

    result = [normalise(m) for m in all_meters]

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

    Returns a tree: Zone -> Circle -> Division -> Subdivision -> Substation -> Feeder -> DT,
    with meter counts at each level.
    """
    cache_key = "hierarchy:tree"
    if cache.has(cache_key):
        return cache.get(cache_key)

    meters = await export_all_meters()
    levels = ["zone", "circle", "division", "subdivision", "substation", "feeder", "dt"]

    tree_data: dict = {}

    for meter in meters:
        h = meter.get("hierarchy", {})
        current = tree_data
        path_prefix = ""
        for level in levels:
            node_data = h.get(level, {})
            code = node_data.get("code") or ""
            name = node_data.get("name") or "Unknown"
            # Disambiguate missing or duplicate codes using parent path
            path_prefix = f"{path_prefix}/{level}:{code or name}"
            key = path_prefix

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
