"""Transformer service — DT listing with caching and pagination."""

from __future__ import annotations

import math

from app.config import settings
from app.models import PaginatedResponse, Transformer
from app.portal_client import portal_client
from app.services.cache import cache

PAGE_SIZE = 20


async def list_transformers(page: int = 1) -> PaginatedResponse:
    """List distribution transformers with pagination."""
    cache_key = f"dts:page:{page}"
    if cache.has(cache_key):
        return cache.get(cache_key)

    raw = await portal_client.list_transformers(page=page)
    total = raw.get("total", 0)
    items = [Transformer(**dt) for dt in raw.get("data", [])]

    result = PaginatedResponse(
        data=[t.model_dump() for t in items],
        total=total,
        page=page,
        page_size=PAGE_SIZE,
        total_pages=max(1, math.ceil(total / PAGE_SIZE)),
    )
    cache.set(cache_key, result, settings.cache_ttl_seconds)
    return result


async def get_all_transformers() -> list[dict]:
    """Fetch all transformers across all pages."""
    cache_key = "dts:all"
    if cache.has(cache_key):
        return cache.get(cache_key)

    first_page = await portal_client.list_transformers(page=1)
    total = first_page.get("total", 0)
    all_dts = list(first_page.get("data", []))

    total_pages = max(1, math.ceil(total / PAGE_SIZE))
    for page in range(2, total_pages + 1):
        page_data = await portal_client.list_transformers(page=page)
        all_dts.extend(page_data.get("data", []))

    result = [Transformer(**dt).model_dump() for dt in all_dts]
    cache.set(cache_key, result, settings.cache_ttl_seconds)
    return result
