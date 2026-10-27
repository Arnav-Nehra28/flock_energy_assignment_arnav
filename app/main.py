"""Urja Meter Ops API — FastAPI application entry point."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.portal_client import portal_client
from app.routers import hierarchy, meters, transformers

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: open portal client.  Shutdown: close it."""
    logger.info("Starting portal client…")
    await portal_client.start()
    yield
    logger.info("Shutting down portal client…")
    await portal_client.close()


app = FastAPI(
    title=settings.api_title,
    version=settings.api_version,
    description=settings.api_description,
    lifespan=lifespan,
    docs_url="/docs",        # Swagger UI
    redoc_url="/redoc",      # ReDoc
    openapi_url="/openapi.json",
)

# CORS — allow all origins for local development
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register routers
app.include_router(meters.router)
app.include_router(transformers.router)
app.include_router(hierarchy.router)


@app.get("/", tags=["Health"])
async def root():
    """Health check / root endpoint."""
    return {
        "service": settings.api_title,
        "version": settings.api_version,
        "docs": "/docs",
        "openapi": "/openapi.json",
    }


@app.get("/health", tags=["Health"])
async def health():
    """Detailed health check including portal session status."""
    session_info = await portal_client.get_session_info()
    return {
        "status": "healthy",
        "portal_connected": session_info is not None,
        "portal_session": {
            "user": session_info.get("user", {}).get("email") if session_info else None,
            "expires_at": session_info.get("session", {}).get("expiresAt") if session_info else None,
        } if session_info else None,
    }

