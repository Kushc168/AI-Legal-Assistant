"""All AI features. Every LLM call goes through PROMPTS.md (via llm_client) and every result is
verified before it is stored. With no LLM configured, the same pipelines run on heuristics.

Public API (SPEC section 2):
    generate_answer, summarize_contract, explain_clause, risk_analyzer, compare_contracts,
    generate_executive_summary
"""

from __future__ import annotations

import json
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np

from .. import db
from ..config import get_settings
from ..prompts import loader
from . import contracts, embeddings, heuristics, retrieval, scoring, verifier
from .llm_client import LLMError, get_client, is_enabled

log = logging.getLogger("ai")

SERVICES_TYPES = {"services", "supply", "license"}
SUMMARY_FIELDS = {
    "purpose": "purpose of this agreement services scope",
    "effective_date": "effective date commencement",
    "term": "term of agreement duration years",
    "payment_terms": "payment terms fees invoices",
    "key_obligations": "obligations shall provide perform",
    "termination": "termination terminate this agreement",
    "renewal": "renewal automatically renew",
    "liability": "limitation of liability",
    "confidentiality": "confidential information",
    "governing_law": "governing law jurisdiction",
}


def mode() -> str:
    return "llm" if is_enabled() else "offline"


def _model_label() -> str:
    return get_settings().llm_model if is_enabled() else "offline-heuristics"


def _perspective() -> str:
    profile = db.kv_get("profile", {}) or {}
    return profile.get("default_perspective") or "neutral"


# ---------------------------------------------------------------------------
# Question answering
# ---------------------------------------------------------------------------

def generate_answer(contract_ids: list[str], question: str, history: list[dict]) -> dict:
    """Answer a question from retrieved excerpts. Citations are verified against chunk text."""
    hits = retrieval.search(contract_ids, question, top_k=8)
    by_id = {h.chunk["id"]: h.chunk for h in hits}
    names = {c["id"]: c["name"] for c in (contracts.get(cid) for cid in contract_ids) if c}
    if is_enabled():
        history_text = "\n".join(
            f"{m['role']}: {m['content'][:600]}" for m in history[-6:]
        ) or "(none)"
        try:
            data = get_client().complete_json(
                "qa.answer",
                {
                    "question": question,
                    "excerpts": retrieval.format_excerpts(hits),
                    "history": history_text,
                    "contract_names": ", ".join(names.values()),
                },
            ).data
        except LLMError as exc:
            return {"found": False, "answer": f"The AI service is unavailable: {exc}", "citations": [],
                    "confidence": 0.0, "mode": "llm", "error": True}
    else:
        data = heuristics.answer(question, hits)

    citations, valid_ids = [], []
    for c in data.get("citations", []):
        chunk = by_id.get(c["chunk_id"])
        if not chunk:
            continue
        loc = verifier.locate_quote(c["quote"], chunk["text"])
        if not loc:
            continue
        valid_ids.append(c["chunk_id"])
        citations.append(_citation(chunk, c["quote"], loc, names))

    answer = data["answer"]
    flags = []
    if data["found"]:
        evidence = " ".join(h.chunk["text"] for h in hits)
        answer, changed = verifier.strip_ungrounded_sentences(answer, evidence, question)
        if changed:
            flags.append("ungrounded_numbers_removed")
        # Replace [chunk_id] markers with numbered citation markers; drop unverified ones.
        order = {cid: i + 1 for i, cid in enumerate(dict.fromkeys(valid_ids))}
        answer = re.sub(
            r"\[(c_[\w]+)\]",
            lambda m: f"[{order[m.group(1)]}]" if m.group(1) in order else "",
            answer,
        )
        if not citations:
            flags.append("no_verified_citations")
        elif not answer.strip():
            answer = "The answer could not be verified as written. The relevant passages are cited below."
    return {
        "found": bool(data["found"] and citations),
        "answer": answer.strip() if (data["found"] and citations) or not data["found"]
        else "I found related text but could not verify an answer against the contract. Try rephrasing the question.",
        "citations": _dedupe_citations(citations),
        "confidence": round(float(data.get("confidence", 0)), 2),
        "mode": mode(),
        "flags": flags,
    }


def _citation(chunk: dict, quote: str, loc: tuple[int, int], names: dict) -> dict:
    return {
        "chunk_id": chunk["id"],
        "contract_id": chunk["contract_id"],
        "contract_name": names.get(chunk["contract_id"], ""),
        "page": chunk["page_start"],
        "clause_ref": chunk["clause_ref"],
        "quote": chunk["text"][loc[0]:loc[1]],
        "start": chunk["start_offset"] + loc[0],
        "end": chunk["start_offset"] + loc[1],
    }


def _dedupe_citations(citations: list[dict]) -> list[dict]:
    seen, out = set(), []
    for c in citations:
        key = (c["chunk_id"], c["start"])
        if key not in seen:
            seen.add(key)
            out.append(c)
    return out


# ---------------------------------------------------------------------------
# Summary and clause explanation
# ---------------------------------------------------------------------------

def summarize_contract(contract_id: str) -> dict:
    contract = contracts.get_with_text(contract_id)
    chunk_list = contracts.chunks_for(contract_id)
    field_hits = {f: retrieval.search([contract_id], q, top_k=3) for f, q in SUMMARY_FIELDS.items()}
    contract_type = contract["contract_type"] or "other"
    personal = bool(contract["involves_personal_data"])

    if is_enabled():
        selected: dict[str, dict] = {c["id"]: c for c in chunk_list[:3]}
        for hits in field_hits.values():
            for h in hits:
                selected.setdefault(h.chunk["id"], h.chunk)
        chunks_sel = sorted(selected.values(), key=lambda c: c["idx"])[:28]
        result = get_client().complete_json(
            "contract.summarize",
            {"contract_name": contract["name"], "excerpts": retrieval.format_excerpts(chunks_sel)},
        )
        data, allowed = result.data, set(selected)
    else:
        data = heuristics.summary(field_hits, chunk_list[:3], contract_type, personal)
        allowed = {c["id"] for c in chunk_list}

    # Verify: every field must cite chunks that were actually provided.
    fields = {}
    for key, value in (data.get("fields") or {}).items():
        if value and value.get("chunk_ids") and verifier.citations_in_context(value["chunk_ids"], allowed):
            first = contracts.chunk(value["chunk_ids"][0])
            fields[key] = {**value, "page": first["page_start"] if first else None,
                           "clause_ref": first["clause_ref"] if first else None}
        else:
            fields[key] = None
    parties = [p for p in data.get("parties", []) if verifier.citations_in_context(p.get("chunk_ids", []), allowed)]
    summary = {
        "contract_type": data.get("contract_type") or contract_type,
        "involves_personal_data": data.get("involves_personal_data"),
        "parties": parties,
        "fields": fields,
        "mode": mode(),
        "created_at": db.now_iso(),
    }
    personal_flag = summary["involves_personal_data"]
    db.update(
        "contracts",
        contract_id,
        {
            "summary_json": summary,
            "contract_type": summary["contract_type"],
            "involves_personal_data": int(personal_flag) if personal_flag is not None else int(personal),
        },
    )
    return summary


def explain_clause(chunk_id: str, audience: str = "business user") -> dict:
    chunk = contracts.chunk(chunk_id)
    if not chunk:
        raise ValueError("Clause not found")
    neighbours = [
        c for c in contracts.chunks_for(chunk["contract_id"])
        if abs(c["idx"] - chunk["idx"]) == 1
    ]
    definitions = [
        h.chunk for h in retrieval.search([chunk["contract_id"]], "definitions means", top_k=2)
        if h.chunk["id"] != chunk_id
    ]
    context = {c["id"]: c for c in neighbours + definitions}
    allowed = {chunk_id, *context}
    if is_enabled():
        try:
            data = get_client().complete_json(
                "clause.explain",
                {
                    "clause_excerpt": retrieval.format_excerpts([chunk]),
                    "context_excerpts": retrieval.format_excerpts(list(context.values())),
                    "audience": audience,
                },
            ).data
        except LLMError as exc:
            raise ValueError(f"The AI service is unavailable: {exc}") from exc
    else:
        matched = [
            rt for rt in get_settings().risk_taxonomy["risk_types"]
            if any(re.search(p, chunk["text"], re.IGNORECASE) for p in rt.get("patterns", []))
        ]
        data = heuristics.explain(chunk, matched)

    sources = {chunk_id: chunk, **context}
    citations = []
    for c in data.get("citations", []):
        src = sources.get(c["chunk_id"])
        loc = verifier.locate_quote(c["quote"], src["text"]) if src else None
        if loc:
            citations.append({"chunk_id": src["id"], "page": src["page_start"], "clause_ref": src["clause_ref"],
                              "quote": src["text"][loc[0]:loc[1]]})
    evidence = " ".join(s["text"] for s in sources.values())
    plain, _ = verifier.strip_ungrounded_sentences(data["plain_language"], evidence)
    return {
        "chunk": chunk,
        "plain_language": plain,
        "obligations": data.get("obligations", []),
        "key_terms": [t for t in data.get("key_terms", []) if t.get("chunk_id") in allowed],
        "watch_outs": data.get("watch_outs", []),
        "citations": citations,
        "mode": mode(),
    }


# ---------------------------------------------------------------------------
# Module 1 - Risk Analyzer
# ---------------------------------------------------------------------------

def create_risk_report(contract_id: str) -> str:
    previous = db.scalar("SELECT MAX(version) FROM risk_reports WHERE contract_id = ?", (contract_id,)) or 0
    report_id = db.new_id("rr")
    db.insert(
        "risk_reports",
        {
            "id": report_id,
            "contract_id": contract_id,
            "version": previous + 1,
            "status": "queued",
            "progress_json": {"done": [], "total": len(get_settings().risk_taxonomy["categories"])},
            "mode": mode(),
            "model": _model_label(),
            "created_at": db.now_iso(),
        },
    )
    return report_id


def _applicable(rt: dict, contract: dict) -> bool:
    condition = rt.get("applies_if")
    if condition == "personal_data":
        return bool(contract["involves_personal_data"])
    if condition == "services":
        return (contract["contract_type"] or "other") in SERVICES_TYPES
    return True


def _analyse_category(category: str, types: list[dict], contract: dict, chunk_list: list[dict]) -> dict:
    """Run one category: retrieval, LLM (or heuristics), verification. Returns verified findings."""
    settings = get_settings()
    cfg = settings.risk_config
    cid = contract["id"]
    queries = [q for rt in types for q in rt["queries"]]
    hits = retrieval.multi_search([cid], queries, top_k=max(cfg["retrieval_top_k"] * 2, 10))
    hit_by_id = {h.chunk["id"]: h for h in hits}
    prompt_ref = None

    if is_enabled():
        result = get_client().complete_json(
            "risk.analyze",
            {
                "category": category,
                "risk_types": [
                    {k: rt[k] for k in ("id", "name", "mode", "default_severity", "definition")}
                    for rt in types
                ],
                "thresholds": cfg["thresholds"],
                "contract_type": contract["contract_type"] or "other",
                "involves_personal_data": bool(contract["involves_personal_data"]),
                "perspective": _perspective(),
                "excerpts": retrieval.format_excerpts(hits),
            },
        )
        raw, prompt_ref = result.data, result.prompt_ref
        allowed_chunks = hit_by_id
    else:
        raw = heuristics.risk_category(types, hits, chunk_list, cfg["thresholds"])
        allowed_chunks = {c["id"]: retrieval.Hit(chunk=c, score=0.0, similarity=hit_by_id[c["id"]].similarity
                                                 if c["id"] in hit_by_id else 0.0) for c in chunk_list}

    types_by_id = {rt["id"]: rt for rt in types}
    threshold_numbers = {str(v) for v in cfg["thresholds"].values()}
    drops: dict[str, int] = {}
    findings: list[dict] = []

    def drop(rule: str) -> None:
        drops[rule] = drops.get(rule, 0) + 1

    for f in raw.get("findings", []):
        rt = types_by_id.get(f["risk_type_id"])
        if not rt:
            drop("unknown_type")
            continue
        hit = allowed_chunks.get(f["chunk_id"])
        if not hit:
            drop("citation_not_in_context")
            continue
        chunk = hit.chunk
        loc = verifier.locate_quote(f["quote"], chunk["text"])
        if not loc:
            drop("quote_not_verbatim")
            continue
        quote = chunk["text"][loc[0]:loc[1]]
        flags = []
        explanation, changed_e = verifier.strip_ungrounded_sentences(f["explanation"], quote, allowed=threshold_numbers)
        reason, changed_r = verifier.strip_ungrounded_sentences(f["reason"], quote, allowed=threshold_numbers)
        if changed_e or changed_r:
            flags.append("ungrounded_numbers_removed")
        severity = verifier.clamp_severity(f["severity"], rt["default_severity"], cfg["severity_order"])
        if severity != f["severity"]:
            flags.append("severity_clamped")
        confidence = scoring.finding_confidence(f["confidence"], "clause", hit.similarity, cfg)
        findings.append({
            "risk_type_id": rt["id"], "category": category, "title": f["title"], "severity": severity,
            "severity_reason": f.get("severity_reason"), "evidence_type": "clause",
            "clause_ref": chunk["clause_ref"], "page": chunk["page_start"], "chunk_id": chunk["id"],
            "quote": quote, "quote_start": chunk["start_offset"] + loc[0],
            "quote_end": chunk["start_offset"] + loc[1],
            "explanation": explanation or rt["template"]["explanation"],
            "reason": reason or rt["template"]["reason"],
            "suggested_improvement": f["suggested_improvement"], "confidence": confidence,
            "flags": flags,
        })

    # Absence: keyword scan AND retrieval similarity AND the model's check must all agree.
    llm_checks = {c["risk_type_id"]: c for c in raw.get("absence_checks", [])}
    for rt in types:
        if rt["mode"] != "absence" or not rt.get("keywords"):
            continue
        if retrieval.keyword_scan(contract["full_text"], rt["keywords"]):
            continue
        best_sim = max(
            (h.similarity for h in retrieval.multi_search([cid], rt["queries"], top_k=3)), default=0.0
        )
        if best_sim >= cfg["absence_similarity_threshold"]:
            continue
        check = llm_checks.get(rt["id"])
        if is_enabled():
            if check is None or check.get("addressed"):
                continue
            llm_conf = check.get("confidence", 0.8)
        else:
            llm_conf = 0.8
        findings.append({
            "risk_type_id": rt["id"], "category": category, "title": rt["template"]["title"],
            "severity": rt["default_severity"], "severity_reason": "Default severity for a missing clause.",
            "evidence_type": "absence", "clause_ref": None, "page": None, "chunk_id": None, "quote": None,
            "quote_start": None, "quote_end": None, "explanation": rt["template"]["explanation"],
            "reason": rt["template"]["reason"], "suggested_improvement": rt["template"]["improvement"],
            "confidence": scoring.finding_confidence(llm_conf, "absence", 0.0, cfg),
            "search_queries": rt["queries"] + [k.replace("\\b", "") for k in rt["keywords"]],
            "flags": [],
        })

    # Deduplicate: one finding per (risk type, chunk); at most 3 per risk type.
    best: dict[tuple, dict] = {}
    for f in findings:
        key = (f["risk_type_id"], f["chunk_id"])
        if key not in best or f["confidence"] > best[key]["confidence"]:
            best[key] = f
    per_type: dict[str, int] = {}
    deduped = []
    for f in sorted(best.values(), key=lambda x: -x["confidence"]):
        per_type[f["risk_type_id"]] = per_type.get(f["risk_type_id"], 0) + 1
        if per_type[f["risk_type_id"]] <= 3:
            deduped.append(f)
    if drops:
        log.info("risk %s/%s verifier drops: %s", cid, category, drops)
    return {"findings": deduped, "prompt_ref": prompt_ref, "drops": drops}


def risk_analyzer(contract_id: str, report_id: str) -> dict:
    """Module 1 pipeline. Runs as a background job; writes progress and the final report."""
    settings = get_settings()
    cfg = settings.risk_config
    taxonomy = settings.risk_taxonomy
    started = time.perf_counter()
    contract = contracts.get_with_text(contract_id)
    db.update("risk_reports", report_id, {"status": "running", "mode": mode(), "model": _model_label()})
    if not contract or contract["status"] != "indexed":
        db.update("risk_reports", report_id, {"status": "failed", "error": "Contract is not indexed."})
        return {}
    chunk_list = contracts.chunks_for(contract_id)

    applicable, not_applicable = {}, []
    for rt in taxonomy["risk_types"]:
        if _applicable(rt, contract):
            applicable.setdefault(rt["category"], []).append(rt)
        else:
            not_applicable.append({"risk_type_id": rt["id"], "name": rt["name"], "category": rt["category"]})

    done, failed, prompt_refs, all_findings, drops_total = [], [], set(), [], {}

    def run(category: str):
        try:
            return category, _analyse_category(category, applicable.get(category, []), contract, chunk_list), None
        except Exception as exc:  # one failed category gives a partial report, not a lost one
            log.exception("Risk category %s failed", category)
            return category, None, str(exc)

    workers = 5 if is_enabled() else 1
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for category, result, error in pool.map(run, taxonomy["categories"]):
            if error:
                failed.append({"category": category, "error": error})
            else:
                all_findings.extend(result["findings"])
                if result["prompt_ref"]:
                    prompt_refs.add(result["prompt_ref"])
                for k, v in result["drops"].items():
                    drops_total[k] = drops_total.get(k, 0) + v
            done.append(category)
            db.update("risk_reports", report_id, {"progress_json": {"done": done, "total": len(taxonomy["categories"]),
                                                                    "drops": drops_total}})

    rows = []
    for f in all_findings:
        rows.append({
            "id": db.new_id("rf"), "report_id": report_id, "contract_id": contract_id,
            "risk_type_id": f["risk_type_id"], "category": f["category"], "title": f["title"],
            "severity": f["severity"], "severity_reason": f.get("severity_reason"),
            "evidence_type": f["evidence_type"], "clause_ref": f["clause_ref"], "page": f["page"],
            "chunk_id": f["chunk_id"], "quote": f["quote"], "quote_start": f["quote_start"],
            "quote_end": f["quote_end"], "explanation": f["explanation"], "reason": f["reason"],
            "suggested_improvement": f["suggested_improvement"], "confidence": f["confidence"],
            "counted": int(f["confidence"] >= cfg["min_confidence"]), "status": "open",
            "search_queries_json": f.get("search_queries"), "flags_json": f.get("flags") or [],
        })
    db.insert_many("risk_findings", rows)

    score = scoring.score_report(rows, cfg)
    items = [
        {"id": r["id"], "risk_type_id": r["risk_type_id"], "severity": r["severity"], "title": r["title"],
         "reason": r["reason"], "page": r["page"], "clause": r["clause_ref"],
         "suggested_improvement": r["suggested_improvement"]}
        for r in rows if r["counted"]
    ]
    summary = {"overall_assessment": "", "major_points": [], "recommendations": []}
    try:
        summary = generate_executive_summary(
            "risk report", [contract["name"]], {"score": score["score"], "level": score["level"]}, items
        )
        if summary.get("prompt_ref"):
            prompt_refs.add(summary["prompt_ref"])
    except Exception as exc:
        log.exception("Executive summary failed for %s", report_id)
        failed.append({"category": "Executive summary", "error": str(exc)})

    status = "failed" if len(failed) >= len(taxonomy["categories"]) else ("partial" if failed else "complete")
    db.update("risk_reports", report_id, {
        "status": status,
        "overall_score": score["score"],
        "overall_level": score["level"],
        "counts_json": {"by_severity": score["by_severity"], "by_category": score["by_category"],
                        "counted": score["counted"], "needs_review": score["needs_review"]},
        "not_applicable_json": not_applicable,
        "failed_categories_json": failed,
        "executive_summary": summary["overall_assessment"],
        "major_points_json": summary["major_points"],
        "recommendations_json": summary["recommendations"],
        "checks_run": sum(len(v) for v in applicable.values()),
        "prompt_versions_json": sorted(prompt_refs),
        "duration_ms": int((time.perf_counter() - started) * 1000),
        "error": "; ".join(f"{f['category']}: {f['error']}" for f in failed) or None,
    })
    return get_risk_report(report_id)


def rescore_report(report_id: str) -> None:
    """Recompute scores after a reviewer dismisses or restores a finding."""
    cfg = get_settings().risk_config
    findings = db.query("SELECT * FROM risk_findings WHERE report_id = ?", (report_id,))
    score = scoring.score_report(findings, cfg)
    db.update("risk_reports", report_id, {
        "overall_score": score["score"], "overall_level": score["level"],
        "counts_json": {"by_severity": score["by_severity"], "by_category": score["by_category"],
                        "counted": score["counted"], "needs_review": score["needs_review"]},
    })


def get_risk_report(report_id: str) -> dict | None:
    report = db.query_one("SELECT * FROM risk_reports WHERE id = ?", (report_id,))
    if not report:
        return None
    report["findings"] = db.query(
        "SELECT * FROM risk_findings WHERE report_id = ? ORDER BY "
        "CASE severity WHEN 'Critical' THEN 0 WHEN 'High' THEN 1 WHEN 'Medium' THEN 2 ELSE 3 END, "
        "confidence DESC",
        (report_id,),
    )
    report["contract"] = contracts.get(report["contract_id"])
    return report


# ---------------------------------------------------------------------------
# Executive summary
# ---------------------------------------------------------------------------

def generate_executive_summary(mode_name: str, document_names: list[str], scores: dict, items: list[dict]) -> dict:
    """Summary + recommendations over VERIFIED items. Recommendations must reference item ids."""
    prompt_ref = None
    if is_enabled():
        result = get_client().complete_json(
            "summary.executive",
            {"mode": mode_name, "document_names": ", ".join(document_names), "scores": scores, "items": items},
        )
        data, prompt_ref = result.data, result.prompt_ref
    else:
        data = heuristics.executive(mode_name, scores, items)
    ids = {i["id"] for i in items}
    evidence = json.dumps(items) + json.dumps(scores)
    allowed = {"100"}  # scores are written "40/100"

    def clean(entries: list[dict]) -> list[dict]:
        out = []
        for e in entries:
            refs = [i for i in e.get("item_ids", []) if i in ids]
            text, _ = verifier.strip_ungrounded_sentences(e["text"], evidence, allowed=allowed)
            if refs and text:
                out.append({"text": text, "item_ids": refs})
        return out

    assessment, _ = verifier.strip_ungrounded_sentences(data["overall_assessment"], evidence, allowed=allowed)
    return {
        "overall_assessment": assessment,
        "major_points": clean(data.get("major_points", [])),
        "recommendations": clean(data.get("recommendations", [])) if items else [],
        "prompt_ref": prompt_ref,
    }


# ---------------------------------------------------------------------------
# Module 2 - Contract comparison
# ---------------------------------------------------------------------------

def create_comparison(contract_a_id: str, contract_b_id: str, perspective: str) -> str:
    comparison_id = db.new_id("cmp")
    db.insert("comparisons", {
        "id": comparison_id, "contract_a_id": contract_a_id, "contract_b_id": contract_b_id,
        "perspective": perspective, "status": "queued",
        "progress_json": {"done": 0, "total": len(get_settings().comparison_categories["categories"])},
        "mode": mode(), "model": _model_label(), "created_at": db.now_iso(),
    })
    return comparison_id


def _addressed(hits: list[retrieval.Hit], keywords: list[str]) -> list[retrieval.Hit]:
    """Keep hits whose text actually mentions the topic (the category's keywords)."""
    return [h for h in hits if any(re.search(k, h.chunk["text"], re.IGNORECASE) for k in keywords)]


def _norm(text: str) -> str:
    text = re.sub(r"^\s*(?:section|clause|article)?\s*\d+(?:\.\d+)*[.)]?\s*", "", text, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", text).strip().lower()


def _counterparts(hits: list, own: retrieval._Index, other: retrieval._Index, threshold: float) -> list[dict]:
    """For each hit, its closest clause anywhere in the other contract and whether it is identical."""
    position = {c["id"]: i for i, c in enumerate(own.chunks)}
    out = []
    for h in hits:
        if not other.chunks:
            out.append({"chunk": h.chunk, "match": None, "similarity": 0.0, "identical": False})
            continue
        sims = other.matrix @ own.matrix[position[h.chunk["id"]]]
        j = int(np.argmax(sims))
        match, sim = other.chunks[j], float(sims[j])
        identical = _norm(match["text"]) == _norm(h.chunk["text"])
        out.append({"chunk": h.chunk, "match": match if sim >= threshold else None,
                    "similarity": sim, "identical": identical})
    return out


def _alignment_anchors(chunks_a: list[dict], chunks_b: list[dict], threshold: float) -> list[dict]:
    """Greedy one-to-one clause alignment used for synchronised scrolling in the viewer."""
    if not chunks_a or not chunks_b:
        return []
    va = embeddings.embed_texts([c["text"] for c in chunks_a])
    vb = embeddings.embed_texts([c["text"] for c in chunks_b])
    sim = va @ vb.T
    pairs = sorted(((float(sim[i, j]), i, j) for i in range(sim.shape[0]) for j in range(sim.shape[1])
                    if sim[i, j] >= threshold), reverse=True)
    used_a, used_b, anchors = set(), set(), []
    for s, i, j in pairs:
        if i in used_a or j in used_b:
            continue
        used_a.add(i)
        used_b.add(j)
        anchors.append({"a": chunks_a[i]["id"], "b": chunks_b[j]["id"], "similarity": round(s, 3)})
    order_a = {c["id"]: c["idx"] for c in chunks_a}
    anchors.sort(key=lambda x: order_a[x["a"]])
    return anchors


def _verify_side(citations: list[dict], allowed: dict[str, dict]) -> tuple[dict | None, list[str]]:
    """Return the first verified citation as {chunk, quote, start, end} plus flags."""
    for c in citations:
        chunk = allowed.get(c["chunk_id"])
        if not chunk:
            continue
        loc = verifier.locate_quote(c["quote"], chunk["text"])
        if loc:
            return {"chunk": chunk, "quote": chunk["text"][loc[0]:loc[1]],
                    "start": chunk["start_offset"] + loc[0], "end": chunk["start_offset"] + loc[1]}, []
    return None, (["citation_unverified"] if citations else [])


def _value_in_quote(value: str, side: dict | None) -> bool:
    """A value like "90 days" is grounded if its numbers (or, with no numbers, its words) are in the quote."""
    if not side:
        return False
    numbers = verifier.numbers_in(value)
    if numbers:
        return numbers <= verifier.numbers_in(side["quote"])
    return value.lower() in side["quote"].lower()


def _heuristic_compare(category: dict, side_a: list[dict], side_b: list[dict],
                       allowed_a: dict, allowed_b: dict) -> dict:
    """Offline comparison: pick the clause pair that actually changed and diff its key values."""
    if not side_a:
        return heuristics.compare(category, None, side_b[0]["chunk"], 0.0)
    if not side_b:
        return heuristics.compare(category, side_a[0]["chunk"], None, 0.0)
    changed = [m for m in side_a if m["match"] and not m["identical"]]
    if changed:
        # Prefer a pair whose key values differ; otherwise the least similar pair.
        def differs(m):
            return heuristics.signature(m["chunk"]["text"]) != heuristics.signature(m["match"]["text"])
        pick = next((m for m in changed if differs(m)), min(changed, key=lambda m: m["similarity"]))
        allowed_b.setdefault(pick["match"]["id"], pick["match"])
        return heuristics.compare(category, pick["chunk"], pick["match"], pick["similarity"])
    new_in_b = [m for m in side_b if m["match"] is None]
    if new_in_b:
        result = heuristics.compare(category, None, new_in_b[0]["chunk"], 0.0)
        result.update(status="modified", headline=f"New {category['name'].lower()} provision in B",
                      change_summary=f"Contract B adds a {category['name'].lower()} provision with no counterpart in A.",
                      citations_a=[{"chunk_id": side_a[0]["chunk"]["id"], "quote": side_a[0]["chunk"]["text"][:300]}])
        return result
    gone_from_b = [m for m in side_a if m["match"] is None]
    if gone_from_b:
        result = heuristics.compare(category, gone_from_b[0]["chunk"], None, 0.0)
        result.update(status="modified", headline=f"{category['name']} provision removed in B",
                      change_summary=f"A {category['name'].lower()} provision in contract A has no counterpart in B.",
                      citations_b=[{"chunk_id": side_b[0]["chunk"]["id"], "quote": side_b[0]["chunk"]["text"][:300]}])
        return result
    return heuristics.compare(category, side_a[0]["chunk"], side_a[0]["match"] or side_b[0]["chunk"], 1.0)


def _compare_category(category: dict, contract_a: dict, contract_b: dict) -> dict:
    cfg = get_settings().comparison_categories
    hits_a = _addressed(retrieval.multi_search([contract_a["id"]], category["queries"], top_k=cfg["top_k"] + 2),
                        category["keywords"])[: cfg["top_k"]]
    hits_b = _addressed(retrieval.multi_search([contract_b["id"]], category["queries"], top_k=cfg["top_k"] + 2),
                        category["keywords"])[: cfg["top_k"]]
    index_a, index_b = retrieval.index_for(contract_a["id"]), retrieval.index_for(contract_b["id"])
    threshold = cfg["alignment_threshold"]
    side_a_matches = _counterparts(hits_a, index_a, index_b, threshold)
    side_b_matches = _counterparts(hits_b, index_b, index_a, threshold)
    allowed_a = {h.chunk["id"]: h.chunk for h in hits_a}
    allowed_b = {h.chunk["id"]: h.chunk for h in hits_b}
    prompt_ref = None

    def same(m: dict) -> bool:
        return m["identical"] or m["similarity"] >= cfg["unchanged_similarity"]

    # Fast path: every relevant clause on both sides has an identical counterpart (no LLM call).
    fast_path = hits_a and hits_b and all(same(m) for m in side_a_matches + side_b_matches)
    if fast_path:
        first = side_a_matches[0]
        counterpart = first["match"] or hits_b[0].chunk
        allowed_b.setdefault(counterpart["id"], counterpart)
        raw = {"status": "unchanged", "headline": f"No change to {category['name'].lower()}", "value_a": None,
               "value_b": None, "change_summary": "The clauses are identical apart from formatting or numbering.",
               "citations_a": [{"chunk_id": first["chunk"]["id"], "quote": first["chunk"]["text"][:300]}],
               "citations_b": [{"chunk_id": counterpart["id"], "quote": counterpart["text"][:300]}], "confidence": 0.95}
    elif not hits_a and not hits_b:
        raw = heuristics.compare(category, None, None, 0.0)
    elif is_enabled():
        result = get_client().complete_json(
            "contract.compare",
            {
                "category": category["name"], "value_spec": category["value_spec"],
                "contract_a_name": contract_a["name"], "contract_b_name": contract_b["name"],
                "excerpts_a": retrieval.format_excerpts(hits_a), "excerpts_b": retrieval.format_excerpts(hits_b),
            },
        )
        raw, prompt_ref = result.data, result.prompt_ref
    else:
        raw = _heuristic_compare(category, side_a_matches, side_b_matches, allowed_a, allowed_b)

    side_a, flags_a = _verify_side(raw.get("citations_a", []), allowed_a)
    side_b, flags_b = _verify_side(raw.get("citations_b", []), allowed_b)
    flags = flags_a + flags_b
    status = raw["status"]
    # Classification must be consistent with the verified evidence.
    if status == "added" and side_a:
        side_a = None
    if status == "removed" and side_b:
        side_b = None
    if status in {"modified", "unchanged"} and (side_a is None) != (side_b is None):
        flags.append("one_side_unverified")
    value_a, value_b = raw.get("value_a"), raw.get("value_b")
    if (value_a and not _value_in_quote(value_a, side_a)) or (value_b and not _value_in_quote(value_b, side_b)):
        value_a, value_b = None, None
        flags.append("values_not_in_quotes")
    evidence = " ".join(s["quote"] for s in (side_a, side_b) if s)
    summary, changed = verifier.strip_ungrounded_sentences(raw["change_summary"], evidence)
    headline, changed_h = verifier.strip_ungrounded_sentences(raw["headline"], evidence)
    if changed or changed_h:
        flags.append("ungrounded_numbers_removed")
    return {
        "category_id": category["id"], "category": category["name"], "status": status,
        "headline": headline or category["name"], "value_a": value_a, "value_b": value_b,
        "clause_ref_a": side_a["chunk"]["clause_ref"] if side_a else None,
        "page_a": side_a["chunk"]["page_start"] if side_a else None,
        "chunk_id_a": side_a["chunk"]["id"] if side_a else None,
        "quote_a": side_a["quote"] if side_a else None,
        "quote_a_start": side_a["start"] if side_a else None, "quote_a_end": side_a["end"] if side_a else None,
        "clause_ref_b": side_b["chunk"]["clause_ref"] if side_b else None,
        "page_b": side_b["chunk"]["page_start"] if side_b else None,
        "chunk_id_b": side_b["chunk"]["id"] if side_b else None,
        "quote_b": side_b["quote"] if side_b else None,
        "quote_b_start": side_b["start"] if side_b else None, "quote_b_end": side_b["end"] if side_b else None,
        "change_summary": summary, "confidence": round(float(raw.get("confidence", 0.5)), 2),
        "flags": sorted(set(flags)), "prompt_ref": prompt_ref,
    }


def _ensure_risk_report(contract_id: str) -> dict | None:
    report = contracts.latest_report(contract_id, complete_only=True)
    if report:
        return report
    running = contracts.latest_report(contract_id)
    if running and running["status"] in {"queued", "running"}:
        for _ in range(240):  # wait for the in-flight analysis (up to 2 minutes)
            time.sleep(0.5)
            running = contracts.latest_report(contract_id)
            if running["status"] not in {"queued", "running"}:
                break
        return contracts.latest_report(contract_id, complete_only=True)
    report_id = create_risk_report(contract_id)
    risk_analyzer(contract_id, report_id)
    return contracts.latest_report(contract_id, complete_only=True)


def _risk_delta(contract_a_id: str, contract_b_id: str) -> dict | None:
    ra, rb = _ensure_risk_report(contract_a_id), _ensure_risk_report(contract_b_id)
    if not ra or not rb:
        return None
    fa = db.query("SELECT risk_type_id, title, severity FROM risk_findings WHERE report_id = ? AND counted = 1 "
                  "AND status != 'dismissed'", (ra["id"],))
    fb = db.query("SELECT risk_type_id, title, severity FROM risk_findings WHERE report_id = ? AND counted = 1 "
                  "AND status != 'dismissed'", (rb["id"],))
    types_a, types_b = {f["risk_type_id"]: f for f in fa}, {f["risk_type_id"]: f for f in fb}
    return {
        "score_a": ra["overall_score"], "level_a": ra["overall_level"], "report_a": ra["id"],
        "score_b": rb["overall_score"], "level_b": rb["overall_level"], "report_b": rb["id"],
        "new_findings": [types_b[t] for t in types_b if t not in types_a],
        "resolved_findings": [types_a[t] for t in types_a if t not in types_b],
    }


def compare_contracts(comparison_id: str) -> dict:
    """Module 2 pipeline. Runs as a background job."""
    settings = get_settings()
    cfg = settings.comparison_categories
    started = time.perf_counter()
    cmp = db.query_one("SELECT * FROM comparisons WHERE id = ?", (comparison_id,))
    contract_a = contracts.get_with_text(cmp["contract_a_id"])
    contract_b = contracts.get_with_text(cmp["contract_b_id"])
    db.update("comparisons", comparison_id, {"status": "running", "mode": mode(), "model": _model_label()})
    try:
        categories = cfg["categories"]
        results: list[dict | None] = [None] * len(categories)
        done = 0

        def run(i_cat):
            i, cat = i_cat
            return i, _compare_category(cat, contract_a, contract_b)

        with ThreadPoolExecutor(max_workers=5 if is_enabled() else 1) as pool:
            for i, change in pool.map(run, enumerate(categories)):
                results[i] = change
                done += 1
                db.update("comparisons", comparison_id, {"progress_json": {"done": done, "total": len(categories)}})

        prompt_refs = {r["prompt_ref"] for r in results if r and r["prompt_ref"]}
        rows = []
        for i, r in enumerate(results):
            row = {k: v for k, v in r.items() if k not in {"prompt_ref", "flags"}}
            rows.append({**row, "id": db.new_id("chg"), "comparison_id": comparison_id, "sort": i,
                         "flags_json": r["flags"]})

        # Business impact for every non-unchanged change (one batched call).
        changed = [r for r in rows if r["status"] != "unchanged"]
        if changed:
            payload = [
                {"id": r["id"], "category": r["category"], "status": r["status"], "value_a": r["value_a"],
                 "value_b": r["value_b"], "change_summary": r["change_summary"], "quote_a": r["quote_a"],
                 "quote_b": r["quote_b"]}
                for r in changed
            ]
            if is_enabled():
                result = get_client().complete_json(
                    "impact.business",
                    {"perspective": cmp["perspective"], "party_a_label": "the first party named in the contract",
                     "party_b_label": "the second party named in the contract",
                     "contract_type": contract_b["contract_type"] or "other", "changes": payload},
                )
                impacts = result.data
                prompt_refs.add(result.prompt_ref)
            else:
                impacts = heuristics.impacts(payload)
            by_id = {r["id"]: r for r in rows}
            for imp in impacts["impacts"]:
                row = by_id.get(imp["change_id"])
                if not row:
                    continue
                evidence = " ".join(filter(None, [row["quote_a"], row["quote_b"], row["value_a"], row["value_b"]]))
                text, _ = verifier.strip_ungrounded_sentences(imp["impact"], evidence)
                row.update({"business_impact": text, "favours": imp["favours"],
                            "risk_direction": imp["risk_direction"],
                            "impact_confidence": round(float(imp["confidence"]), 2)})
        db.insert_many("comparison_changes", rows)

        counts = {s: sum(1 for r in rows if r["status"] == s) for s in ("modified", "added", "removed", "unchanged")}
        risk_delta = None
        try:
            risk_delta = _risk_delta(contract_a["id"], contract_b["id"])
        except Exception:
            log.exception("Risk delta failed for %s", comparison_id)
        items = [{"id": r["id"], "category": r["category"], "status": r["status"], "headline": r["headline"],
                  "value_a": r["value_a"], "value_b": r["value_b"], "impact": r.get("business_impact")}
                 for r in rows]
        scores = {**counts, "changed": len(changed), "total": len(rows)}
        if risk_delta:
            scores.update({"risk_score_a": risk_delta["score_a"], "risk_score_b": risk_delta["score_b"]})
        summary = generate_executive_summary("comparison", [contract_a["name"], contract_b["name"]], scores, items)
        if summary.get("prompt_ref"):
            prompt_refs.add(summary["prompt_ref"])
        anchors = _alignment_anchors(contracts.chunks_for(contract_a["id"]), contracts.chunks_for(contract_b["id"]),
                                     cfg["alignment_threshold"])
        db.update("comparisons", comparison_id, {
            "status": "complete", "counts_json": counts, "executive_summary": summary["overall_assessment"],
            "major_points_json": summary["major_points"], "recommendations_json": summary["recommendations"],
            "risk_delta_json": risk_delta, "anchors_json": anchors, "prompt_versions_json": sorted(prompt_refs),
            "duration_ms": int((time.perf_counter() - started) * 1000),
        })
    except Exception as exc:
        log.exception("Comparison %s failed", comparison_id)
        db.update("comparisons", comparison_id, {"status": "failed", "error": str(exc)})
    return get_comparison(comparison_id)


def get_comparison(comparison_id: str) -> dict | None:
    cmp = db.query_one("SELECT * FROM comparisons WHERE id = ?", (comparison_id,))
    if not cmp:
        return None
    cmp["changes"] = db.query("SELECT * FROM comparison_changes WHERE comparison_id = ? ORDER BY "
                              "CASE status WHEN 'modified' THEN 0 WHEN 'added' THEN 1 WHEN 'removed' THEN 2 ELSE 3 END, sort",
                              (comparison_id,))
    cmp["contract_a"] = contracts.get(cmp["contract_a_id"])
    cmp["contract_b"] = contracts.get(cmp["contract_b_id"])
    return cmp


def prompt_versions() -> dict:
    return {pid: p.ref for pid, p in loader.registry().items() if p.schema is not None}
