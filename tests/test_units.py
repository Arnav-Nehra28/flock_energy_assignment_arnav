import time
import pytest
from app.models import (
    PhaseType,
    InstallStatus,
    MeterSummary,
    MeterNameplate,
    GeoLocation,
    EnergyReading,
)
from app.services.cache import TTLCache
from app.services.meter_service import (
    _parse_energy_timestamp,
    _normalise_nameplate_legacy,
    _normalise_nameplate_v2,
    _decode_sveltekit_data,
)
from app.portal_client import PortalClient


def test_meter_summary_model():
    data = {
        "meterId": "J100000",
        "serialNo": "SE33962",
        "make": "HPL",
        "phaseType": "single",
        "installStatus": "Decommissioned",
        "dtCode": "DT-001",
    }
    meter = MeterSummary(**data)
    assert meter.meter_id == "J100000"
    assert meter.serial_no == "SE33962"
    assert meter.phase_type == PhaseType.single
    assert meter.install_status == InstallStatus.decommissioned


def test_cache_ttl():
    cache = TTLCache(default_ttl=1)
    cache.set("foo", "bar", ttl=1)
    assert cache.get("foo") == "bar"
    time.sleep(1.1)
    assert cache.get("foo") is None


def test_cache_invalidation():
    cache = TTLCache(default_ttl=60)
    cache.set("meter:1", "val1")
    cache.set("meter:2", "val2")
    cache.set("other:3", "val3")
    removed = cache.invalidate("meter:")
    assert removed == 2
    assert cache.get("meter:1") is None
    assert cache.get("other:3") == "val3"


def test_parse_energy_timestamp():
    raw = "24/06/2026 14:30"
    iso = _parse_energy_timestamp(raw)
    assert iso == "2026-06-24T14:30:00"


def test_normalise_nameplate_legacy():
    data = [
        {"parameterName": "Meter ID", "parameterValue": "J100000"},
        {"parameterName": "Serial No", "parameterValue": "SE33962"},
        {"parameterName": "Make", "parameterValue": "HPL"},
        {"parameterName": "Phase Type", "parameterValue": "single"},
        {"parameterName": "Installation Status", "parameterValue": "Decommissioned"},
        {"parameterName": "Installation Type", "parameterValue": "Whole Current"},
    ]
    np = _normalise_nameplate_legacy(data)
    assert np.meter_id == "J100000"
    assert np.make == "HPL"
    assert np.install_status == "Decommissioned"


def test_normalise_nameplate_v2():
    class_data = '{"installed_meter":{"MeterId":"J100004","SerialNo":"SE65293","Make":"Genus","PhaseType":"single","InstallationStatus":"Faulty","InstallationType":"CT Operated"}}'
    np = _normalise_nameplate_v2(class_data)
    assert np.meter_id == "J100004"
    assert np.serial_no == "SE65293"
    assert np.make == "Genus"
    assert np.install_status == "Faulty"


def test_decode_sveltekit_data():
    nodes = [
        None,
        {"type": "data"},
        {
            "type": "data",
            "data": [
                {"meterId": 1, "detail": 2},
                "J100000",
                {"make": 3},
                "HPL",
            ],
        },
    ]
    decoded = _decode_sveltekit_data(nodes)
    assert decoded["meterId"] == "J100000"
    assert decoded["detail"]["make"] == "HPL"


def test_hmac_computation():
    client = PortalClient()
    secret = "test-secret"
    ts, sig = client._compute_hmac("GET", "/portal/export", "page=1", secret)
    assert len(sig) == 64  # SHA256 hex digest length
    assert ts.isdigit()

