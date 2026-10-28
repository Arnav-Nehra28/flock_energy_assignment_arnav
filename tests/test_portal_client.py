"""Unit tests for PortalClient behavior, auth serialization, and error handling."""

import asyncio
from unittest.mock import AsyncMock
import pytest
import httpx

from app.portal_client import PortalAuthError, PortalClient, PortalUpstreamError


@pytest.mark.asyncio
async def test_concurrent_login_serialization():
    """Ensure concurrent calls to _ensure_session() only perform login once."""
    client = PortalClient()
    login_call_count = 0

    async def fake_login():
        nonlocal login_call_count
        login_call_count += 1
        await asyncio.sleep(0.05)
        client._session_cookie = "mock-token"
        client._session_expires_at = 9999999999.0

    client._login = fake_login
    client._get_client = lambda: AsyncMock()

    # Trigger 5 concurrent calls
    await asyncio.gather(
        client._ensure_session(),
        client._ensure_session(),
        client._ensure_session(),
        client._ensure_session(),
        client._ensure_session(),
    )

    assert login_call_count == 1
    assert client._session_cookie == "mock-token"


@pytest.mark.asyncio
async def test_get_json_reauth_on_401():
    """Verify that a 401 response triggers re-authentication and succeeds on retry."""
    client = PortalClient()
    client._session_cookie = "initial-token"
    client._session_expires_at = 9999999999.0

    first_call = True

    async def mock_login():
        client._session_cookie = "refreshed-token"

    client._login = mock_login

    mock_http_client = AsyncMock()

    resp_401 = httpx.Response(401, text="Unauthorized")
    resp_200 = httpx.Response(200, json={"status": "ok"})

    async def fake_get(*args, **kwargs):
        nonlocal first_call
        if first_call:
            first_call = False
            return resp_401
        return resp_200

    mock_http_client.get = fake_get
    client._get_client = lambda: mock_http_client

    data = await client._get_json("/test-path")
    assert data == {"status": "ok"}
    assert client._session_cookie == "refreshed-token"


@pytest.mark.asyncio
async def test_get_json_fails_if_401_persists():
    """Verify that a persistent 401 after re-auth raises PortalAuthError."""
    client = PortalClient()
    client._session_cookie = "token"
    client._session_expires_at = 9999999999.0

    async def mock_login():
        pass

    client._login = mock_login

    mock_http_client = AsyncMock()
    resp_401 = httpx.Response(401, text="Unauthorized")
    mock_http_client.get.return_value = resp_401
    client._get_client = lambda: mock_http_client

    with pytest.raises(PortalAuthError):
        await client._get_json("/test-path")
