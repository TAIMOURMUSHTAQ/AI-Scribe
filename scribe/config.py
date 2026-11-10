"""Settings loaded once from environment / .env. No secrets are ever logged."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = ROOT / ".env"
load_dotenv(ENV_FILE, override=False)


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def _float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    provider: str = os.getenv("AI_PROVIDER", "openai").strip().lower()  # openai | mock
    openai_api_key: str = os.getenv("OPENAI_API_KEY", "").strip()
    openai_base_url: str = os.getenv("OPENAI_BASE_URL", "").strip()
    clinical_model: str = os.getenv("OPENAI_CLINICAL_MODEL", "gpt-4.1-mini").strip()
    transcription_model: str = os.getenv("OPENAI_TRANSCRIPTION_MODEL", "gpt-transcribe").strip()
    reasoning_effort: str = os.getenv("OPENAI_REASONING_EFFORT", "").strip()  # only for reasoning models

    # Time limits (seconds). A stalled stream is cut off instead of loading forever.
    connect_timeout: float = _float("AI_CONNECT_TIMEOUT", 10)
    stream_idle_timeout: float = _float("AI_STREAM_IDLE_TIMEOUT", 40)
    pipeline_timeout: float = _float("AI_PIPELINE_TIMEOUT", 150)
    transcribe_timeout: float = _float("AI_TRANSCRIBE_TIMEOUT", 60)
    max_retries: int = _int("AI_MAX_RETRIES", 1)
    max_concurrent_ai: int = _int("AI_MAX_CONCURRENT", 8)

    max_audio_mb: int = _int("MAX_AUDIO_SIZE_MB", 25)
    max_input_chars: int = _int("MAX_INPUT_CHARS", 30000)
    max_context_chars: int = _int("MAX_CONTEXT_CHARS", 12000)

    service_token: str = os.getenv("SERVICE_TOKEN", "").strip()  # optional, for Laravel-to-service calls
    # Optional browser password for a hosted demo/staging site (both must be set to turn it on).
    basic_auth_user: str = os.getenv("BASIC_AUTH_USER", "").strip()
    basic_auth_password: str = os.getenv("BASIC_AUTH_PASSWORD", "").strip()
    log_level: str = os.getenv("LOG_LEVEL", "INFO").upper()

    @property
    def is_reasoning_model(self) -> bool:
        m = self.clinical_model.lower()
        return m.startswith(("gpt-5", "o1", "o3", "o4"))

    @property
    def ready(self) -> bool:
        return self.provider == "mock" or bool(self.openai_api_key)


settings = Settings()
