"""Dashboard, analytics, search, profile and settings."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .. import db
from ..config import get_settings
from ..services import ai_service, analytics, contracts, retrieval

router = APIRouter(prefix="/api", tags=["workspace"])

EDITABLE_THRESHOLDS = ("notice_days_threshold", "penalty_percent_threshold")


@router.get("/health")
def health():
    return {"ok": True, "mode": ai_service.mode()}


@router.get("/dashboard")
def dashboard():
    return analytics.dashboard()


@router.get("/analytics")
def analytics_view():
    return analytics.analytics()


@router.get("/search")
def search(q: str, contract_id: str | None = None, limit: int = 20):
    q = q.strip()
    if not q:
        raise HTTPException(400, "Enter a search term")
    ids = [contract_id] if contract_id else [c["id"] for c in contracts.list_all() if c["status"] == "indexed"]
    hits = retrieval.search(ids, q, top_k=min(max(limit, 1), 50))
    db.insert("searches", {"id": db.new_id("q"), "query": q, "kind": "search", "conversation_id": None,
                           "created_at": db.now_iso()})
    names = {c["id"]: c["name"] for c in contracts.list_all()}
    return [
        {"chunk_id": h.chunk["id"], "contract_id": h.chunk["contract_id"], "contract_name": names.get(h.chunk["contract_id"]),
         "page": h.chunk["page_start"], "clause_ref": h.chunk["clause_ref"], "heading": h.chunk["heading"],
         "text": h.chunk["text"], "start": h.chunk["start_offset"], "score": round(h.score, 3)}
        for h in hits
    ]


class Profile(BaseModel):
    name: str = Field(default="", max_length=80)
    role: str = Field(default="", max_length=80)
    organization: str = Field(default="", max_length=120)
    default_perspective: Literal["neutral", "customer", "supplier", "employer", "employee"] = "neutral"


@router.get("/profile")
def get_profile():
    return Profile(**(db.kv_get("profile", {}) or {})).model_dump()


@router.put("/profile")
def put_profile(body: Profile):
    db.kv_set("profile", body.model_dump())
    return body.model_dump()


class SettingsUpdate(BaseModel):
    min_confidence: float | None = Field(default=None, ge=0.1, le=0.95)
    notice_days_threshold: int | None = Field(default=None, ge=7, le=365)
    penalty_percent_threshold: float | None = Field(default=None, ge=1, le=100)


def apply_overrides() -> None:
    """Apply saved settings on top of risk_config.json (called at startup and on save)."""
    overrides = db.kv_get("risk_overrides", {}) or {}
    cfg = get_settings().risk_config
    if "min_confidence" in overrides:
        cfg["min_confidence"] = overrides["min_confidence"]
    for key in EDITABLE_THRESHOLDS:
        if key in overrides:
            cfg["thresholds"][key] = overrides[key]


@router.get("/settings")
def get_settings_view():
    settings = get_settings()
    cfg = settings.risk_config
    return {
        "llm": {
            "mode": ai_service.mode(),
            "provider": settings.llm_provider,
            "model": settings.llm_model if ai_service.mode() == "llm" else None,
            "key_configured": bool(settings.anthropic_api_key),
            "fallbacks": settings.llm_fallbacks,
        },
        "risk": {
            "min_confidence": cfg["min_confidence"],
            "absence_confidence_cap": cfg["absence_confidence_cap"],
            "severity_weights": cfg["severity_weights"],
            "level_bands": cfg["level_bands"],
            **{k: cfg["thresholds"][k] for k in EDITABLE_THRESHOLDS},
        },
        "prompts": ai_service.prompt_versions(),
        "storage": {"data_dir": str(settings.data_dir), "max_upload_mb": settings.max_upload_mb},
    }


@router.put("/settings")
def put_settings(body: SettingsUpdate):
    overrides = db.kv_get("risk_overrides", {}) or {}
    overrides.update({k: v for k, v in body.model_dump().items() if v is not None})
    db.kv_set("risk_overrides", overrides)
    apply_overrides()
    return get_settings_view()


@router.post("/settings/clear-workspace", status_code=204)
def clear_workspace():
    contracts.reset_all()
