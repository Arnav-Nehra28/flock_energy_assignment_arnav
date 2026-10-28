"""Deterministic offline API tests with mocked portal responses."""

from unittest.mock import AsyncMock, patch
import pytest
import httpx

from app.main import app
from app.portal_client import PortalAuthError, PortalNotFoundError, PortalUpstreamError
from app.services.cache import cache


@pytest.fixture(autouse=True)
def clear_cache():
    """Clear in-memory cache before every test for clean isolation."""
    cache.invalidate()


@pytest.fixture
async def client():
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as ac:
        yield ac


# ── Health ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_health_endpoint_healthy(client):
    with patch("app.main.portal_client.get_session_info", new_callable=AsyncMock) as mock_sess:
        mock_sess.return_value = {
            "session": {"expiresAt": "2026-10-27T17:57:28.164Z"},
            "user": {"email": "operator@urja.local"},
        }
        res = await client.get("/health")
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "healthy"
        assert data["portal_connected"] is True
        assert data["portal_session"]["user"] == "operator@urja.local"


@pytest.mark.asyncio
async def test_health_endpoint_disconnected(client):
    with patch("app.main.portal_client.get_session_info", new_callable=AsyncMock) as mock_sess:
        mock_sess.return_value = None
        res = await client.get("/health")
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "healthy"
        assert data["portal_connected"] is False
        assert data["portal_session"] is None


# ── Meters Search ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_search_meters(client):
    fake_data = {
        "data": [
            {
                "meterId": "J100000",
                "serialNo": "SE33962",
                "make": "HPL",
                "phaseType": "single",
                "installStatus": "Installed",
                "dtCode": "DT-001",
            }
        ],
        "total": 1,
    }
    with patch("app.services.meter_service.portal_client.search_meters", new_callable=AsyncMock) as mock_search:
        mock_search.return_value = fake_data
        res = await client.get("/api/meters?page=1")
        assert res.status_code == 200
        data = res.json()
        assert data["total"] == 1
        assert len(data["data"]) == 1
        assert data["data"][0]["meter_id"] == "J100000"


# ── Meter Detail (Legacy & V2) ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_meter_detail_legacy(client):
    fake_sveltekit = {
        "nodes": [
            None,
            {"type": "data"},
            {
                "type": "data",
                "data": [
                    {"detail": 1, "hierarchy": 2},
                    {
                        "data": [
                            {"parameterName": "Meter ID", "parameterValue": "J100000"},
                            {"parameterName": "Serial No", "parameterValue": "SE33962"},
                            {"parameterName": "Make", "parameterValue": "HPL"},
                            {"parameterName": "Phase Type", "parameterValue": "single"},
                            {"parameterName": "Installation Status", "parameterValue": "Installed"},
                            {"parameterName": "Installation Type", "parameterValue": "Whole Current"},
                        ]
                    },
                    {
                        "Zone": "Jaipur Zone 1 (Z-01)",
                        "Circle": "Circle 1 (C-01)",
                        "Division": "Division 1 (D-01)",
                        "Subdivision": "Subdivision 1 (SD-01)",
                        "Sub Station": "Substation 1 (SS-01)",
                        "Feeder": "Feeder 1 (F-001)",
                        "DT": "Malviya Nagar DT 1 (DT-001)",
                    },
                ],
            },
        ]
    }
    fake_geo = {"data": {"latitude": "26.9389", "longitude": "75.8309"}}

    with patch("app.services.meter_service.portal_client.get_meter_detail_sveltekit", new_callable=AsyncMock) as mock_sk, \
         patch("app.services.meter_service.portal_client.get_meter_geo", new_callable=AsyncMock) as mock_geo:
        mock_sk.return_value = fake_sveltekit
        mock_geo.return_value = fake_geo

        res = await client.get("/api/meters/J100000")
        assert res.status_code == 200
        detail = res.json()
        assert detail["meter_id"] == "J100000"
        assert detail["nameplate"]["make"] == "HPL"
        assert detail["hierarchy"]["zone"]["code"] == "Z-01"
        assert detail["location"]["latitude"] == 26.9389


@pytest.mark.asyncio
async def test_meter_detail_v2(client):
    fake_sveltekit = {
        "nodes": [
            None,
            {"type": "data"},
            {
                "type": "data",
                "data": [
                    {"detail": 1, "hierarchy": 2},
                    {
                        "classData": '{"installed_meter":{"MeterId":"J100004","SerialNo":"SE65293","Make":"Genus","PhaseType":"single","InstallationStatus":"Faulty","InstallationType":"CT Operated"}}'
                    },
                    {"Zone": "Jaipur Zone 2 (Z-02)"},
                ],
            },
        ]
    }
    with patch("app.services.meter_service.portal_client.get_meter_detail_sveltekit", new_callable=AsyncMock) as mock_sk, \
         patch("app.services.meter_service.portal_client.get_meter_geo", new_callable=AsyncMock) as mock_geo:
        mock_sk.return_value = fake_sveltekit
        mock_geo.return_value = {"data": {"latitude": "26.91", "longitude": "75.82"}}

        res = await client.get("/api/meters/J100004")
        assert res.status_code == 200
        detail = res.json()
        assert detail["nameplate"]["make"] == "Genus"
        assert detail["nameplate"]["install_status"] == "Faulty"


# ── Meter Location & Consumption ───────────────────────────────────────

@pytest.mark.asyncio
async def test_meter_location(client):
    with patch("app.services.meter_service.portal_client.get_meter_geo", new_callable=AsyncMock) as mock_geo:
        mock_geo.return_value = {"data": {"latitude": "26.9389", "longitude": "75.8309"}}
        res = await client.get("/api/meters/J100000/location")
        assert res.status_code == 200
        loc = res.json()
        assert loc["latitude"] == 26.9389
        assert loc["longitude"] == 75.8309


@pytest.mark.asyncio
async def test_meter_consumption(client):
    with patch("app.services.meter_service.portal_client.get_meter_energy", new_callable=AsyncMock) as mock_en:
        mock_en.return_value = {
            "data": [
                {
                    "timestamp": "24/06/2026 14:30",
                    "kwh": "48438.74",
                    "kvah": "52313.84",
                    "voltR": "226",
                }
            ]
        }
        res = await client.get("/api/meters/J100000/consumption")
        assert res.status_code == 200
        data = res.json()
        assert len(data) == 1
        assert data[0]["timestamp"] == "2026-06-24T14:30:00+05:30"
        assert data[0]["kwh"] == 48438.74
        assert data[0]["volt_r"] == 226


# ── Export & Filtering ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_export_and_filtering(client):
    mock_fleet = {
        "data": [
            {
                "meterId": "J100000",
                "serialNo": "SE33962",
                "make": "HPL",
                "phaseType": "single",
                "installStatus": "Installed",
                "installType": "Whole Current",
                "build": "legacy",
                "dtCode": "DT-001",
                "hierarchy": {"zone": {"name": "Jaipur Zone 1", "code": "Z-01"}},
                "geo": {"lat": 26.9, "lng": 75.8},
            },
            {
                "meterId": "J100001",
                "serialNo": "SE33963",
                "make": "Genus",
                "phaseType": "three",
                "installStatus": "Faulty",
                "installType": "CT Operated",
                "build": "v2",
                "dtCode": "DT-002",
                "hierarchy": {"zone": {"name": "Jaipur Zone 2", "code": "Z-02"}},
                "geo": {"lat": 26.95, "lng": 75.85},
            },
        ]
    }
    with patch("app.services.meter_service.portal_client.export_all_meters", new_callable=AsyncMock) as mock_exp:
        mock_exp.return_value = mock_fleet

        # All export
        res_all = await client.get("/api/meters/export")
        assert res_all.status_code == 200
        assert res_all.json()["total"] == 2

        # Filter by make
        res_make = await client.get("/api/meters/export?make=Genus")
        assert res_make.status_code == 200
        data = res_make.json()
        assert data["total"] == 1
        assert data["data"][0]["make"] == "Genus"

        # Filter by phase
        res_phase = await client.get("/api/meters/export?phase=single")
        assert res_phase.status_code == 200
        assert res_phase.json()["total"] == 1


# ── Transformers ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_transformers_endpoints(client):
    fake_page = {
        "data": [
            {
                "code": "DT-001",
                "name": "Malviya Nagar DT 1",
                "feederCode": "F-001",
                "capacityKva": 100,
            }
        ],
        "total": 1,
    }
    with patch("app.services.transformer_service.portal_client.list_transformers", new_callable=AsyncMock) as mock_dts:
        mock_dts.return_value = fake_page

        res = await client.get("/api/transformers?page=1")
        assert res.status_code == 200
        data = res.json()
        assert data["total"] == 1
        assert data["data"][0]["code"] == "DT-001"

        res_all = await client.get("/api/transformers/all")
        assert res_all.status_code == 200
        assert res_all.json()["total"] == 1


# ── Hierarchy Tree ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_hierarchy_tree(client):
    mock_fleet = {
        "data": [
            {
                "meterId": "J100000",
                "dtCode": "DT-001",
                "hierarchy": {
                    "zone": {"name": "Jaipur Zone 1", "code": "Z-01"},
                    "circle": {"name": "Circle 1", "code": "C-01"},
                    "division": {"name": "Division 1", "code": "D-01"},
                    "subdivision": {"name": "Subdivision 1", "code": "SD-01"},
                    "substation": {"name": "Substation 1", "code": "SS-01"},
                    "feeder": {"name": "Feeder 1", "code": "F-001"},
                    "dt": {"name": "DT 1", "code": "DT-001"},
                },
            }
        ]
    }
    with patch("app.services.meter_service.portal_client.export_all_meters", new_callable=AsyncMock) as mock_exp:
        mock_exp.return_value = mock_fleet
        res = await client.get("/api/hierarchy")
        assert res.status_code == 200
        tree = res.json()
        assert len(tree) == 1
        assert tree[0]["code"] == "Z-01"
        assert tree[0]["meter_count"] == 1


# ── Error Handling (404, 502, 503) ─────────────────────────────────────

@pytest.mark.asyncio
async def test_not_found_handling(client):
    with patch("app.services.meter_service.portal_client.get_meter_detail_sveltekit", side_effect=PortalNotFoundError("Meter not found")):
        res = await client.get("/api/meters/J999999")
        assert res.status_code == 404

    with patch("app.services.meter_service.portal_client.get_meter_geo", side_effect=PortalNotFoundError("Location not found")):
        res = await client.get("/api/meters/J999999/location")
        assert res.status_code == 404


@pytest.mark.asyncio
async def test_upstream_and_auth_error_mapping(client):
    # Upstream error -> 502
    with patch("app.services.meter_service.portal_client.search_meters", side_effect=PortalUpstreamError("Portal timeout")):
        res = await client.get("/api/meters")
        assert res.status_code == 502

    # Auth failure -> 503
    with patch("app.services.meter_service.portal_client.search_meters", side_effect=PortalAuthError("Invalid credentials")):
        res = await client.get("/api/meters")
        assert res.status_code == 503


# ── OpenAPI Contract Check ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_openapi_specification_contract(client):
    res = await client.get("/openapi.json")
    assert res.status_code == 200
    spec = res.json()
    assert spec["openapi"].startswith("3.")
    paths = spec["paths"]

    # Verify all expected routes exist
    assert "/api/meters" in paths
    assert "/api/meters/export" in paths
    assert "/api/meters/{meter_id}" in paths
    assert "/api/meters/{meter_id}/location" in paths
    assert "/api/meters/{meter_id}/consumption" in paths
    assert "/api/transformers" in paths
    assert "/api/transformers/all" in paths
    assert "/api/hierarchy" in paths
    assert "/health" in paths
