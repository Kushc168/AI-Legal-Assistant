"""Dashboard and analytics queries. All numbers come straight from the database."""

from __future__ import annotations

from collections import Counter, defaultdict

from .. import db
from . import scoring

_LATEST_REPORTS = """
SELECT r.* FROM risk_reports r
JOIN (SELECT contract_id, MAX(version) AS v FROM risk_reports
      WHERE status IN ('complete', 'partial') GROUP BY contract_id) l
  ON r.contract_id = l.contract_id AND r.version = l.v
JOIN contracts c ON c.id = r.contract_id
"""


def latest_reports() -> list[dict]:
    return db.query(_LATEST_REPORTS)


def dashboard() -> dict:
    reports = latest_reports()
    report_ids = [r["id"] for r in reports]
    findings = []
    if report_ids:
        marks = ",".join("?" for _ in report_ids)
        findings = db.query(
            f"SELECT f.*, c.name AS contract_name FROM risk_findings f JOIN contracts c ON c.id = f.contract_id "
            f"WHERE f.report_id IN ({marks}) AND f.counted = 1 AND f.status != 'dismissed'",
            report_ids,
        )
    by_severity = Counter(f["severity"] for f in findings)
    critical_open = [f for f in findings if f["severity"] == "Critical" and f["status"] == "open"]
    top = sorted(
        (f for f in findings if f["severity"] in {"Critical", "High"}),
        key=lambda f: ({"Critical": 0, "High": 1}[f["severity"]], -f["confidence"]),
    )[:6]
    portfolio = scoring.portfolio_score([r["overall_score"] for r in reports if r["overall_score"] is not None])
    recent = db.query(
        "SELECT id, name, filename, status, uploaded_at FROM contracts ORDER BY uploaded_at DESC LIMIT 5"
    )
    report_by_contract = {r["contract_id"]: r for r in reports}
    for c in recent:
        latest = db.query_one(
            "SELECT status, overall_score, overall_level FROM risk_reports WHERE contract_id = ? "
            "ORDER BY version DESC LIMIT 1", (c["id"],))
        c["risk"] = latest
        c["has_report"] = c["id"] in report_by_contract
    return {
        "total_contracts": db.scalar("SELECT COUNT(*) FROM contracts") or 0,
        "ai_conversations": db.scalar("SELECT COUNT(*) FROM conversations") or 0,
        "portfolio_risk": {
            "score": portfolio,
            "level": scoring.level_for(portfolio, _bands()) if portfolio is not None else None,
            "contracts": len(reports),
        },
        "critical_risks": len(critical_open),
        "contracts_compared": db.scalar("SELECT COUNT(*) FROM comparisons WHERE status = 'complete'") or 0,
        "risk_distribution": {s: by_severity.get(s, 0) for s in ("Critical", "High", "Medium", "Low")},
        "top_findings": [
            {k: f[k] for k in ("id", "contract_id", "contract_name", "title", "severity", "page", "clause_ref",
                               "confidence", "report_id")}
            for f in top
        ],
        "recent_contracts": recent,
        "recent_searches": db.query(
            "SELECT id, query, kind, conversation_id, created_at FROM searches ORDER BY created_at DESC LIMIT 5"
        ),
    }


def _bands() -> list[dict]:
    from ..config import get_settings

    return get_settings().risk_config["level_bands"]


def analytics() -> dict:
    reports = latest_reports()
    report_ids = [r["id"] for r in reports]
    findings = []
    if report_ids:
        marks = ",".join("?" for _ in report_ids)
        findings = db.query(f"SELECT * FROM risk_findings WHERE report_id IN ({marks})", report_ids)
    counted = [f for f in findings if f["counted"] and f["status"] != "dismissed"]

    # Portfolio trend: average of each contract's latest score as of each analysis date.
    all_reports = db.query(
        "SELECT contract_id, overall_score, substr(created_at, 1, 10) AS day FROM risk_reports "
        "WHERE status IN ('complete', 'partial') AND contract_id IN (SELECT id FROM contracts) ORDER BY created_at"
    )
    trend, state = [], {}
    for day in sorted({r["day"] for r in all_reports}):
        for r in all_reports:
            if r["day"] == day:
                state[r["contract_id"]] = r["overall_score"]
        trend.append({"day": day, "score": round(sum(state.values()) / len(state)), "contracts": len(state)})

    from ..config import get_settings

    names = {rt["id"]: rt["name"] for rt in get_settings().risk_taxonomy["risk_types"]}
    frequent = Counter(f["risk_type_id"] for f in counted).most_common(8)

    by_type: dict[str, list[int]] = defaultdict(list)
    contract_types = {c["id"]: c["contract_type"] or "other" for c in db.query("SELECT id, contract_type FROM contracts")}
    for r in reports:
        by_type[contract_types.get(r["contract_id"], "other")].append(r["overall_score"] or 0)

    category = defaultdict(lambda: Counter())
    for f in counted:
        category[f["category"]][f["severity"]] += 1

    comparisons = db.query(
        "SELECT substr(created_at, 1, 10) AS day, COUNT(*) AS n FROM comparisons WHERE status = 'complete' "
        "GROUP BY day ORDER BY day"
    )
    return {
        "trend": trend,
        "frequent_risks": [{"risk_type_id": t, "name": names.get(t, t), "count": n} for t, n in frequent],
        "risk_by_contract_type": [
            {"contract_type": t, "avg_score": round(sum(v) / len(v)), "contracts": len(v)} for t, v in sorted(by_type.items())
        ],
        "findings_by_status": dict(Counter(f["status"] for f in findings if f["counted"])),
        "by_category": {c: dict(v) for c, v in category.items()},
        "comparisons": comparisons,
        "contracts_analysed": len(reports),
        "evidence": {
            "clause": sum(1 for f in counted if f["evidence_type"] == "clause"),
            "absence": sum(1 for f in counted if f["evidence_type"] == "absence"),
        },
    }
