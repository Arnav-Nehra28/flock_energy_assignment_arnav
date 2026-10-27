"""HTTP client for the Urja Meter Ops portal.

Handles authentication, session management, HMAC signing for export,
and exposes clean async methods for each portal endpoint.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import time
from typing import Any, Optional

import httpx

from app.config import settings

logger = logging.getLogger(__name__)


class PortalAuthError(Exception):
    """Raised when portal authentication fails."""


class PortalRequestError(Exception):
    """Raised when a portal request fails unexpectedly."""


class PortalClient:
    """Async HTTP client wrapping the Urja Meter Ops portal.

    Manages a single session cookie and re-authenticates automatically
    when it expires (1-hour TTL).
    """

    def __init__(self) -> None:
        self._client: Optional[httpx.AsyncClient] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._session_cookie: Optional[str] = None
        self._session_expires_at: float = 0
        self._signing_secret: Optional[str] = None

    # ── lifecycle ──────────────────────────────────────────────────────

    def _get_client(self) -> httpx.AsyncClient:
        """Return an active httpx client bound to the current running event loop."""
        try:
            current_loop = asyncio.get_running_loop()
        except RuntimeError:
            current_loop = None

        if (
            self._client is None
            or self._client.is_closed
            or (current_loop is not None and self._loop != current_loop)
        ):
            self._client = httpx.AsyncClient(
                base_url=settings.portal_base_url,
                timeout=30.0,
                follow_redirects=False,
            )
            self._loop = current_loop
            if self._session_cookie:
                self._client.cookies.set(
                    "__Secure-better-auth.session_token", self._session_cookie
                )
        return self._client

    async def start(self) -> None:
        """Create the underlying httpx client."""
        self._get_client()

    async def close(self) -> None:
        """Shut down the underlying httpx client."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None
            self._loop = None

    # ── authentication ─────────────────────────────────────────────────

    async def _ensure_session(self) -> None:
        """Log in if we don't have a valid session."""
        self._get_client()
        if self._session_cookie and time.time() < self._session_expires_at:
            return
        await self._login()

    async def _login(self) -> None:
        """Authenticate against the portal and store the session cookie."""
        client = self._get_client()
        logger.info("Authenticating with portal…")
        resp = await client.post(
            "/login",
            data={
                "email": settings.portal_email,
                "password": settings.portal_password,
            },
            headers={
                "Origin": settings.portal_base_url,
                "Content-Type": "application/x-www-form-urlencoded",
            },
        )
        cookie_header = resp.headers.get("set-cookie", "")
        if "__Secure-better-auth.session_token=" not in cookie_header:
            raise PortalAuthError(
                f"Login failed — status {resp.status_code}, no session cookie returned"
            )
        # Extract token value
        token_part = cookie_header.split("__Secure-better-auth.session_token=")[1]
        token_value = token_part.split(";")[0]
        self._session_cookie = token_value
        client.cookies.set("__Secure-better-auth.session_token", self._session_cookie)

        # Parse Max-Age for expiry (default 3600s, subtract 60s safety margin)
        max_age = 3600
        if "Max-Age=" in cookie_header:
            try:
                max_age = int(cookie_header.split("Max-Age=")[1].split(";")[0])
            except (ValueError, IndexError):
                pass
        self._session_expires_at = time.time() + max_age - 60

        # Clear cached signing secret on new session
        self._signing_secret = None
        logger.info("Portal session established (expires in %ds)", max_age)

    # ── helpers ────────────────────────────────────────────────────────

    async def _get_json(self, path: str, params: dict | None = None) -> Any:
        """Authenticated GET returning parsed JSON."""
        await self._ensure_session()
        client = self._get_client()
        resp = await client.get(
            path,
            params=params,
        )
        if resp.status_code == 401:
            # Session expired — re-auth and retry once
            logger.warning("Session expired, re-authenticating…")
            await self._login()
            resp = await client.get(
                path,
                params=params,
            )
        if resp.status_code != 200:
            raise PortalRequestError(
                f"GET {path} returned {resp.status_code}: {resp.text[:200]}"
            )
        return resp.json()

    # ── HMAC signing for export ────────────────────────────────────────

    async def _get_signing_secret(self) -> str:
        """Fetch (and cache) the HMAC signing secret."""
        if self._signing_secret:
            return self._signing_secret
        data = await self._get_json("/portal/keys")
        self._signing_secret = data["data"]["signingSecret"]
        return self._signing_secret

    def _compute_hmac(self, method: str, path: str, query: str, secret: str) -> tuple[str, str]:
        """Compute HMAC-SHA256 signature matching the portal's scheme.

        Returns (timestamp, hex_signature).
        """
        timestamp = str(int(time.time()))
        message = f"{method}\n{path}\n{query}\n{timestamp}"
        sig = hmac.new(
            secret.encode(),
            message.encode(),
            hashlib.sha256,
        ).hexdigest()
        return timestamp, sig

    # ── public portal methods ──────────────────────────────────────────

    async def search_meters(self, query: str = "", page: int = 1) -> dict:
        """Search meters by meter ID or serial number.

        Returns ``{ "data": [...], "total": N }``.
        """
        return await self._get_json(
            "/portal/meters/search",
            params={"q": query, "page": page},
        )

    async def list_transformers(self, page: int = 1) -> dict:
        """List distribution transformers.

        Returns ``{ "data": [...], "total": N }``.
        """
        return await self._get_json("/portal/dts", params={"page": page})

    async def get_meter_geo(self, meter_id: str) -> dict:
        """Get geographic coordinates for a meter.

        Returns ``{ "data": { "latitude": "...", "longitude": "..." } }``.
        """
        return await self._get_json(f"/portal/meters/{meter_id}/geo")

    async def get_meter_energy(self, meter_id: str) -> dict:
        """Get consumption readings for a meter.

        Returns ``{ "data": [{ timestamp, kwh, kvah, voltR }, ...] }``.
        """
        return await self._get_json(f"/portal/meters/{meter_id}/energy")

    async def get_meter_detail_sveltekit(self, meter_id: str) -> dict:
        """Fetch the SvelteKit server-load data for a meter detail page.

        Returns the raw SvelteKit ``__data.json`` response.
        """
        return await self._get_json(f"/meters/{meter_id}/__data.json")

    async def export_all_meters(self) -> dict:
        """Bulk-export all meters using the HMAC-signed endpoint.

        Returns ``{ "data": [...], "total": N }`` with enriched records.
        """
        secret = await self._get_signing_secret()
        query = "page=1"
        ts, sig = self._compute_hmac("GET", "/portal/export", query, secret)

        await self._ensure_session()
        client = self._get_client()
        resp = await client.get(
            f"/portal/export?{query}",
            headers={"x-timestamp": ts, "x-signature": sig},
        )
        if resp.status_code == 401:
            await self._login()
            secret = await self._get_signing_secret()
            ts, sig = self._compute_hmac("GET", "/portal/export", query, secret)
            resp = await client.get(
                f"/portal/export?{query}",
                headers={"x-timestamp": ts, "x-signature": sig},
            )
        if resp.status_code != 200:
            raise PortalRequestError(
                f"Export failed with {resp.status_code}: {resp.text[:200]}"
            )
        return resp.json()

    async def get_session_info(self) -> dict | None:
        """Get current session info from Better Auth."""
        try:
            return await self._get_json("/api/auth/get-session")
        except Exception:
            return None


# Module-level singleton
portal_client = PortalClient()

