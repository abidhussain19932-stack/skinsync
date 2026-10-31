"""Runtime configuration, read from environment variables (optionally a .env file)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field

try:  # optional
    from dotenv import load_dotenv

    load_dotenv()
except Exception:  # pragma: no cover
    pass


def _bool(name: str, default: bool) -> bool:
    v = os.getenv(name)
    if v is None or v == "":
        return default
    return v.strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class Settings:
    # YouCam (Perfect Corp) AI API v2 — Bearer auth with the API key.
    youcam_api_key: str = field(default_factory=lambda: os.getenv("YOUCAM_API_KEY", ""))
    youcam_base_url: str = field(
        default_factory=lambda: os.getenv("YOUCAM_BASE_URL", "https://yce-api-01.makeupar.com").rstrip("/")
    )
    youcam_poll_interval: float = field(default_factory=lambda: float(os.getenv("YOUCAM_POLL_INTERVAL", "2")))
    youcam_timeout: float = field(default_factory=lambda: float(os.getenv("YOUCAM_TIMEOUT", "120")))
    # DEMO_MODE: realistic canned YouCam responses so the app runs without a key.
    # Defaults to ON when no YOUCAM_API_KEY is set.
    demo_mode: bool = field(default_factory=lambda: _bool("DEMO_MODE", not os.getenv("YOUCAM_API_KEY")))

    # LLM (any OpenAI-compatible endpoint; defaults to Groq + gpt-oss-120b)
    llm_api_key: str = field(default_factory=lambda: os.getenv("LLM_API_KEY", os.getenv("GROQ_API_KEY", "")))
    llm_base_url: str = field(default_factory=lambda: os.getenv("LLM_BASE_URL", "https://api.groq.com/openai/v1"))
    llm_model: str = field(default_factory=lambda: os.getenv("LLM_MODEL", "openai/gpt-oss-120b"))
    llm_enabled: bool = field(default_factory=lambda: _bool("LLM_ENABLED", True))

    catalog_path: str = field(default_factory=lambda: os.getenv("CATALOG_PATH", "data/catalog.json"))
    max_upload_mb: int = field(default_factory=lambda: int(os.getenv("MAX_UPLOAD_MB", "9")))


settings = Settings()
