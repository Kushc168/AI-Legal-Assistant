"""Contract comparison endpoints (SPEC 4.10)."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

from .. import db
from ..services import ai_service, contracts, export, jobs

router = APIRouter(prefix="/api", tags=["compare"])


class NewComparison(BaseModel):
    contract_a_id: str
    contract_b_id: str
    perspective: Literal["party_a", "party_b", "neutral"] = "neutral"


@router.post("/comparisons", status_code=202)
def create(body: NewComparison):
    if body.contract_a_id == body.contract_b_id:
        raise HTTPException(400, "Choose two different contracts.")
    for cid in (body.contract_a_id, body.contract_b_id):
        row = contracts.get(cid)
        if not row:
            raise HTTPException(404, "Contract not found")
        if row["status"] != "indexed":
            raise HTTPException(409, f"{row['name']} is not indexed yet.")
    labels = {"party_a": "the first party named in the contract", "party_b": "the second party named in the contract",
              "neutral": "neutral"}
    comparison_id = ai_service.create_comparison(body.contract_a_id, body.contract_b_id, labels[body.perspective])
    jobs.submit(ai_service.compare_contracts, comparison_id)
    return {"id": comparison_id, "status": "queued"}


@router.get("/comparisons")
def list_comparisons():
    return db.query(
        "SELECT c.id, c.status, c.counts_json, c.created_at, c.contract_a_id, c.contract_b_id, "
        "a.name AS name_a, b.name AS name_b FROM comparisons c "
        "LEFT JOIN contracts a ON a.id = c.contract_a_id LEFT JOIN contracts b ON b.id = c.contract_b_id "
        "ORDER BY c.created_at DESC"
    )


@router.get("/comparisons/{comparison_id}")
def get(comparison_id: str):
    data = ai_service.get_comparison(comparison_id)
    if not data:
        raise HTTPException(404, "Comparison not found")
    return data


@router.get("/comparisons/{comparison_id}/export")
def export_comparison(comparison_id: str, format: Literal["md", "pdf", "docx"] = "md"):
    data = ai_service.get_comparison(comparison_id)
    if not data or data["status"] != "complete":
        raise HTTPException(404, "Comparison not ready")
    if format == "docx":
        raise HTTPException(501, "DOCX export is coming soon.")
    blocks = export.comparison_blocks(data)
    stem = f"Comparison_{data['contract_a']['name']}_vs_{data['contract_b']['name']}".replace(" ", "_")
    if format == "md":
        return Response(export.to_markdown(blocks), media_type="text/markdown",
                        headers={"Content-Disposition": f'attachment; filename="{stem}.md"'})
    return Response(export.to_pdf(blocks), media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="{stem}.pdf"'})


@router.delete("/comparisons/{comparison_id}", status_code=204)
def delete(comparison_id: str):
    with db.connect() as conn:
        conn.execute("DELETE FROM comparison_changes WHERE comparison_id = ?", (comparison_id,))
        conn.execute("DELETE FROM comparisons WHERE id = ?", (comparison_id,))
