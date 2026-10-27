"""Pydantic models for the API — clean, typed representations of portal data."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


# ── Enums ──────────────────────────────────────────────────────────────

class PhaseType(str, Enum):
    single = "single"
    three = "three"


class InstallStatus(str, Enum):
    installed = "Installed"
    faulty = "Faulty"
    decommissioned = "Decommissioned"


class InstallType(str, Enum):
    whole_current = "Whole Current"
    ct_operated = "CT Operated"


class Make(str, Enum):
    hpl = "HPL"
    l_and_t = "L&T"
    genus = "Genus"
    allied = "Allied"
    secure = "Secure"


# ── Hierarchy ──────────────────────────────────────────────────────────

class HierarchyNode(BaseModel):
    """A single level in the network hierarchy (e.g. a Zone or Circle)."""
    name: str
    code: str


class MeterHierarchy(BaseModel):
    """Full 7-level network hierarchy for a meter."""
    zone: HierarchyNode
    circle: HierarchyNode
    division: HierarchyNode
    subdivision: HierarchyNode
    substation: HierarchyNode
    feeder: HierarchyNode
    dt: HierarchyNode


# ── Location ───────────────────────────────────────────────────────────

class GeoLocation(BaseModel):
    """Geographic coordinates of a meter."""
    latitude: float
    longitude: float


# ── Meter models ───────────────────────────────────────────────────────

class MeterSummary(BaseModel):
    """Compact meter listing returned by search / list endpoints."""
    meter_id: str = Field(..., alias="meterId", description="Unique meter identifier, e.g. J100000")
    serial_no: str = Field(..., alias="serialNo")
    make: str
    phase_type: PhaseType = Field(..., alias="phaseType")
    install_status: InstallStatus = Field(..., alias="installStatus")
    dt_code: str = Field(..., alias="dtCode")

    model_config = {"populate_by_name": True}


class MeterNameplate(BaseModel):
    """Nameplate (detail) parameters of a meter — normalised from both
    legacy and v2 formats."""
    meter_id: str
    serial_no: str
    make: str
    phase_type: str
    install_status: str
    install_type: str


class MeterDetail(BaseModel):
    """Full meter detail: nameplate, hierarchy, location, and optional
    consumption summary."""
    meter_id: str
    nameplate: MeterNameplate
    hierarchy: MeterHierarchy
    location: Optional[GeoLocation] = None


class MeterExport(BaseModel):
    """Enriched meter record from the bulk export endpoint."""
    meter_id: str = Field(..., alias="meterId")
    serial_no: str = Field(..., alias="serialNo")
    make: str
    phase_type: PhaseType = Field(..., alias="phaseType")
    install_status: InstallStatus = Field(..., alias="installStatus")
    install_type: InstallType = Field(..., alias="installType")
    build: str
    dt_code: str = Field(..., alias="dtCode")
    hierarchy: MeterHierarchy
    location: Optional[GeoLocation] = Field(None, alias="geo")

    model_config = {"populate_by_name": True}


# ── Distribution Transformer ──────────────────────────────────────────

class Transformer(BaseModel):
    """Distribution Transformer (DT)."""
    code: str
    name: str
    feeder_code: str = Field(..., alias="feederCode")
    capacity_kva: int = Field(..., alias="capacityKva")

    model_config = {"populate_by_name": True}


# ── Energy / Consumption ──────────────────────────────────────────────

class EnergyReading(BaseModel):
    """A single 30-minute consumption reading."""
    timestamp: str = Field(..., description="ISO-8601 timestamp (converted from DD/MM/YYYY HH:mm)")
    kwh: Optional[float] = Field(None, description="Cumulative active energy (kWh)")
    kvah: Optional[float] = Field(None, description="Cumulative apparent energy (kVAh)")
    volt_r: Optional[int] = Field(None, description="R-phase voltage (V)")


# ── Paginated wrapper ─────────────────────────────────────────────────

class PaginatedResponse(BaseModel):
    """Paginated API response envelope."""
    data: list
    total: int
    page: int
    page_size: int
    total_pages: int


# ── Hierarchy tree ─────────────────────────────────────────────────────

class HierarchyTreeNode(BaseModel):
    """A node in the reconstructed network hierarchy tree."""
    name: str
    code: str
    level: str
    children: list[HierarchyTreeNode] = Field(default_factory=list)
    meter_count: Optional[int] = None

