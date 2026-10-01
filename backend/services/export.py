"""Deterministic report export (no LLM calls at export time). Markdown and PDF share one block model."""

from __future__ import annotations

import os
from pathlib import Path

from fpdf import FPDF

DISCLAIMER = "AI-assisted review, not legal advice. Verify every finding against the source contract."
SEVERITIES = ["Critical", "High", "Medium", "Low"]
STATUS_LABEL = {"modified": "Modified", "added": "Added", "removed": "Removed", "unchanged": "Unchanged"}


def _loc(page, clause) -> str:
    parts = []
    if page:
        parts.append(f"Page {page}")
    if clause:
        parts.append(f"Clause {clause}")
    return " · ".join(parts) or "Not found in document"


# ----------------------------------------------------------------------------- blocks

def risk_blocks(report: dict) -> list[tuple]:
    c = report["contract"]
    findings = report["findings"]
    counts = report.get("counts") or {}
    blocks: list[tuple] = [
        ("h1", f"Contract risk report: {c['name']}"),
        ("meta", f"Analysed {report['created_at']} · {report.get('checks_run') or 0} checks · mode {report['mode']} · "
                 f"model {report['model']} · prompts {', '.join(report.get('prompt_versions') or []) or 'n/a'}"),
        ("h2", "Overall contract risk"),
        ("p", f"{report['overall_level']} · {report['overall_score']}/100"),
    ]
    if report.get("executive_summary"):
        blocks.append(("p", report["executive_summary"]))
    sev = counts.get("by_severity") or {}
    blocks += [("h2", "Risk distribution"), ("table", ["Severity", "Findings"], [[s, str(sev.get(s, 0))] for s in SEVERITIES])]

    active = [f for f in findings if f["status"] != "dismissed"]
    counted = [f for f in active if f["counted"]]
    for severity in SEVERITIES:
        group = [f for f in counted if f["severity"] == severity and f["evidence_type"] == "clause"]
        if not group:
            continue
        blocks.append(("h2", f"{severity} risks"))
        for f in group:
            blocks += [
                ("h3", f"{f['title']} ({f['risk_type_id']})"),
                ("meta", f"{_loc(f['page'], f['clause_ref'])} · Confidence {round(f['confidence'] * 100)}% · {f['category']}"),
                ("quote", f["quote"] or ""),
                ("p", f"What it says: {f['explanation']}"),
                ("p", f"Why it matters: {f['reason']}"),
                ("p", f"Suggested improvement: {f['suggested_improvement']}"),
            ]
    absent = [f for f in counted if f["evidence_type"] == "absence"]
    if absent:
        blocks.append(("h2", "Not found in document"))
        for f in absent:
            searched = ", ".join((f.get("search_queries") or [])[:5])
            blocks += [
                ("h3", f"{f['title']} ({f['severity']})"),
                ("meta", f"Confidence {round(f['confidence'] * 100)}% · Searched for: {searched}"),
                ("p", f"Why it matters: {f['reason']}"),
                ("p", f"Suggested improvement: {f['suggested_improvement']}"),
            ]
    review = [f for f in active if not f["counted"]]
    if review:
        blocks.append(("h2", "Needs human review (below confidence threshold)"))
        for f in review:
            blocks.append(("bullet", f"{f['title']} · {_loc(f['page'], f['clause_ref'])} · {round(f['confidence'] * 100)}%"))
    recs = report.get("recommendations") or []
    if recs:
        titles = {f["id"]: f["title"] for f in findings}
        blocks.append(("h2", "AI recommendations"))
        for i, r in enumerate(recs, 1):
            refs = "; ".join(titles.get(x, x) for x in r["item_ids"])
            blocks.append(("bullet", f"{i}. {r['text']} (addresses: {refs})"))
    na = report.get("not_applicable") or []
    if na:
        blocks += [("h2", "Checks not applicable"), ("p", ", ".join(n["name"] for n in na))]
    blocks.append(("footer", DISCLAIMER))
    return blocks


def comparison_blocks(cmp: dict) -> list[tuple]:
    a, b = cmp["contract_a"], cmp["contract_b"]
    counts = cmp.get("counts") or {}
    changes = cmp["changes"]
    delta = cmp.get("risk_delta")
    blocks: list[tuple] = [
        ("h1", "Contract comparison summary"),
        ("meta", f"Compared {cmp['created_at']} · mode {cmp['mode']} · model {cmp['model']} · perspective {cmp['perspective']} · "
                 f"prompts {', '.join(cmp.get('prompt_versions') or []) or 'n/a'}"),
        ("p", f"Documents compared: {a['filename']} (A) and {b['filename']} (B)"),
        ("p", " · ".join(f"{counts.get(k, 0)} {k}" for k in ("modified", "added", "removed", "unchanged"))
         + (f" · Risk {delta['score_a']} → {delta['score_b']}" if delta else "")),
        ("h2", "Executive summary"),
        ("p", cmp.get("executive_summary") or "No summary available."),
    ]
    changed = [c for c in changes if c["status"] != "unchanged"]
    if changed:
        rows = []
        for c in changed:
            values = f"{c['value_a'] or '—'} → {c['value_b'] or '—'}" if (c["value_a"] or c["value_b"]) else c["headline"]
            refs = " / ".join(x for x in (
                f"A p.{c['page_a']}" if c["page_a"] else "", f"B p.{c['page_b']}" if c["page_b"] else "") if x)
            rows.append([c["category"], STATUS_LABEL[c["status"]], values, refs])
        blocks += [("h2", "Major changes"), ("table", ["Topic", "Change", "Detail", "Pages"], rows)]
    blocks.append(("h2", "All changes"))
    for c in changes:
        blocks.append(("h3", f"{c['category']}: {STATUS_LABEL[c['status']]}"))
        blocks.append(("p", c["headline"]))
        if c["change_summary"]:
            blocks.append(("p", c["change_summary"]))
        if c["quote_a"]:
            blocks += [("meta", f"Version A · {_loc(c['page_a'], c['clause_ref_a'])}"), ("quote", c["quote_a"])]
        if c["quote_b"]:
            blocks += [("meta", f"Version B · {_loc(c['page_b'], c['clause_ref_b'])}"), ("quote", c["quote_b"])]
        if c.get("business_impact"):
            fav = (c.get("favours") or "unclear").replace("_", " ")
            blocks.append(("p", f"Business impact: {c['business_impact']} (favours: {fav}; risk for you: "
                                f"{c.get('risk_direction') or 'neutral'}; confidence {round((c.get('impact_confidence') or 0) * 100)}%)"))
        blocks.append(("meta", f"Classification confidence {round((c['confidence'] or 0) * 100)}%"))
    blocks.append(("h2", "Risk analysis"))
    if delta:
        blocks.append(("p", f"Risk score A: {delta['score_a']} ({delta['level_a']}) · B: {delta['score_b']} ({delta['level_b']})"))
        for f in delta.get("new_findings", []):
            blocks.append(("bullet", f"New in B: {f['title']} ({f['severity']})"))
        for f in delta.get("resolved_findings", []):
            blocks.append(("bullet", f"Resolved in B: {f['title']} ({f['severity']})"))
    else:
        blocks.append(("p", "Risk reports were not available for both contracts."))
    recs = cmp.get("recommendations") or []
    if recs:
        names = {c["id"]: c["category"] for c in changes}
        blocks.append(("h2", "AI recommendations"))
        for i, r in enumerate(recs, 1):
            blocks.append(("bullet", f"{i}. {r['text']} (re: {', '.join(names.get(x, x) for x in r['item_ids'])})"))
    blocks.append(("h2", "Citations and page references"))
    for c in changes:
        for side, label in (("a", "A"), ("b", "B")):
            if c[f"quote_{side}"]:
                blocks.append(("bullet", f"{c['category']} · {label} {_loc(c[f'page_{side}'], c[f'clause_ref_{side}'])}: "
                                         f"\u201c{c[f'quote_{side}'][:160]}\u201d"))
    blocks.append(("footer", DISCLAIMER))
    return blocks


# ----------------------------------------------------------------------------- renderers

def to_markdown(blocks: list[tuple]) -> str:
    out = []
    for block in blocks:
        kind = block[0]
        if kind == "h1":
            out.append(f"# {block[1]}\n")
        elif kind == "h2":
            out.append(f"\n## {block[1]}\n")
        elif kind == "h3":
            out.append(f"\n### {block[1]}\n")
        elif kind == "meta":
            out.append(f"_{block[1]}_\n")
        elif kind == "p":
            out.append(f"{block[1]}\n")
        elif kind == "bullet":
            out.append(f"- {block[1]}")
        elif kind == "quote":
            out.append("> " + block[1].replace("\n", "\n> ") + "\n")
        elif kind == "table":
            headers, rows = block[1], block[2]
            out.append("| " + " | ".join(headers) + " |")
            out.append("| " + " | ".join("---" for _ in headers) + " |")
            for row in rows:
                out.append("| " + " | ".join(str(c).replace("|", "\\|") for c in row) + " |")
            out.append("")
        elif kind == "footer":
            out.append(f"\n---\n\n_{block[1]}_\n")
    return "\n".join(out)


_FONT_CANDIDATES = [
    ("segoeui.ttf", "segoeuib.ttf", "segoeuii.ttf"),
    ("arial.ttf", "arialbd.ttf", "ariali.ttf"),
    ("DejaVuSans.ttf", "DejaVuSans-Bold.ttf", "DejaVuSans-Oblique.ttf"),
]
_FONT_DIRS = [Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts", Path("/usr/share/fonts/truetype/dejavu"),
              Path("/Library/Fonts")]


def _setup_fonts(pdf: FPDF) -> tuple[str, bool]:
    for regular, bold, italic in _FONT_CANDIDATES:
        for directory in _FONT_DIRS:
            if (directory / regular).exists() and (directory / bold).exists():
                pdf.add_font("Body", "", str(directory / regular))
                pdf.add_font("Body", "B", str(directory / bold))
                pdf.add_font("Body", "I", str(directory / (italic if (directory / italic).exists() else regular)))
                return "Body", True
    return "Helvetica", False


def to_pdf(blocks: list[tuple]) -> bytes:
    pdf = FPDF(format="A4")
    pdf.set_auto_page_break(auto=True, margin=16)
    pdf.set_margins(18, 16, 18)
    font, unicode_ok = _setup_fonts(pdf)
    pdf.add_page()

    def txt(s: str) -> str:
        s = str(s)
        if unicode_ok:
            return s
        for a, b in {"\u2192": "->", "\u2014": "-", "\u2013": "-", "\u00b7": "|", "\u201c": '"', "\u201d": '"',
                     "\u2018": "'", "\u2019": "'", "\u2026": "..."}.items():
            s = s.replace(a, b)
        return s.encode("latin-1", "replace").decode("latin-1")

    width = pdf.w - pdf.l_margin - pdf.r_margin
    for block in blocks:
        kind = block[0]
        pdf.set_x(pdf.l_margin)
        if kind == "h1":
            pdf.set_font(font, "B", 17)
            pdf.set_text_color(20, 30, 50)
            pdf.multi_cell(width, 8, txt(block[1]))
            pdf.ln(1)
        elif kind == "h2":
            pdf.ln(3)
            pdf.set_font(font, "B", 13)
            pdf.set_text_color(30, 50, 90)
            pdf.multi_cell(width, 7, txt(block[1]))
            pdf.set_draw_color(210, 215, 225)
            pdf.line(pdf.l_margin, pdf.get_y(), pdf.l_margin + width, pdf.get_y())
            pdf.ln(2)
        elif kind == "h3":
            pdf.ln(1.5)
            pdf.set_font(font, "B", 11)
            pdf.set_text_color(25, 25, 35)
            pdf.multi_cell(width, 6, txt(block[1]))
        elif kind == "meta":
            pdf.set_font(font, "I", 8.5)
            pdf.set_text_color(110, 115, 130)
            pdf.multi_cell(width, 4.6, txt(block[1]))
            pdf.ln(0.5)
        elif kind in {"p", "bullet"}:
            pdf.set_font(font, "", 10)
            pdf.set_text_color(35, 35, 45)
            prefix = "\u2022 " if kind == "bullet" and unicode_ok else ("- " if kind == "bullet" else "")
            pdf.multi_cell(width, 5.2, txt(prefix + block[1]))
            pdf.ln(0.8)
        elif kind == "quote":
            pdf.set_font(font, "I", 9.5)
            pdf.set_text_color(60, 60, 75)
            pdf.set_fill_color(244, 246, 250)
            pdf.set_x(pdf.l_margin + 3)
            pdf.multi_cell(width - 3, 5, txt(block[1]), fill=True)
            pdf.ln(1)
        elif kind == "table":
            headers, rows = block[1], block[2]
            pdf.set_font(font, "", 9)
            pdf.set_text_color(35, 35, 45)
            with pdf.table(text_align="LEFT", line_height=5.2, first_row_as_headings=True) as table:
                head = table.row()
                for h in headers:
                    head.cell(txt(h))
                for r in rows:
                    row = table.row()
                    for cell in r:
                        row.cell(txt(cell))
            pdf.ln(2)
        elif kind == "footer":
            pdf.ln(4)
            pdf.set_font(font, "I", 8.5)
            pdf.set_text_color(120, 120, 130)
            pdf.multi_cell(width, 4.5, txt(block[1]))
    return bytes(pdf.output())
