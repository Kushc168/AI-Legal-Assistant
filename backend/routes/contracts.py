"""Contracts: upload, list, detail, document view, delete. No prompt text in route handlers."""

from __future__ import annotations

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse

from .. import db
from ..config import ROOT_DIR, get_settings
from ..services import ai_service, contracts, jobs

router = APIRouter(prefix="/api", tags=["contracts"])

SAMPLES_DIR = ROOT_DIR / "samples"


def _require(contract_id: str) -> dict:
    row = contracts.get(contract_id)
    if not row:
        raise HTTPException(404, "Contract not found")
    return row


@router.get("/contracts")
def list_contracts():
    return contracts.list_all()


@router.post("/contracts", status_code=201)
async def upload_contract(
    file: UploadFile = File(...), name: str | None = Form(None), parent_id: str | None = Form(None)
):
    data = await file.read()
    try:
        return contracts.create(file.filename or "contract.txt", data, name=name or None, parent_id=parent_id or None)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/samples/load", status_code=201)
def load_samples():
    """Load the bundled sample contracts (demo data, clearly named as samples)."""
    created = []
    existing = {c["filename"] for c in contracts.list_all()}
    msa_v1_id = None
    for path in sorted(SAMPLES_DIR.glob("*.txt")):
        if path.name in existing:
            continue
        parent = msa_v1_id if path.name.startswith("Sample_MSA_v2") else None
        row = contracts.create(path.name, path.read_bytes(), name=path.stem.replace("_", " "), parent_id=parent)
        if path.name.startswith("Sample_MSA_v1"):
            msa_v1_id = row["id"]
        created.append(row)
    return created


@router.get("/contracts/{contract_id}")
def contract_detail(contract_id: str):
    row = _require(contract_id)
    report = contracts.latest_report(contract_id)
    row["risk"] = (
        {k: report[k] for k in ("id", "status", "overall_score", "overall_level", "progress", "created_at")}
        if report else None
    )
    family = []
    if row["parent_id"]:
        parent = contracts.get(row["parent_id"])
        if parent:
            family.append({"id": parent["id"], "name": parent["name"], "relation": "previous version"})
    for child in db.query("SELECT id, name FROM contracts WHERE parent_id = ?", (contract_id,)):
        family.append({"id": child["id"], "name": child["name"], "relation": "next version"})
    row["versions"] = family
    row["chunk_count"] = db.scalar("SELECT COUNT(*) FROM chunks WHERE contract_id = ?", (contract_id,))
    return row


@router.get("/contracts/{contract_id}/document")
def contract_document(contract_id: str):
    _require(contract_id)
    full = contracts.get_with_text(contract_id)
    if not full["full_text"]:
        return {"pages": [], "chunks": []}
    text, starts = full["full_text"], full["pages"]
    pages = []
    for i, start in enumerate(starts):
        end = starts[i + 1] - 2 if i + 1 < len(starts) else len(text)
        pages.append({"page": i + 1, "start": start, "end": end, "text": text[start:end]})
    chunk_rows = [
        {k: c[k] for k in ("id", "idx", "start_offset", "end_offset", "page_start", "clause_ref", "heading")}
        for c in contracts.chunks_for(contract_id)
    ]
    return {"pages": pages, "chunks": chunk_rows}


@router.get("/contracts/{contract_id}/file")
def contract_file(contract_id: str):
    row = _require(contract_id)
    path = get_settings().upload_dir / f"{contract_id}.{row['file_type']}"
    if not path.exists():
        raise HTTPException(404, "Original file not found")
    return FileResponse(path, filename=row["filename"])


@router.post("/contracts/{contract_id}/summary")
def regenerate_summary(contract_id: str):
    row = _require(contract_id)
    if row["status"] != "indexed":
        raise HTTPException(409, "The contract is not indexed yet.")
    try:
        return ai_service.summarize_contract(contract_id)
    except Exception as exc:
        raise HTTPException(502, f"Summary failed: {exc}") from exc


@router.post("/contracts/{contract_id}/reindex", status_code=202)
def reindex(contract_id: str):
    _require(contract_id)
    db.update("contracts", contract_id, {"status": "processing", "error": None})
    jobs.submit(contracts.process, contract_id)
    return {"status": "processing"}


@router.delete("/contracts/{contract_id}", status_code=204)
def delete_contract(contract_id: str):
    _require(contract_id)
    contracts.delete(contract_id)
