import pytest
import httpx
from app.main import app
from app.portal_client import portal_client


@pytest.fixture
async def client():
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as ac:
        yield ac


@pytest.mark.asyncio
async def test_health_endpoint(client):
    response = await client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert "portal_connected" in data


@pytest.mark.asyncio
async def test_search_meters(client):
    response = await client.get("/api/meters?page=1")
    assert response.status_code == 200
    data = response.json()
    assert "data" in data
    assert "total" in data
    assert len(data["data"]) > 0
    assert data["total"] > 0
    # Check structure
    meter = data["data"][0]
    assert "meter_id" in meter or "meterId" in meter


@pytest.mark.asyncio
async def test_meter_detail_legacy_and_v2(client):
    # J100000 is legacy, J100004 is v2
    for meter_id in ["J100000", "J100004"]:
        response = await client.get(f"/api/meters/{meter_id}")
        assert response.status_code == 200
        detail = response.json()
        assert detail["meter_id"] == meter_id
        assert "nameplate" in detail
        assert "hierarchy" in detail
        assert detail["nameplate"]["make"] != ""


@pytest.mark.asyncio
async def test_meter_consumption(client):
    response = await client.get("/api/meters/J100000/consumption")
    assert response.status_code == 200
    readings = response.json()
    assert isinstance(readings, list)
    assert len(readings) > 0
    first = readings[0]
    assert "timestamp" in first
    assert "T" in first["timestamp"]  # Verified ISO-8601 formatting


@pytest.mark.asyncio
async def test_meter_location(client):
    response = await client.get("/api/meters/J100000/location")
    assert response.status_code == 200
    loc = response.json()
    assert "latitude" in loc
    assert "longitude" in loc
    assert isinstance(loc["latitude"], float)
    assert isinstance(loc["longitude"], float)


@pytest.mark.asyncio
async def test_transformers_list(client):
    response = await client.get("/api/transformers?page=1")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 40
    assert len(data["data"]) == 20


@pytest.mark.asyncio
async def test_export_and_filtering(client):
    # Filter by make=Genus
    response = await client.get("/api/meters/export?make=Genus")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] > 0
    for item in data["data"]:
        assert item["make"] == "Genus"


@pytest.mark.asyncio
async def test_meter_not_found(client):
    # Non-existent meter should return 404 across detail, location, and consumption
    res_detail = await client.get("/api/meters/J999999")
    assert res_detail.status_code == 404

    res_loc = await client.get("/api/meters/J999999/location")
    assert res_loc.status_code == 404

    res_cons = await client.get("/api/meters/J999999/consumption")
    assert res_cons.status_code == 404


@pytest.mark.asyncio
async def test_hierarchy_tree(client):
    response = await client.get("/api/hierarchy")
    assert response.status_code == 200
    tree = response.json()
    assert isinstance(tree, list)
    assert len(tree) > 0
    # Top level should be zones
    assert all(node["level"] == "zone" for node in tree)
    # Check total meter count across zones equals 403
    total_meters = sum(node["meter_count"] for node in tree)
    assert total_meters == 403
