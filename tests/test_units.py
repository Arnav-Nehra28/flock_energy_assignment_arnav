import time
import pytest
from app.models import (
    EnergyReading,
    GeoLocation,
    HierarchyNode,
    MeterDetail,
    MeterExport,
    MeterNameplate,
    MeterSummary,
    Transformer,
)
from app.services.cache import TTLCache
from app.services.meter_service import (
    MeterParsingError,
    _decode_sveltekit_data,
    _normalise_nameplate_legacy,
    _normalise_nameplate_v2,
    _parse_energy_timestamp,
    _safe_float,
    _safe_int,
)
from app.portal_client import PortalClient, PortalNotFoundError, _validate_meter_id


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
    assert meter.phase_type == "single"
    assert meter.install_status == "Decommissioned"


def test_cache_ttl_and_zero():
    cache = TTLCache(default_ttl=5)

    # Standard TTL
    cache.set("foo", "bar", ttl=1)
    assert cache.has("foo")
    assert cache.get("foo") == "bar"
    time.sleep(1.1)
    assert not cache.has("foo")
    assert cache.get("foo") is None

    # TTL = 0 should expire immediately and not default to default_ttl
    cache.set("instant", "val", ttl=0)
    time.sleep(0.01)
    assert not cache.has("instant")
    assert cache.get("instant") is None


def test_cache_empty_list():
    cache = TTLCache(default_ttl=60)
    # An empty list is a valid cached response (e.g. empty search results)
    cache.set("empty_list", [])
    assert cache.has("empty_list")
    assert cache.get("empty_list") == []


def test_cache_invalidation():
    cache = TTLCache(default_ttl=60)
    cache.set("meter:1", "val1")
    cache.set("meter:2", "val2")
    cache.set("other:3", "val3")
    removed = cache.invalidate("meter:")
    assert removed == 2
    assert not cache.has("meter:1")
    assert cache.get("other:3") == "val3"


def test_parse_energy_timestamp_with_ist():
    raw = "24/06/2026 14:30"
    iso = _parse_energy_timestamp(raw)
    # Must contain +05:30 IST offset
    assert iso == "2026-06-24T14:30:00+05:30"


def test_parse_energy_timestamp_invalid():
    raw = "invalid-date"
    assert _parse_energy_timestamp(raw) == "invalid-date"


def test_safe_numeric_conversions():
    assert _safe_float("123.45") == 123.45
    assert _safe_float("") is None
    assert _safe_float(None) is None
    assert _safe_float("bad") is None

    assert _safe_int("226") == 226
    assert _safe_int("226.0") == 226
    assert _safe_int("") is None
    assert _safe_int("bad") is None


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


def test_normalise_nameplate_v2_valid():
    class_data = '{"installed_meter":{"MeterId":"J100004","SerialNo":"SE65293","Make":"Genus","PhaseType":"single","InstallationStatus":"Faulty","InstallationType":"CT Operated"}}'
    np = _normalise_nameplate_v2(class_data)
    assert np.meter_id == "J100004"
    assert np.serial_no == "SE65293"
    assert np.make == "Genus"
    assert np.install_status == "Faulty"


def test_normalise_nameplate_v2_malformed():
    # Broken JSON should raise MeterParsingError
    with pytest.raises(MeterParsingError):
        _normalise_nameplate_v2('{"installed_meter": invalid-json')

    # Missing installed_meter key should raise MeterParsingError
    with pytest.raises(MeterParsingError):
        _normalise_nameplate_v2('{"some_other_key": {}}')


def test_decode_sveltekit_data_normal():
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


def test_decode_sveltekit_data_out_of_bounds_and_cycles():
    # Out of bounds reference
    nodes_oob = [
        None,
        {"type": "data"},
        {
            "type": "data",
            "data": [
                {"bad_ref": 99},  # index 99 does not exist
            ],
        },
    ]
    decoded = _decode_sveltekit_data(nodes_oob)
    assert decoded["bad_ref"] is None

    # Circular reference (node 1 points to node 1)
    nodes_cycle = [
        None,
        {"type": "data"},
        {
            "type": "data",
            "data": [
                {"child": 1},
                {"loop": 1},
            ],
        },
    ]
    decoded = _decode_sveltekit_data(nodes_cycle)
    assert decoded["child"]["loop"] is None


def test_hmac_computation():
    client = PortalClient()
    secret = "test-secret"
    ts, sig = client._compute_hmac("GET", "/portal/export", "page=1", secret)
    assert len(sig) == 64
    assert ts.isdigit()


def test_validate_meter_id():
    assert _validate_meter_id("J100000") == "J100000"
    assert _validate_meter_id("M-1234") == "M-1234"

    with pytest.raises(PortalNotFoundError):
        _validate_meter_id("../../../etc/passwd")

    with pytest.raises(PortalNotFoundError):
        _validate_meter_id("")
