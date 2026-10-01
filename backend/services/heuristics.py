"""Offline mode: deterministic stand-ins for each LLM call, used when no LLM is configured.

They return the SAME shapes as the LLM prompts' output schemas, so the verifier, scoring and
storage paths are identical in both modes. Results are labelled mode="offline" in the UI.
"""

from __future__ import annotations

import re

from . import embeddings
from .retrieval import Hit

# A sentence runs to "." or ";" - but not the dot inside a clause number such as "5.2".
_SENTENCE = re.compile(r"(?:[^.;\n]|[.;](?=\d))+(?:[.;]|$)", re.MULTILINE)
_DURATION = re.compile(
    r"\b(\d+)\s*(?:\([a-z\s-]+\)\s*)?(?:calendar |business |working )?(days?|months?|years?|weeks?)\b",
    re.IGNORECASE,
)
_PERCENT = re.compile(r"\b(\d+(?:\.\d+)?)\s?(?:%|per ?cent)", re.IGNORECASE)
_MONEY = re.compile(r"(?:USD|INR|EUR|GBP|Rs\.?|\$|€|£)\s?\d[\d,]*(?:\.\d+)?", re.IGNORECASE)
_FREQUENCY = re.compile(r"\b(monthly|quarterly|annually|annual|yearly|weekly|semi-annually|bi-annually)\b", re.IGNORECASE)
QUOTE_MAX = 300


def _quote_around(text: str, start: int, end: int) -> str:
    """A verbatim excerpt containing [start, end), cut at sentence or word boundaries."""
    s = max(text.rfind(". ", 0, start), text.rfind("\n", 0, start))
    s = s + 1 if s >= 0 else 0
    e_candidates = [i for i in (text.find(". ", end), text.find("\n", end)) if i >= 0]
    e = min(e_candidates) + 1 if e_candidates else len(text)
    if e - s > QUOTE_MAX:
        s = max(s, start - 100)
        e = min(e, s + QUOTE_MAX)
        # snap to word boundaries
        while s > 0 and s < start and not text[s - 1].isspace():
            s += 1
        while e < len(text) and e > end and not text[e - 1].isspace():
            e -= 1
    return text[s:e].strip()


def _durations_in_days(text: str) -> list[int]:
    days = []
    for m in _DURATION.finditer(text):
        n, unit = int(m.group(1)), m.group(2).lower()
        factor = 1 if unit.startswith("day") else 7 if unit.startswith("week") else 30 if unit.startswith("month") else 365
        days.append(n * factor)
    return days


def risk_category(
    risk_types: list[dict], hits: list[Hit], chunks: list[dict], thresholds: dict
) -> dict:
    """Pattern-based equivalent of the risk.analyze prompt."""
    findings, seen = [], set()
    for rt in risk_types:
        patterns = rt.get("patterns") or []
        if not patterns:
            continue
        per_type = 0
        for chunk in chunks:
            if per_type >= 3:
                break
            text = chunk["text"]
            for pattern in patterns:
                m = re.search(pattern, text, re.IGNORECASE)
                if not m:
                    continue
                quote = _quote_around(text, m.start(), m.end())
                severity = rt["default_severity"]
                severity_reason = "Default severity for this risk type."
                confidence = 0.75 if rt["mode"] == "presence" else 0.65
                check = rt.get("numeric_check")
                if check:
                    limit = thresholds[check["threshold_key"]]
                    if check["type"] == "percent_gte":
                        pcts = [float(p) for p in _PERCENT.findall(quote)]
                        if pcts and max(pcts) < limit:
                            continue
                        if not pcts:
                            confidence = 0.55
                    elif check["type"] == "notice_days_gt":
                        days = _durations_in_days(m.group(0))
                        if not days or max(days) <= limit:
                            continue
                        severity_reason = "The notice period exceeds the configured threshold."
                key = (rt["id"], chunk["id"])
                if key in seen:
                    break
                seen.add(key)
                findings.append({
                    "risk_type_id": rt["id"],
                    "title": rt["template"]["title"],
                    "severity": severity,
                    "severity_reason": severity_reason,
                    "evidence_type": "clause",
                    "chunk_id": chunk["id"],
                    "quote": quote,
                    "explanation": rt["template"]["explanation"],
                    "reason": rt["template"]["reason"],
                    "suggested_improvement": rt["template"]["improvement"],
                    "confidence": confidence,
                })
                per_type += 1
                break
    return {"findings": findings, "absence_checks": []}


def answer(question: str, hits: list[Hit]) -> dict:
    """Extractive answer: the best-matching sentences from the top passages."""
    if not hits or hits[0].score < 0.12:
        return {"found": False, "answer": "I couldn't find this in the contract.", "citations": [], "confidence": 0.0}
    q_vec = embeddings.embed_text(question)
    citations, lines = [], []
    for hit in hits[:3]:
        if hit.score < 0.08:
            continue
        sentences = [m.group(0).strip() for m in _SENTENCE.finditer(hit.chunk["text"]) if len(m.group(0).strip()) > 20]
        if not sentences:
            continue
        best = max(sentences, key=lambda s: float(embeddings.embed_text(s) @ q_vec))
        quote = best[:QUOTE_MAX]
        citations.append({"chunk_id": hit.chunk["id"], "quote": quote})
        lines.append(f"“{quote}” [{hit.chunk['id']}]")
    if not citations:
        return {"found": False, "answer": "I couldn't find this in the contract.", "citations": [], "confidence": 0.0}
    text = "Offline mode shows the most relevant passages rather than a written answer:\n\n" + "\n\n".join(lines)
    return {"found": True, "answer": text, "citations": citations, "confidence": round(min(0.9, hits[0].score + 0.3), 2)}


_PARTIES = re.compile(
    r"between\s+(.{3,90}?)\s*\((?:the\s+)?[\"“]?([A-Za-z ]{2,30})[\"”]?\)\s*,?\s*(?:and|&)\s+(.{3,90}?)\s*\((?:the\s+)?[\"“]?([A-Za-z ]{2,30})[\"”]?\)",
    re.IGNORECASE | re.DOTALL,
)


def summary(field_hits: dict[str, list[Hit]], first_chunks: list[dict], contract_type: str, personal_data: bool) -> dict:
    parties = []
    head = " ".join(c["text"] for c in first_chunks)
    m = _PARTIES.search(head)
    if m:
        cid = first_chunks[0]["id"]
        for name, role in ((m.group(1), m.group(2)), (m.group(3), m.group(4))):
            parties.append({"name": " ".join(name.split()).strip(" ,"), "role": role.strip(), "chunk_ids": [cid]})
    fields = {}
    for field, hits in field_hits.items():
        if not hits or hits[0].score < 0.15:
            fields[field] = None
            continue
        chunk = hits[0].chunk
        sentences = [s.group(0).strip() for s in _SENTENCE.finditer(chunk["text"]) if len(s.group(0).strip()) > 25]
        text = sentences[0] if sentences else chunk["text"][:240]
        fields[field] = {"text": text[:400], "chunk_ids": [chunk["id"]]}
    return {
        "contract_type": contract_type,
        "involves_personal_data": personal_data,
        "parties": parties,
        "fields": fields,
    }


def explain(chunk: dict, matched_types: list[dict]) -> dict:
    watch = [f"{rt['name']}: {rt['template']['reason']}" for rt in matched_types]
    return {
        "plain_language": "Offline mode can't write a plain-language explanation. Add an API key to enable it."
        + (" The risk patterns this clause matches are listed below." if watch else ""),
        "obligations": [],
        "key_terms": [],
        "watch_outs": watch,
        "citations": [{"chunk_id": chunk["id"], "quote": chunk["text"][:QUOTE_MAX].strip()}],
    }


def signature(text: str) -> dict[str, str]:
    """Key values in a clause: durations, percentages, money, frequencies -> original spelling."""
    sig: dict[str, str] = {}
    for rx in (_DURATION, _PERCENT, _MONEY, _FREQUENCY):
        for m in rx.finditer(text):
            sig[re.sub(r"\s+", " ", m.group(0).lower())] = m.group(0)
    return sig


def _sentence_with(text: str, needle: str) -> str:
    pos = text.lower().find(needle.lower())
    if pos < 0:
        return text[:QUOTE_MAX].strip()
    return _quote_around(text, pos, pos + len(needle))


def compare(category: dict, chunk_a: dict | None, chunk_b: dict | None, similarity: float) -> dict:
    """Heuristic equivalent of the contract.compare prompt for one category."""
    name = category["name"]
    if chunk_a is None and chunk_b is None:
        return {"status": "unchanged", "headline": f"{name} not addressed in either contract", "value_a": None, "value_b": None,
                "change_summary": "Neither contract addresses this topic.", "citations_a": [], "citations_b": [], "confidence": 0.6}
    if chunk_a is None:
        quote = chunk_b["text"][:QUOTE_MAX].strip()
        return {"status": "added", "headline": f"{name} clause added", "value_a": None, "value_b": None,
                "change_summary": f"Contract B addresses {name.lower()}; contract A does not.",
                "citations_a": [], "citations_b": [{"chunk_id": chunk_b["id"], "quote": quote}], "confidence": 0.65}
    if chunk_b is None:
        quote = chunk_a["text"][:QUOTE_MAX].strip()
        return {"status": "removed", "headline": f"{name} clause removed", "value_a": None, "value_b": None,
                "change_summary": f"Contract A addresses {name.lower()}; contract B does not.",
                "citations_a": [{"chunk_id": chunk_a["id"], "quote": quote}], "citations_b": [], "confidence": 0.65}
    sig_a, sig_b = signature(chunk_a["text"]), signature(chunk_b["text"])
    only_a = [sig_a[k] for k in sig_a if k not in sig_b]
    only_b = [sig_b[k] for k in sig_b if k not in sig_a]
    if only_a or only_b:
        value_a = only_a[0] if only_a else None
        value_b = only_b[0] if only_b else None
        quote_a = _sentence_with(chunk_a["text"], value_a) if value_a else chunk_a["text"][:QUOTE_MAX].strip()
        quote_b = _sentence_with(chunk_b["text"], value_b) if value_b else chunk_b["text"][:QUOTE_MAX].strip()
        arrow = f"{value_a or '—'} → {value_b or '—'}"
        return {"status": "modified", "headline": f"{name} changed: {arrow}", "value_a": value_a, "value_b": value_b,
                "change_summary": f"The key values in the {name.lower()} clause differ between the two contracts.",
                "citations_a": [{"chunk_id": chunk_a["id"], "quote": quote_a}],
                "citations_b": [{"chunk_id": chunk_b["id"], "quote": quote_b}], "confidence": 0.7}
    if similarity >= 0.85:
        return {"status": "unchanged", "headline": f"No material change to {name.lower()}", "value_a": None, "value_b": None,
                "change_summary": "The clauses are worded almost identically.",
                "citations_a": [{"chunk_id": chunk_a["id"], "quote": chunk_a["text"][:QUOTE_MAX].strip()}],
                "citations_b": [{"chunk_id": chunk_b["id"], "quote": chunk_b["text"][:QUOTE_MAX].strip()}], "confidence": 0.6}
    return {"status": "modified", "headline": f"{name} wording changed", "value_a": None, "value_b": None,
            "change_summary": "The wording differs. Offline mode cannot judge whether the meaning changed; review both clauses.",
            "citations_a": [{"chunk_id": chunk_a["id"], "quote": chunk_a["text"][:QUOTE_MAX].strip()}],
            "citations_b": [{"chunk_id": chunk_b["id"], "quote": chunk_b["text"][:QUOTE_MAX].strip()}], "confidence": 0.45}


def impacts(changes: list[dict]) -> dict:
    out = []
    for c in changes:
        if c["status"] == "added":
            text = f"A new {c['category'].lower()} clause creates rights or obligations that did not exist before. Check which party it binds."
        elif c["status"] == "removed":
            text = f"Removing the {c['category'].lower()} clause drops protections or obligations that existed in contract A."
        else:
            text = f"The {c['category'].lower()} provisions changed. Check how the new wording affects cost, flexibility and exposure."
        out.append({"change_id": c["id"], "impact": text, "favours": "unclear", "risk_direction": "neutral", "confidence": 0.4})
    return {"impacts": out}


def executive(mode: str, scores: dict, items: list[dict]) -> dict:
    if mode == "risk report":
        if not items:
            return {"overall_assessment": "No risks were found in the checks performed.", "major_points": [], "recommendations": []}
        order = {"Critical": 0, "High": 1, "Medium": 2, "Low": 3}
        ranked = sorted(items, key=lambda i: order.get(i["severity"], 9))
        top = ", ".join(i["title"].lower() for i in ranked[:3])
        assessment = f"Overall contract risk is {scores['level']} ({scores['score']}/100). The most important issues are: {top}."
        points = [{"text": f"{i['severity']}: {i['title']}", "item_ids": [i["id"]]} for i in ranked[:6]]
        recs, used = [], set()
        for i in ranked:
            if i["risk_type_id"] in used or len(recs) >= 6:
                continue
            used.add(i["risk_type_id"])
            ids = [j["id"] for j in ranked if j["risk_type_id"] == i["risk_type_id"]]
            recs.append({"text": i["suggested_improvement"], "item_ids": ids})
        return {"overall_assessment": assessment, "major_points": points, "recommendations": recs}
    changed = [i for i in items if i["status"] != "unchanged"]
    if not changed:
        return {"overall_assessment": "No material differences were found between the two contracts.", "major_points": [], "recommendations": []}
    names = ", ".join(i["category"].lower() for i in changed[:4])
    assessment = f"{len(changed)} of {len(items)} topics differ between the contracts, including {names}."
    points = [{"text": i["headline"], "item_ids": [i["id"]]} for i in changed[:6]]
    recs = [{"text": f"Review the {i['category'].lower()} change before signing.", "item_ids": [i["id"]]} for i in changed[:6]]
    return {"overall_assessment": assessment, "major_points": points, "recommendations": recs}
