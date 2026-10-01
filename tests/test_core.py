from backend.config import get_settings
from backend.services import embeddings, ingest, scoring, verifier

CFG = get_settings().risk_config


# ----------------------------------------------------------------- verifier

def test_quote_found_with_whitespace_and_curly_quotes():
    source = "9.3 The Customer’s liability   shall be\nunlimited."
    loc = verifier.locate_quote("The Customer's liability shall be unlimited", source)
    assert loc is not None
    assert source[loc[0]:loc[1]].startswith("The Customer")


def test_fabricated_quote_is_rejected():
    source = "The Supplier's total liability shall not exceed the Fees paid in the twelve months."
    assert verifier.locate_quote("The Supplier's liability shall be unlimited", source) is None


def test_quote_joined_with_ellipsis_is_rejected():
    source = "Clause one says this. Clause two says that."
    assert verifier.locate_quote("Clause one ... that", source) is None


def test_ungrounded_numbers_are_stripped():
    text = "The notice period is 90 days. This is long. Penalties reach 50%."
    cleaned, changed = verifier.strip_ungrounded_sentences(text, "not less than 90 days' prior written notice")
    assert changed
    assert "90 days" in cleaned and "50%" not in cleaned


def test_clause_numbers_and_line_breaks_in_grounding():
    text = "Relevant passages:\n\n“5.2 The obligations continue for five (5) years”\n\nIt also says 99 days."
    cleaned, changed = verifier.strip_ungrounded_sentences(text, "5.2 The obligations continue for five (5) years")
    assert changed
    assert "5.2 The obligations" in cleaned and "99" not in cleaned
    assert cleaned.startswith("Relevant passages:\n\n")


def test_severity_clamped_to_one_level():
    order = CFG["severity_order"]
    assert verifier.clamp_severity("Critical", "Low", order) == "Medium"
    assert verifier.clamp_severity("Low", "Critical", order) == "High"
    assert verifier.clamp_severity("High", "Medium", order) == "High"
    assert verifier.clamp_severity("Nonsense", "Medium", order) == "Medium"


# ----------------------------------------------------------------- scoring

def test_sample_report_scores_40_medium():
    score = scoring.overall_score(["Critical", "Medium", "Low"], CFG["severity_weights"])
    assert score == 40
    assert scoring.level_for(score, CFG["level_bands"]) == "Medium"


def test_many_low_findings_stay_low():
    score = scoring.overall_score(["Low"] * 10, CFG["severity_weights"])
    assert scoring.level_for(score, CFG["level_bands"]) == "Low"


def test_absence_confidence_is_capped():
    assert scoring.finding_confidence(0.99, "absence", 0.0, CFG) == CFG["absence_confidence_cap"]


def test_dismissed_and_low_confidence_findings_do_not_count():
    findings = [
        {"severity": "Critical", "category": "Financial", "counted": 1, "status": "dismissed"},
        {"severity": "High", "category": "Legal", "counted": 0, "status": "open"},
        {"severity": "Medium", "category": "Legal", "counted": 1, "status": "open"},
    ]
    result = scoring.score_report(findings, CFG)
    assert result["counted"] == 1 and result["needs_review"] == 1
    assert result["score"] == 6


# ----------------------------------------------------------------- ingestion

def test_chunks_carry_clause_and_page():
    text = "AGREEMENT\n\n9. LIABILITY\n\n9.3 The Customer's liability shall be unlimited.\n\n" + ("Filler text. " * 300)
    doc = ingest.build_document(ingest._paginate(text))
    chunks = ingest.chunk_document(doc)
    liability = next(c for c in chunks if "unlimited" in c.text)
    assert liability.clause_ref == "9.3"
    assert liability.page_start == 1
    assert doc.full_text[liability.start_offset:liability.end_offset] == liability.text
    assert max(c.page_end for c in chunks) >= 2


def test_heading_parser():
    assert ingest.parse_heading("4. FEES AND PAYMENT") == ("4", "FEES AND PAYMENT")
    assert ingest.parse_heading("Section 12.1 Notices")[0] == "12.1"
    assert ingest.parse_heading("2026 was a good year") is None


def test_embeddings_rank_related_text_higher():
    a = embeddings.embed_text("limitation of liability")
    related = embeddings.embed_text("the supplier's total liability shall not exceed the fees")
    unrelated = embeddings.embed_text("invoices are payable within thirty days")
    assert float(a @ related) > float(a @ unrelated)
