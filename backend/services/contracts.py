"""Contract storage and the ingestion pipeline: extract -> chunk -> embed -> index -> analyse."""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

from .. import db
from ..config import get_settings
from . import embeddings, ingest, jobs, retrieval

log = logging.getLogger("contracts")

ALLOWED_TYPES = {"pdf", "docx", "txt", "md"}


def get(contract_id: str) -> dict | None:
    return db.query_one(
        "SELECT id, name, filename, file_type, size_bytes, status, error, page_count, char_count, "
        "contract_type, involves_personal_data, summary_json, parent_id, uploaded_at "
        "FROM contracts WHERE id = ?",
        (contract_id,),
    )


def get_with_text(contract_id: str) -> dict | None:
    return db.query_one("SELECT * FROM contracts WHERE id = ?", (contract_id,))


def chunks_for(contract_id: str) -> list[dict]:
    return db.query(
        "SELECT id, contract_id, idx, text, start_offset, end_offset, page_start, page_end, "
        "clause_ref, heading FROM chunks WHERE contract_id = ? ORDER BY idx",
        (contract_id,),
    )


def chunk(chunk_id: str) -> dict | None:
    return db.query_one(
        "SELECT id, contract_id, idx, text, start_offset, end_offset, page_start, page_end, "
        "clause_ref, heading FROM chunks WHERE id = ?",
        (chunk_id,),
    )


def latest_report(contract_id: str, complete_only: bool = False) -> dict | None:
    status = "AND status IN ('complete', 'partial')" if complete_only else ""
    return db.query_one(
        f"SELECT * FROM risk_reports WHERE contract_id = ? {status} ORDER BY version DESC LIMIT 1",
        (contract_id,),
    )


def list_all() -> list[dict]:
    rows = db.query(
        "SELECT id, name, filename, file_type, size_bytes, status, error, page_count, contract_type, "
        "parent_id, uploaded_at FROM contracts ORDER BY uploaded_at DESC"
    )
    for row in rows:
        report = latest_report(row["id"])
        row["risk"] = (
            {
                "report_id": report["id"],
                "status": report["status"],
                "score": report["overall_score"],
                "level": report["overall_level"],
                "progress": report["progress"],
            }
            if report
            else None
        )
    return rows


def create(filename: str, data: bytes, name: str | None = None, parent_id: str | None = None) -> dict:
    settings = get_settings()
    ext = Path(filename).suffix.lower().lstrip(".")
    if ext not in ALLOWED_TYPES:
        raise ValueError(f"Unsupported file type '.{ext}'. Upload PDF, DOCX or TXT.")
    if len(data) > settings.max_upload_mb * 1024 * 1024:
        raise ValueError(f"File is larger than {settings.max_upload_mb} MB.")
    if not data:
        raise ValueError("The file is empty.")
    if parent_id and not get(parent_id):
        raise ValueError("The previous version was not found.")
    contract_id = db.new_id("ct")
    stored = settings.upload_dir / f"{contract_id}.{ext}"
    stored.write_bytes(data)
    db.insert(
        "contracts",
        {
            "id": contract_id,
            "name": name or Path(filename).stem.replace("_", " "),
            "filename": Path(filename).name,
            "file_type": ext,
            "size_bytes": len(data),
            "status": "processing",
            "parent_id": parent_id,
            "uploaded_at": db.now_iso(),
        },
    )
    jobs.submit(process, contract_id)
    return get(contract_id)


def index(contract_id: str) -> None:
    """Extract, chunk and embed a stored upload. Raises ingest.ExtractionError on bad files."""
    settings = get_settings()
    row = get(contract_id)
    path = settings.upload_dir / f"{contract_id}.{row['file_type']}"
    pages = ingest.extract_pages(path, row["file_type"])
    document = ingest.build_document(pages)
    chunk_list = ingest.chunk_document(document)
    if not chunk_list:
        raise ingest.ExtractionError("No text could be extracted from this file.")
    vectors = embeddings.embed_texts([c.text for c in chunk_list])
    with db.connect() as conn:
        conn.execute("DELETE FROM chunks WHERE contract_id = ?", (contract_id,))
        conn.executemany(
            "INSERT INTO chunks (id, contract_id, idx, text, start_offset, end_offset, page_start, "
            "page_end, clause_ref, heading, embedding) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    f"c_{contract_id[3:]}_{c.idx:04d}", contract_id, c.idx, c.text, c.start_offset,
                    c.end_offset, c.page_start, c.page_end, c.clause_ref, c.heading,
                    embeddings.to_blob(vectors[i]),
                )
                for i, c in enumerate(chunk_list)
            ],
        )
    db.update(
        "contracts",
        contract_id,
        {
            "full_text": document.full_text,
            "pages_json": document.page_starts,
            "page_count": len(pages),
            "char_count": len(document.full_text),
            "contract_type": ingest.guess_contract_type(document.full_text),
            "involves_personal_data": int(ingest.guess_personal_data(document.full_text)),
        },
    )
    retrieval.invalidate(contract_id)


def process(contract_id: str) -> None:
    """Background job: index, summarise, then run the risk analyzer (SPEC 3.2)."""
    from . import ai_service

    try:
        index(contract_id)
    except Exception as exc:
        log.exception("Indexing failed for %s", contract_id)
        db.update("contracts", contract_id, {"status": "failed", "error": str(exc)})
        return
    db.update("contracts", contract_id, {"status": "indexed", "error": None})
    try:
        ai_service.summarize_contract(contract_id)
    except Exception:
        log.exception("Summary failed for %s (risk analysis continues)", contract_id)
    report_id = ai_service.create_risk_report(contract_id)
    ai_service.risk_analyzer(contract_id, report_id)


def delete(contract_id: str) -> None:
    row = get(contract_id)
    if not row:
        return
    settings = get_settings()
    with db.connect() as conn:
        report_ids = [r[0] for r in conn.execute("SELECT id FROM risk_reports WHERE contract_id = ?", (contract_id,))]
        for rid in report_ids:
            conn.execute("DELETE FROM risk_findings WHERE report_id = ?", (rid,))
        conn.execute("DELETE FROM risk_reports WHERE contract_id = ?", (contract_id,))
        cmp_ids = [
            r[0]
            for r in conn.execute(
                "SELECT id FROM comparisons WHERE contract_a_id = ? OR contract_b_id = ?",
                (contract_id, contract_id),
            )
        ]
        for cid in cmp_ids:
            conn.execute("DELETE FROM comparison_changes WHERE comparison_id = ?", (cid,))
            conn.execute("DELETE FROM comparisons WHERE id = ?", (cid,))
        conn.execute("DELETE FROM chunks WHERE contract_id = ?", (contract_id,))
        conn.execute("UPDATE contracts SET parent_id = NULL WHERE parent_id = ?", (contract_id,))
        conn.execute("DELETE FROM contracts WHERE id = ?", (contract_id,))
    retrieval.invalidate(contract_id)
    stored = settings.upload_dir / f"{contract_id}.{row['file_type']}"
    if stored.exists():
        stored.unlink()


def reset_all() -> None:
    """Remove every contract and derived record (Settings > Clear workspace)."""
    settings = get_settings()
    with db.connect() as conn:
        for table in ("risk_findings", "risk_reports", "comparison_changes", "comparisons",
                      "chunks", "contracts", "messages", "conversations", "searches"):
            conn.execute(f"DELETE FROM {table}")
    shutil.rmtree(settings.upload_dir, ignore_errors=True)
    settings.upload_dir.mkdir(parents=True, exist_ok=True)
    retrieval._cache.clear()
