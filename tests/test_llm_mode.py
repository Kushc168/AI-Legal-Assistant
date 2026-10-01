"""LLM-mode pipeline with a fake Claude client.

Exercises every prompt (rendering with all variables, schema-valid responses) and checks that the
verifier removes fabricated evidence before anything is stored.
"""

import re
import time

import pytest

from backend.prompts import loader
from backend.services import ai_service, contracts, verifier
from backend.services.llm_client import LLMResult

MSA = (
    "MASTER SERVICES AGREEMENT\n\nThis Agreement is between Alpha Ltd (\"Customer\") and Beta Ltd (\"Supplier\").\n\n"
    "4. PAYMENT\n\n4.1 Invoices are payable within 30 days.\n\n"
    "9. LIABILITY\n\n9.3 The Customer's liability under this Agreement shall be unlimited.\n\n"
    "11. TERMINATION\n\n11.1 Either party may terminate on 60 days' written notice.\n"
)
MSA_B = MSA.replace("60 days'", "90 days'").replace("within 30 days", "within 45 days")
EXCERPT = re.compile(r'<excerpt chunk_id="([^"]+)"[^>]*>\n(.*?)\n</excerpt>', re.DOTALL)


def excerpts(text):
    return EXCERPT.findall(text)


class FakeClient:
    model = "fake-claude"

    def __init__(self):
        self.calls = []

    def complete_json(self, prompt_id, variables):
        loader.render(prompt_id, variables)  # raises if a variable is missing
        self.calls.append(prompt_id)
        data = getattr(self, prompt_id.replace(".", "_"))(variables)
        verifier.validate_schema(data, loader.get(prompt_id).schema)
        return LLMResult(data=data, prompt_ref=loader.get(prompt_id).ref, model=self.model,
                         input_tokens=1, output_tokens=1, latency_ms=1)

    def contract_summarize(self, v):
        cid = excerpts(v["excerpts"])[0][0]
        fields = {k: None for k in ["purpose", "effective_date", "term", "payment_terms", "key_obligations",
                                    "termination", "renewal", "liability", "confidentiality", "governing_law"]}
        fields["purpose"] = {"text": "IT services.", "chunk_ids": [cid]}
        fields["liability"] = {"text": "Bogus.", "chunk_ids": ["c_not_provided"]}
        return {"contract_type": "services", "involves_personal_data": False,
                "parties": [{"name": "Alpha Ltd", "role": "Customer", "chunk_ids": [cid]}], "fields": fields}

    def risk_analyze(self, v):
        findings, checks = [], []
        for cid, text in excerpts(v["excerpts"]):
            if "unlimited" in text and v["category"] == "Financial":
                findings.append(self._finding("FIN-01", cid, "The Customer's liability under this Agreement shall be unlimited",
                                              "Critical", "The customer owes up to 5000000 in damages."))
                findings.append(self._finding("FIN-02", cid, "The Customer shall pay a penalty of 50% of fees", "High", "Made up."))
        for rt in v["risk_types"]:
            if rt["mode"] == "absence":
                checks.append({"risk_type_id": rt["id"], "addressed": False, "chunk_id": None, "confidence": 0.95})
        return {"findings": findings, "absence_checks": checks}

    @staticmethod
    def _finding(rid, cid, quote, severity, explanation):
        return {"risk_type_id": rid, "title": "Test finding", "severity": severity, "severity_reason": "test",
                "evidence_type": "clause", "chunk_id": cid, "quote": quote, "explanation": explanation,
                "reason": "Uncapped exposure.", "suggested_improvement": "Add a cap.", "confidence": 0.9}

    def summary_executive(self, v):
        ids = [i["id"] for i in v["items"]]
        return {"overall_assessment": "Risk is driven by liability.",
                "major_points": [{"text": "Liability is uncapped.", "item_ids": ids[:1] or ["x"]}],
                "recommendations": [{"text": "Negotiate a cap.", "item_ids": ids[:1] or ["x"]},
                                    {"text": "Invented action.", "item_ids": ["does-not-exist"]}]}

    def qa_answer(self, v):
        cid, text = excerpts(v["excerpts"])[0]
        return {"found": True, "answer": f"See the clause [{cid}]. It also mentions 777 days.",
                "citations": [{"chunk_id": cid, "quote": text[:60]}, {"chunk_id": cid, "quote": "fabricated words here"}],
                "confidence": 0.8}

    def clause_explain(self, v):
        cid, text = excerpts(v["clause_excerpt"])[0]
        return {"plain_language": "The customer has no cap.", "obligations": [], "key_terms": [],
                "watch_outs": [], "citations": [{"chunk_id": cid, "quote": text[:40]}]}

    def contract_compare(self, v):
        a, b = excerpts(v["excerpts_a"]), excerpts(v["excerpts_b"])
        if not a or not b:
            return {"status": "unchanged", "headline": "Same", "value_a": None, "value_b": None,
                    "change_summary": "Same.", "citations_a": [], "citations_b": [], "confidence": 0.5}
        return {"status": "modified", "headline": "Changed", "value_a": "12345 days", "value_b": None,
                "change_summary": "Different.", "citations_a": [{"chunk_id": a[0][0], "quote": a[0][1][:50]}],
                "citations_b": [{"chunk_id": b[0][0], "quote": b[0][1][:50]}], "confidence": 0.8}

    def impact_business(self, v):
        return {"impacts": [{"change_id": c["id"], "impact": "More flexibility.", "favours": "party_a",
                             "risk_direction": "increased", "confidence": 0.7} for c in v["changes"]]}


@pytest.fixture(scope="module")
def fake(request):
    from backend import db

    db.init_db()
    client = FakeClient()
    mp = pytest.MonkeyPatch()
    mp.setattr(ai_service, "get_client", lambda: client)
    mp.setattr(ai_service, "is_enabled", lambda: True)
    yield client
    mp.undo()


def _create(name, text):
    row = contracts.create(f"{name}.txt", text.encode(), name=name)
    for _ in range(200):
        r = contracts.latest_report(row["id"])
        if r and r["status"] in {"complete", "partial", "failed"}:
            return row["id"]
        time.sleep(0.05)
    raise AssertionError("analysis did not finish")


def test_llm_risk_pipeline_drops_fabrications(fake):
    cid = _create("LLM MSA", MSA)
    report = ai_service.get_risk_report(contracts.latest_report(cid)["id"])
    assert report["status"] == "complete", report["error"]
    assert report["mode"] == "llm"
    types = {f["risk_type_id"]: f for f in report["findings"]}
    assert "FIN-01" in types and "FIN-02" not in types, "fabricated quote must be dropped"
    fin = types["FIN-01"]
    assert fin["clause_ref"] == "9.3" and fin["page"] == 1
    assert "5000000" not in fin["explanation"], "ungrounded number must be stripped"
    assert report["progress"]["drops"].get("quote_not_verbatim") == 1
    # Absence requires keyword scan + similarity + model; the model said "not addressed" for every type,
    # but termination is in the text, so it must not be reported missing.
    assert "LEG-04" not in types
    assert "LEG-02" in types and types["LEG-02"]["confidence"] <= 0.85
    for rec in report["recommendations"]:
        assert "does-not-exist" not in rec["item_ids"]
    assert all(r["text"] != "Invented action." for r in report["recommendations"])
    assert "risk.analyze@v1" in report["prompt_versions"]
    summary = contracts.get(cid)["summary"]
    assert summary["fields"]["liability"] is None, "field citing an unprovided chunk is dropped"
    assert summary["fields"]["purpose"]["text"] == "IT services."


def test_llm_answer_and_explain(fake):
    cid = next(c["id"] for c in contracts.list_all() if c["name"] == "LLM MSA")
    result = ai_service.generate_answer([cid], "What about liability?", [])
    assert result["found"]
    assert len(result["citations"]) == 1, "fabricated citation removed"
    assert "[1]" in result["answer"] and "777" not in result["answer"]
    chunk_id = result["citations"][0]["chunk_id"]
    explained = ai_service.explain_clause(chunk_id)
    assert explained["citations"] and explained["mode"] == "llm"


def test_llm_comparison_clears_ungrounded_values(fake):
    a = next(c["id"] for c in contracts.list_all() if c["name"] == "LLM MSA")
    b = _create("LLM MSA B", MSA_B)
    cmp_id = ai_service.create_comparison(a, b, "neutral")
    cmp = ai_service.compare_contracts(cmp_id)
    assert cmp["status"] == "complete", cmp["error"]
    modified = [c for c in cmp["changes"] if c["status"] == "modified"]
    assert modified
    for c in modified:
        assert c["value_a"] is None, "12345 days is not in the quote, so values are cleared"
        assert "values_not_in_quotes" in c["flags"]
        assert c["business_impact"] == "More flexibility."
    assert "contract.compare@v1" in cmp["prompt_versions"]
    assert "impact.business@v1" in cmp["prompt_versions"]
    assert set(fake.calls) >= {"contract.summarize", "risk.analyze", "summary.executive", "qa.answer",
                               "clause.explain", "contract.compare", "impact.business"}
