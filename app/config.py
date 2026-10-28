"""Application configuration via environment variables."""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Configuration loaded from environment / .env file."""

    # Portal connection — must be configured via environment or .env
    portal_base_url: str = "https://urja-ops.flockenergy.tech"
    portal_email: str
    portal_password: str

    # Cache settings
    cache_ttl_seconds: int = 300  # 5 minutes for list/search data
    export_cache_ttl_seconds: int = 600  # 10 minutes for bulk export

    # API settings
    api_title: str = "Urja Meter Ops API"
    api_version: str = "1.0.0"
    api_description: str = (
        "A clean REST API over the Urja Meter Ops legacy portal. "
        "Provides programmatic access to smart-meter data, distribution "
        "transformers, network hierarchy, and consumption readings."
    )

    model_config = {"env_prefix": "URJA_", "env_file": ".env"}


settings = Settings()
