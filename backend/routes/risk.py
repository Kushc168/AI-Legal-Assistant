"""Risk Analyzer endpoints (SPEC 3.11)."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

from .. import db
from ..config import get_settings
from ..services import ai_service, contracts, export, jobs

router = APIRouter(prefix="/api", tags=["risk"])


@router.post("/contracts/{contract_id}/risk-analysis", status_code=202)
def rerun_analysis(contract_id: str):
    row = contracts.get(contract_id)
    if not row:
        raise HTTPException(404, "Contract not found")
    if row["status"] != "indexed":
        raise HTTPException(409, "The contract is not indexed yet.")
    current = contracts.latest_report(contract_id)
    if current and current["status"] in {"queued", "running"}:
        return {"report_id": current["id"], "status": current["status"]}
    report_id = ai_service.create_risk_report(contract_id)
    jobs.submit(ai_service.risk_analyzer, contract_id, report_id)
    return {"report_id": report_id, "status": "queued"}


@router.get("/contracts/{contract_id}/risk-report")
def latest_report(contract_id: str, version: int | None = None):
    if version is not None:
        report = db.query_one("SELECT id FROM risk_reports WHERE contract_id = ? AND version = ?", (contract_id, version))
    else:
        report = contracts.latest_report(contract_id)
    if not report:
        raise HTTPException(404, "No risk report yet")
    data = ai_service.get_risk_report(report["id"])
    data["versions"] = db.query(
        "SELECT id, version, status, overall_score, overall_level, created_at FROM risk_reports "
        "WHERE contract_id = ? ORDER BY version DESC", (contract_id,))
    return data


@router.get("/risk-reports/{report_id}")
def get_report(report_id: str):
    data = ai_service.get_risk_report(report_id)
    if not data:
        raise HTTPException(404, "Report not found")
    return data


@router.get("/risk-reports/{report_id}/export")
def export_report(report_id: str, format: Literal["md", "pdf", "docx"] = "md"):
    data = ai_service.get_risk_report(report_id)
    if not data or data["status"] not in {"complete", "partial"}:
        raise HTTPException(404, "Report not ready")
    if format == "docx":
        raise HTTPException(501, "DOCX export is coming soon.")
    blocks = export.risk_blocks(data)
    stem = f"Risk_Report_{data['contract']['name'].replace(' ', '_')}_v{data['version']}"
    if format == "md":
        return Response(export.to_markdown(blocks), media_type="text/markdown",
                        headers={"Content-Disposition": f'attachment; filename="{stem}.md"'})
    return Response(export.to_pdf(blocks), media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="{stem}.pdf"'})


class FindingUpdate(BaseModel):
    status: Literal["open", "accepted", "dismissed"] | None = None
    reviewer_note: str | None = None


@router.patch("/risk-findings/{finding_id}")
def update_finding(finding_id: str, body: FindingUpdate):
    finding = db.query_one("SELECT * FROM risk_findings WHERE id = ?", (finding_id,))
    if not finding:
        raise HTTPException(404, "Finding not found")
    values = {k: v for k, v in body.model_dump().items() if v is not None}
    if values:
        db.update("risk_findings", finding_id, values)
        if "status" in values:
            ai_service.rescore_report(finding["report_id"])
    return db.query_one("SELECT * FROM risk_findings WHERE id = ?", (finding_id,))


@router.get("/clauses/{chunk_id}/explain")
def explain(chunk_id: str, audience: str = "business user"):
    try:
        return ai_service.explain_clause(chunk_id, audience)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/risk-taxonomy")
def taxonomy():
    settings = get_settings()
    return {
        "categories": settings.risk_taxonomy["categories"],
        "risk_types": [
            {k: rt.get(k) for k in ("id", "category", "name", "mode", "default_severity", "definition", "applies_if")}
            for rt in settings.risk_taxonomy["risk_types"]
        ],
    }
