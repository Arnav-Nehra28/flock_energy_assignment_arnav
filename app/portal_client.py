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
import re
import time
from typing import Any, Optional

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

# Meter IDs in the portal follow the pattern of a single letter followed by digits (e.g. J100000)
_METER_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")


class PortalAuthError(Exception):
    """Raised when portal authentication fails."""


class PortalNotFoundError(Exception):
    """Raised when a requested resource is not found (404)."""


class PortalUpstreamError(Exception):
    """Raised when the portal returns an unexpected error or is unreachable."""


class PortalRequestError(Exception):
    """Raised when a portal request fails unexpectedly."""


def _validate_meter_id(meter_id: str) -> str:
    """Basic validation on meter IDs to prevent path manipulation or malformed queries."""
    clean = meter_id.strip()
    if not clean or not _METER_ID_RE.match(clean):
        raise PortalNotFoundError(f"Invalid meter ID format: {meter_id!r}")
    return clean


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
        self._auth_lock: asyncio.Lock | None = None

    def _get_lock(self) -> asyncio.Lock:
        """Return an asyncio.Lock bound to the current event loop."""
        try:
            current_loop = asyncio.get_running_loop()
        except RuntimeError:
            current_loop = None

        if self._auth_lock is None or getattr(self, "_lock_loop", None) != current_loop:
            self._auth_lock = asyncio.Lock()
            self._lock_loop = current_loop
        return self._auth_lock

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
            old = self._client
            if old is not None and not old.is_closed:
                try:
                    if current_loop and current_loop.is_running():
                        current_loop.create_task(old.aclose())
                except RuntimeError:
                    pass

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
        """Log in if we don't have a valid session, serialized via lock."""
        self._get_client()
        if self._session_cookie and time.time() < self._session_expires_at:
            return

        async with self._get_lock():
            # Double-check inside the lock to avoid race conditions
            if self._session_cookie and time.time() < self._session_expires_at:
                return
            await self._login()

    async def _login(self) -> None:
        """Authenticate against the portal and store the session cookie."""
        client = self._get_client()
        logger.info("Authenticating with portal…")
        try:
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
        except httpx.HTTPError as exc:
            raise PortalUpstreamError(f"Could not reach portal for login: {exc}") from exc

        cookie_header = resp.headers.get("set-cookie", "")
        if "__Secure-better-auth.session_token=" not in cookie_header:
            raise PortalAuthError(
                f"Login failed with status {resp.status_code} — no session cookie returned"
            )

        token_part = cookie_header.split("__Secure-better-auth.session_token=")[1]
        token_value = token_part.split(";")[0]
        self._session_cookie = token_value
        client.cookies.set("__Secure-better-auth.session_token", self._session_cookie)

        max_age = 3600
        if "Max-Age=" in cookie_header:
            try:
                max_age = int(cookie_header.split("Max-Age=")[1].split(";")[0])
            except (ValueError, IndexError):
                pass
        self._session_expires_at = time.time() + max_age - 60
        self._signing_secret = None
        logger.info("Portal session established (expires in %ds)", max_age)

    # ── helpers ────────────────────────────────────────────────────────

    async def _get_json(self, path: str, params: dict | None = None) -> Any:
        """Authenticated GET returning parsed JSON with bounded retry on 401."""
        await self._ensure_session()
        client = self._get_client()
        try:
            resp = await client.get(path, params=params)
        except httpx.HTTPError as exc:
            raise PortalUpstreamError(f"Request to {path} failed: {exc}") from exc

        if resp.status_code == 401:
            logger.warning("Got 401 from %s, attempting re-authentication…", path)
            async with self._get_lock():
                await self._login()
            try:
                resp = await client.get(path, params=params)
            except httpx.HTTPError as exc:
                raise PortalUpstreamError(f"Retry request to {path} failed: {exc}") from exc
            if resp.status_code == 401:
                raise PortalAuthError("Portal request remained unauthorized after re-login")

        if resp.status_code == 404:
            raise PortalNotFoundError(f"Resource {path} not found on portal")
        if resp.status_code != 200:
            raise PortalRequestError(
                f"GET {path} returned {resp.status_code}: {resp.text[:200]}"
            )

        try:
            return resp.json()
        except ValueError as exc:
            raise PortalUpstreamError(f"Invalid JSON response from {path}") from exc

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
        """Search meters by meter ID or serial number."""
        return await self._get_json(
            "/portal/meters/search",
            params={"q": query, "page": page},
        )

    async def list_transformers(self, page: int = 1) -> dict:
        """List distribution transformers."""
        return await self._get_json("/portal/dts", params={"page": page})

    async def get_meter_geo(self, meter_id: str) -> dict:
        """Get geographic coordinates for a meter."""
        clean_id = _validate_meter_id(meter_id)
        return await self._get_json(f"/portal/meters/{clean_id}/geo")

    async def get_meter_energy(self, meter_id: str) -> dict:
        """Get consumption readings for a meter."""
        clean_id = _validate_meter_id(meter_id)
        return await self._get_json(f"/portal/meters/{clean_id}/energy")

    async def get_meter_detail_sveltekit(self, meter_id: str) -> dict:
        """Fetch SvelteKit server-load data for a meter detail page."""
        clean_id = _validate_meter_id(meter_id)
        return await self._get_json(f"/meters/{clean_id}/__data.json")

    async def export_all_meters(self) -> dict:
        """Bulk-export all meters using the HMAC-signed endpoint."""
        secret = await self._get_signing_secret()
        query = "page=1"
        ts, sig = self._compute_hmac("GET", "/portal/export", query, secret)

        await self._ensure_session()
        client = self._get_client()
        try:
            resp = await client.get(
                f"/portal/export?{query}",
                headers={"x-timestamp": ts, "x-signature": sig},
            )
        except httpx.HTTPError as exc:
            raise PortalUpstreamError(f"Export request failed: {exc}") from exc

        if resp.status_code == 401:
            async with self._get_lock():
                await self._login()
            self._signing_secret = None
            secret = await self._get_signing_secret()
            ts, sig = self._compute_hmac("GET", "/portal/export", query, secret)
            try:
                resp = await client.get(
                    f"/portal/export?{query}",
                    headers={"x-timestamp": ts, "x-signature": sig},
                )
            except httpx.HTTPError as exc:
                raise PortalUpstreamError(f"Export retry failed: {exc}") from exc
            if resp.status_code == 401:
                raise PortalAuthError("Export still unauthorized after re-auth")

        if resp.status_code != 200:
            raise PortalRequestError(
                f"Export failed with {resp.status_code}: {resp.text[:200]}"
            )

        try:
            return resp.json()
        except ValueError as exc:
            raise PortalUpstreamError("Non-JSON response from export endpoint") from exc

    async def get_session_info(self) -> dict | None:
        """Get current session info from Better Auth."""
        try:
            return await self._get_json("/api/auth/get-session")
        except PortalAuthError:
            logger.warning("Session check failed: not authenticated")
            return None
        except PortalUpstreamError as exc:
            logger.warning("Session check failed due to upstream error: %s", exc)
            return None
        except Exception as exc:
            logger.warning("Session check failed unexpectedly: %s", exc)
            return None


# Module-level singleton
portal_client = PortalClient()
