"""Integration tests against the live Urja Meter Ops portal.

Run with:
    pytest -m live
"""

import pytest
import httpx
from app.main import app


@pytest.fixture
async def client():
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as ac:
        yield ac


@pytest.mark.live
@pytest.mark.asyncio
async def test_live_health(client):
    res = await client.get("/health")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "healthy"
    assert data["portal_connected"] is True


@pytest.mark.live
@pytest.mark.asyncio
async def test_live_search_and_detail(client):
    res = await client.get("/api/meters?page=1")
    assert res.status_code == 200
    data = res.json()
    assert data["total"] > 0
    meter_id = data["data"][0]["meter_id"]

    res_detail = await client.get(f"/api/meters/{meter_id}")
    assert res_detail.status_code == 200
    detail = res_detail.json()
    assert detail["meter_id"] == meter_id


@pytest.mark.live
@pytest.mark.asyncio
async def test_live_export(client):
    res = await client.get("/api/meters/export")
    assert res.status_code == 200
    data = res.json()
    assert data["total"] > 0

