"""Deterministic risk scoring (SPEC section 3.5). The LLM never produces these numbers."""

from __future__ import annotations

from collections import Counter


def retrieval_factor(similarity: float, tiers: list[dict]) -> float:
    for tier in tiers:
        if similarity >= tier["min_similarity"]:
            return tier["factor"]
    return tiers[-1]["factor"]


def finding_confidence(
    llm_confidence: float, evidence_type: str, similarity: float, config: dict
) -> float:
    llm_confidence = max(0.0, min(1.0, float(llm_confidence)))
    if evidence_type == "absence":
        value = min(llm_confidence, config["absence_confidence_cap"])
    else:
        value = llm_confidence * retrieval_factor(similarity, config["retrieval_factor"])
    return round(value, 2)


def overall_score(severities: list[str], weights: dict[str, float]) -> int:
    """100 x (1 - product(1 - w_i)): diminishing returns, so many minor findings stay minor."""
    remaining = 1.0
    for severity in severities:
        remaining *= 1.0 - weights.get(severity, 0.0)
    return round(100 * (1.0 - remaining))


def level_for(score: int, bands: list[dict]) -> str:
    level = bands[0]["level"]
    for band in sorted(bands, key=lambda b: b["min"]):
        if score >= band["min"]:
            level = band["level"]
    return level


def score_report(findings: list[dict], config: dict) -> dict:
    """Score counted, non-dismissed findings. Returns score, level and distributions."""
    counted = [f for f in findings if f["counted"] and f.get("status") != "dismissed"]
    score = overall_score([f["severity"] for f in counted], config["severity_weights"])
    by_severity = Counter(f["severity"] for f in counted)
    by_category: dict[str, dict[str, int]] = {}
    for f in counted:
        by_category.setdefault(f["category"], {}).setdefault(f["severity"], 0)
        by_category[f["category"]][f["severity"]] += 1
    return {
        "score": score,
        "level": level_for(score, config["level_bands"]),
        "by_severity": {s: by_severity.get(s, 0) for s in reversed(config["severity_order"])},
        "by_category": by_category,
        "counted": len(counted),
        "needs_review": sum(1 for f in findings if not f["counted"]),
    }


def portfolio_score(report_scores: list[int]) -> int | None:
    return round(sum(report_scores) / len(report_scores)) if report_scores else None
