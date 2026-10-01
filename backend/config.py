"""Application settings: environment variables plus the JSON config files in backend/config/."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parent.parent
CONFIG_DIR = Path(__file__).resolve().parent / "config"
PROMPTS_FILE = ROOT_DIR / "PROMPTS.md"
FRONTEND_DIR = ROOT_DIR / "frontend"

load_dotenv(ROOT_DIR / ".env")


def _load_json(name: str) -> dict:
    with open(CONFIG_DIR / name, encoding="utf-8") as f:
        return json.load(f)


@dataclass(frozen=True)
class Settings:
    llm_provider: str
    anthropic_api_key: str | None
    llm_model: str
    llm_base_url: str | None
    llm_fallbacks: str
    llm_timeout: float
    data_dir: Path
    max_upload_mb: int
    host: str
    port: int
    risk_taxonomy: dict = field(repr=False)
    risk_config: dict = field(repr=False)
    comparison_categories: dict = field(repr=False)

    @property
    def db_path(self) -> Path:
        return self.data_dir / "app.db"

    @property
    def upload_dir(self) -> Path:
        return self.data_dir / "uploads"

    @property
    def offline(self) -> bool:
        return self.llm_provider == "offline"


def _resolve_provider() -> str:
    explicit = (os.getenv("LLM_PROVIDER") or "").strip().lower()
    if explicit:
        return explicit
    return "anthropic" if os.getenv("ANTHROPIC_API_KEY") else "offline"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    data_dir = Path(os.getenv("DATA_DIR") or ROOT_DIR / "data")
    if not data_dir.is_absolute():
        data_dir = (ROOT_DIR / data_dir).resolve()
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "uploads").mkdir(exist_ok=True)
    return Settings(
        llm_provider=_resolve_provider(),
        anthropic_api_key=os.getenv("ANTHROPIC_API_KEY") or None,
        llm_model=os.getenv("LLM_MODEL") or "claude-opus-5-5",
        llm_base_url=os.getenv("LLM_BASE_URL") or None,
        llm_fallbacks=(os.getenv("LLM_FALLBACKS") or "default").lower(),
        llm_timeout=float(os.getenv("LLM_TIMEOUT_SECONDS") or 120),
        data_dir=data_dir,
        max_upload_mb=int(os.getenv("MAX_UPLOAD_MB") or 25),
        host=os.getenv("HOST") or "127.0.0.1",
        port=int(os.getenv("PORT") or 8000),
        risk_taxonomy=_load_json("risk_taxonomy.json"),
        risk_config=_load_json("risk_config.json"),
        comparison_categories=_load_json("comparison_categories.json"),
    )
