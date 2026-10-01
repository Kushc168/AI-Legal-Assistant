"""SQLite persistence. One short-lived connection per operation keeps it safe across worker threads."""

from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator

from .config import get_settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS contracts (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    filename TEXT NOT NULL,
    file_type TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    status TEXT NOT NULL,                -- processing | indexed | failed
    error TEXT,
    page_count INTEGER DEFAULT 0,
    char_count INTEGER DEFAULT 0,
    full_text TEXT,
    pages_json TEXT,                     -- list of page start offsets into full_text
    contract_type TEXT,
    involves_personal_data INTEGER,
    summary_json TEXT,
    parent_id TEXT,                      -- previous version of the same contract
    uploaded_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS chunks (
    id TEXT PRIMARY KEY,
    contract_id TEXT NOT NULL,
    idx INTEGER NOT NULL,
    text TEXT NOT NULL,
    start_offset INTEGER NOT NULL,
    end_offset INTEGER NOT NULL,
    page_start INTEGER NOT NULL,
    page_end INTEGER NOT NULL,
    clause_ref TEXT,
    heading TEXT,
    embedding BLOB
);
CREATE INDEX IF NOT EXISTS ix_chunks_contract ON chunks(contract_id, idx);
CREATE TABLE IF NOT EXISTS risk_reports (
    id TEXT PRIMARY KEY,
    contract_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    status TEXT NOT NULL,                -- queued | running | complete | partial | failed
    progress_json TEXT,
    overall_score INTEGER,
    overall_level TEXT,
    counts_json TEXT,
    not_applicable_json TEXT,
    failed_categories_json TEXT,
    executive_summary TEXT,
    major_points_json TEXT,
    recommendations_json TEXT,
    checks_run INTEGER,
    model TEXT,
    mode TEXT,
    prompt_versions_json TEXT,
    duration_ms INTEGER,
    error TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_reports_contract ON risk_reports(contract_id, version);
CREATE TABLE IF NOT EXISTS risk_findings (
    id TEXT PRIMARY KEY,
    report_id TEXT NOT NULL,
    contract_id TEXT NOT NULL,
    risk_type_id TEXT NOT NULL,
    category TEXT NOT NULL,
    title TEXT NOT NULL,
    severity TEXT NOT NULL,
    severity_reason TEXT,
    evidence_type TEXT NOT NULL,         -- clause | absence
    clause_ref TEXT,
    page INTEGER,
    chunk_id TEXT,
    quote TEXT,
    quote_start INTEGER,
    quote_end INTEGER,
    explanation TEXT,
    reason TEXT,
    suggested_improvement TEXT,
    confidence REAL NOT NULL,
    counted INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'open', -- open | accepted | dismissed
    reviewer_note TEXT,
    search_queries_json TEXT,
    flags_json TEXT
);
CREATE INDEX IF NOT EXISTS ix_findings_report ON risk_findings(report_id);
CREATE TABLE IF NOT EXISTS comparisons (
    id TEXT PRIMARY KEY,
    contract_a_id TEXT NOT NULL,
    contract_b_id TEXT NOT NULL,
    perspective TEXT NOT NULL,
    status TEXT NOT NULL,
    progress_json TEXT,
    counts_json TEXT,
    executive_summary TEXT,
    major_points_json TEXT,
    recommendations_json TEXT,
    risk_delta_json TEXT,
    anchors_json TEXT,
    model TEXT,
    mode TEXT,
    prompt_versions_json TEXT,
    duration_ms INTEGER,
    error TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS comparison_changes (
    id TEXT PRIMARY KEY,
    comparison_id TEXT NOT NULL,
    sort INTEGER NOT NULL,
    category_id TEXT NOT NULL,
    category TEXT NOT NULL,
    status TEXT NOT NULL,                -- added | removed | modified | unchanged
    headline TEXT,
    value_a TEXT,
    value_b TEXT,
    clause_ref_a TEXT, page_a INTEGER, chunk_id_a TEXT, quote_a TEXT, quote_a_start INTEGER, quote_a_end INTEGER,
    clause_ref_b TEXT, page_b INTEGER, chunk_id_b TEXT, quote_b TEXT, quote_b_start INTEGER, quote_b_end INTEGER,
    change_summary TEXT,
    business_impact TEXT,
    favours TEXT,
    risk_direction TEXT,
    confidence REAL,
    impact_confidence REAL,
    flags_json TEXT
);
CREATE INDEX IF NOT EXISTS ix_changes_cmp ON comparison_changes(comparison_id, sort);
CREATE TABLE IF NOT EXISTS conversations (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    contract_ids_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS messages (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    citations_json TEXT,
    found INTEGER,
    confidence REAL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_messages_conv ON messages(conversation_id, created_at);
CREATE TABLE IF NOT EXISTS searches (
    id TEXT PRIMARY KEY,
    query TEXT NOT NULL,
    kind TEXT NOT NULL,                  -- chat | search
    conversation_id TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS kv (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

JSON_SUFFIX = "_json"


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(get_settings().db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with connect() as conn:
        conn.executescript(SCHEMA)


def _decode(row: sqlite3.Row | None) -> dict | None:
    """Row -> dict, parsing *_json columns into Python values (key without the suffix)."""
    if row is None:
        return None
    out: dict[str, Any] = {}
    for key in row.keys():
        value = row[key]
        if key.endswith(JSON_SUFFIX):
            out[key[: -len(JSON_SUFFIX)]] = json.loads(value) if value else None
        elif key == "embedding":
            continue
        else:
            out[key] = value
    return out


def _encode(values: dict) -> dict:
    """Serialise dict/list values for *_json columns."""
    return {
        k: (json.dumps(v) if k.endswith(JSON_SUFFIX) and v is not None else v)
        for k, v in values.items()
    }


def query(sql: str, params: tuple | list = ()) -> list[dict]:
    with connect() as conn:
        return [_decode(r) for r in conn.execute(sql, params).fetchall()]


def query_one(sql: str, params: tuple | list = ()) -> dict | None:
    with connect() as conn:
        return _decode(conn.execute(sql, params).fetchone())


def scalar(sql: str, params: tuple | list = ()) -> Any:
    with connect() as conn:
        row = conn.execute(sql, params).fetchone()
        return row[0] if row else None


def execute(sql: str, params: tuple | list = ()) -> None:
    with connect() as conn:
        conn.execute(sql, params)


def insert(table: str, values: dict) -> None:
    values = _encode(values)
    cols = ", ".join(values)
    marks = ", ".join("?" for _ in values)
    with connect() as conn:
        conn.execute(f"INSERT INTO {table} ({cols}) VALUES ({marks})", list(values.values()))


def insert_many(table: str, rows: list[dict]) -> None:
    if not rows:
        return
    rows = [_encode(r) for r in rows]
    cols = list(dict.fromkeys(k for r in rows for k in r))
    sql = f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)})"
    with connect() as conn:
        conn.executemany(sql, [[r.get(c) for c in cols] for r in rows])


def update(table: str, row_id: str, values: dict) -> None:
    values = _encode(values)
    assignments = ", ".join(f"{k} = ?" for k in values)
    with connect() as conn:
        conn.execute(f"UPDATE {table} SET {assignments} WHERE id = ?", [*values.values(), row_id])


def kv_get(key: str, default: Any = None) -> Any:
    value = scalar("SELECT value FROM kv WHERE key = ?", (key,))
    return json.loads(value) if value is not None else default


def kv_set(key: str, value: Any) -> None:
    with connect() as conn:
        conn.execute(
            "INSERT INTO kv (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, json.dumps(value)),
        )
