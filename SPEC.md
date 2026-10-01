# AI Legal Assistant — AI modules specification

> Scope: **Core AI Module 1 (AI Risk Analyzer)** and **Core AI Module 2 (Contract Comparison)**,
> plus the dashboard, sidebar and AI-service changes that come with them.
> Delivery is planned in [BUILD_PLAN.md](BUILD_PLAN.md) (Sprints 6–10).
> Every LLM prompt lives in [PROMPTS.md](PROMPTS.md).

---

## 0. Baseline and assumptions

This spec extends the existing product (Sprints 1–5). It assumes these already exist. Anything
marked *(verify)* should be checked against the real codebase before Sprint 6 starts.

| Assumed capability | Needed for |
| --- | --- |
| Contract upload, text extraction, chunking, embeddings, vector store | Both modules |
| Each chunk stores `contract_id`, `page_start`, `page_end`, char offsets *(verify)* | Citations, page numbers, viewer highlights |
| Each chunk stores a `clause_ref` (e.g. `9.3`) parsed from headings *(verify — if missing, add in S6-6)* | "Clause 9.3" in findings |
| Auth, users, contract ownership | Access control on reports |
| Python 3.11+ / FastAPI backend with SQLite (built this way because Node.js wasn't available; the service names follow the spec) | Services, persistence |
| A page-by-page text viewer over the extracted text (no PDF.js) | Side-by-side viewer |

### Core principle: the LLM never invents evidence

Three rules apply to every AI feature in this spec:

1. **Evidence comes from retrieval, not from the model.** The model only sees retrieved chunks
   and must quote them verbatim. It never writes page numbers or clause numbers: the system
   attaches those from chunk metadata.
2. **Every model output is verified before it is stored.** Schema check, verbatim-quote check,
   citation check, number-grounding check (§3.6). Anything that fails is dropped, not shown.
3. **Scores are computed, not generated.** The overall risk score, risk level, distribution
   counts and portfolio score are deterministic functions of verified findings (§3.5).

Contract text is **untrusted input**. A contract can contain text like "ignore previous
instructions". Every prompt wraps contract text in delimiters and tells the model to treat it
as data only (see PROMPTS.md, shared rules).

---

## 1. Updated navigation

```text
Dashboard
Contracts
AI Chat
Risk Analyzer        ← new (Sprint 7)
Compare Contracts    ← new (Sprint 8)
Search
Analytics            ← expanded (Sprint 9)
Profile
Settings
```

---

## 2. AI services

All LLM work goes through one module, `backend/services/ai_service.py` (the frontend wrapper is `aiService` in `frontend/js/api.js`). Controllers and route handlers call these
functions and **never build prompts themselves**.

| Function | Purpose | Prompt id(s) in PROMPTS.md |
| --- | --- | --- |
| `generateAnswer(contractIds, question, history)` | Chat Q&A with citations | `qa.answer` |
| `summarizeContract(contractId)` | Structured contract summary | `contract.summarize` |
| `explainClause(chunkId, audience)` | Plain-language clause explanation | `clause.explain` |
| `riskAnalyzer(contractId)` | Module 1 pipeline | `risk.analyze`, then `summary.executive` |
| `compareContracts(contractAId, contractBId)` | Module 2 pipeline | `contract.compare`, then `impact.business`, then `summary.executive` |
| `generateExecutiveSummary(mode, payload)` | Summary + recommendations for a risk report or comparison | `summary.executive` |

`explainClause()` is not in the original service list, but the spec requires a separate
Clause Explanation prompt, and the risk drill-down and chat both need it. Business Impact
Analysis is called inside `compareContracts()`, not exposed as its own service.

### Prompt loading

- `PROMPTS.md` is the single source of truth. `backend/prompts/loader.py` parses it at startup into a
  registry keyed by prompt id (format in PROMPTS.md §"File format").
- Every stored report records the `prompt_id@version` and the model name used, for audit.
- A unit test fails if any service references a prompt id that is missing from PROMPTS.md, or if
  a template uses a `{{variable}}` the service does not supply.
- A test (`tests/test_prompts.py`) fails on prompt-like text in `backend/routes/`.

---

## 3. Module 1 — AI Risk Analyzer

### 3.1 Objective

Show users the legal and business risks in a contract as soon as it is indexed, before they ask
anything, with every finding traceable to the contract text.

### 3.2 Workflow

```text
Upload contract → Extract text → Chunk → Embed → Store in vector DB
      → [index complete event] → enqueue risk-analysis job
      → per category: targeted retrieval → risk.analyze prompt → verify
      → absence checks for "Missing …" risk types
      → deterministic scoring → summary.executive (recommendations)
      → store risk report → dashboard shows it
```

- Analysis runs as a **background job** triggered by the "indexing complete" event. Upload
  never waits for it.
- The contract list shows the status: `Analyzing…`, `Risk: Medium (42)`, or `Analysis failed — Retry`.
- Re-running is allowed (button on the report). Each run creates a new report version; the
  latest one is the current report.
- Target: report ready within 60 s for a 50-page contract (≈ 6 LLM calls, run in parallel).

### 3.3 Risk taxonomy

The taxonomy is **configuration** (`riskTaxonomy.json`), not code. Each risk type defines:
id, category, name, detection mode, default severity, retrieval queries, keyword hints, and
optional applicability conditions and thresholds.

**Detection modes**

- **presence**: the risk is a clause that exists (e.g. unlimited liability). Must cite a quote.
- **absence**: the risk is a clause that is missing (e.g. no governing law). There is no quote to
  cite, so the evidence is the search that was run (§3.4).
- **quality**: a clause exists but is vague or one-sided (e.g. ambiguous language,
  undefined deliverables). Must cite a quote.

| Id | Category | Risk type | Mode | Default severity |
| --- | --- | --- | --- | --- |
| FIN-01 | Financial | Unlimited liability | presence | Critical |
| FIN-02 | Financial | High penalty clauses | presence | High |
| FIN-03 | Financial | Automatic payment obligations | presence | Medium |
| FIN-04 | Financial | Late payment penalties | presence | Medium |
| FIN-05 | Financial | Hidden charges | presence | High |
| LEG-01 | Legal | Missing confidentiality | absence | High |
| LEG-02 | Legal | Missing governing law | absence | Medium |
| LEG-03 | Legal | Missing jurisdiction | absence | Medium |
| LEG-04 | Legal | Missing termination rights | absence | High |
| LEG-05 | Legal | Ambiguous language | quality | Medium |
| BUS-01 | Business | Automatic renewal | presence | Medium |
| BUS-02 | Business | Vendor lock-in | presence | High |
| BUS-03 | Business | Long notice period (> `notice_days_threshold`, default 90) | presence | Medium |
| BUS-04 | Business | One-sided obligations | quality | High |
| BUS-05 | Business | Exclusive agreements | presence | Medium |
| CMP-01 | Compliance | Missing GDPR clause ¹ | absence | High |
| CMP-02 | Compliance | Missing data protection clause ¹ | absence | High |
| CMP-03 | Compliance | Missing security requirements | absence | Medium |
| CMP-04 | Compliance | Missing audit rights | absence | Medium |
| OPS-01 | Operational | Undefined deliverables | quality / absence | High |
| OPS-02 | Operational | Undefined responsibilities | quality | Medium |
| OPS-03 | Operational | Missing SLA ² | absence | Medium |
| OPS-04 | Operational | Missing acceptance criteria ² | absence | Medium |

¹ Only raised if the contract involves personal data. Otherwise shown as *Not applicable*.
² Only raised for service or delivery contracts (from `contract_type` in the summary).

**Severity:** the model starts from the default severity. It may move it **one level** up or
down, and must give a reason (e.g. a penalty of 2% versus 50% of contract value). Larger jumps
are clamped by the verifier.

**Applicability** stops false positives such as "Missing GDPR clause" on a contract that
processes no personal data. These risk types are marked `not_applicable`, not omitted, so the
user can see they were checked.

### 3.4 Evidence rules

| Mode | Evidence required | Page and clause shown |
| --- | --- | --- |
| presence / quality | Verbatim quote (≤ 300 chars) from a retrieved chunk, plus that `chunk_id` | From chunk metadata: `Page 18 · Clause 9.3` |
| absence | All retrieval queries for the type return no chunk above `absence_similarity_threshold` (default 0.30, calibrated for the built-in lexical embedder) **and** the keyword scan of the full text finds no hits, **and** the model confirms the top-k chunks it was shown do not address the topic | `Not found in document`, plus "Searched for: governing law, applicable law, laws of …" |

A "weak" clause (for example a governing-law clause that names no jurisdiction) is a
**quality** finding with a quote and a page. A clause that does not exist at all is an
**absence** finding with no page. The UI labels these differently.

### 3.5 Scoring (deterministic)

**Finding confidence (0–100%)**

```text
presence / quality:
  confidence = llm_confidence × retrieval_factor
  retrieval_factor = 1.00 if chunk similarity ≥ 0.25   (thresholds calibrated for the lexical
                     0.90 if 0.15–0.25         embedder; raise them for a neural one)
                     0.80 otherwise
absence:
  confidence = min(llm_confidence, 0.85)     # absence is never certain
```

Findings below `min_confidence` (default 0.50) do not count toward the score. They are listed
under **Needs human review**, not discarded, so nothing is hidden silently.

**Overall contract risk score (0–100)** uses diminishing returns, so many low-severity findings
cannot add up to a critical score:

```text
weights: Critical 0.35 · High 0.15 · Medium 0.06 · Low 0.02
score = round(100 × (1 − Π (1 − weight_i)))   over counted findings
level: 0–24 Low · 25–49 Medium · 50–74 High · 75–100 Critical
```

Worked example (matches the sample report): 1 Critical, 1 Medium, 1 Low →
`1 − 0.65 × 0.94 × 0.98 = 0.40` → **40, Medium**.

**Portfolio risk score** = mean of the latest report score across the user's contracts. Shown
together with the number of contracts it covers.

All weights and thresholds live in `riskConfig.json`.

### 3.6 Verification pipeline (runs on every `risk.analyze` response)

1. **Schema:** validate against the output schema (jsonschema; the API also enforces it via structured outputs). On failure, retry once, then mark the
   category `failed` (the report still completes and shows the failed category).
2. **Citation:** `chunk_id` must be one of the chunks sent in this call.
3. **Verbatim quote:** after normalising whitespace and quote characters, the quote must be a
   substring of that chunk's text. Drop the finding if it is not.
4. **Number grounding:** every number in `explanation` and `reason` must appear in the quote, or
   in a configured threshold. Otherwise strip the sentence and flag the finding.
5. **Severity clamp:** at most ±1 level from the default.
6. **Deduplicate:** if two findings cite overlapping chunks for the same risk type, merge them.

Drop counts per rule are logged and shown in the eval harness. A rising drop rate means the
prompt or model has regressed.

### 3.7 Finding fields

| Field | Source | Notes |
| --- | --- | --- |
| `risk_type_id`, `category` | taxonomy | |
| `title` | LLM | e.g. "Unlimited liability clause detected" |
| `severity` | LLM, then clamped | Critical / High / Medium / Low |
| `evidence_type` | system | `clause` or `absence` |
| `clause_ref` | chunk metadata | Clause, e.g. `9.3`; null for absence |
| `page` | chunk metadata | Page number; null for absence |
| `quote`, `chunk_id` | LLM, then verified | |
| `explanation` | LLM | **What** the clause says, in plain language |
| `reason` | LLM | **Why** it is a risk, and for whom |
| `suggested_improvement` | LLM | Concrete redline or negotiation point |
| `confidence` | computed (§3.5) | Confidence score |
| `status` | user | `open` / `accepted` / `dismissed`, plus an optional note |

### 3.8 Risk report

```text
Overall contract risk     Medium · 40/100
Analysed 2026-10-01 14:02 · 23 checks · 2 not applicable · model gpt-x · risk.analyze@v1

Critical risks
  • Unlimited liability clause detected            Page 18 · Clause 9.3 · 92%
High / Medium / Low risks
  • Auto-renewal without notice                    Page 11 · Clause 4.1 · 88%
  • Governing law not clearly specified            Page 25 · 71%
Not found in document
  • No audit rights clause                         searched: audit, inspect records, … · 80%
Needs human review (below confidence threshold)
  • …
AI recommendations                                 (each linked to the findings it addresses)
  1. Review liability clause (→ FIN-01)
  2. Negotiate termination conditions (→ LEG-04)
  3. Clarify renewal process (→ BUS-01)
```

Recommendations come from `summary.executive`, which only sees the **verified** findings. Each
recommendation must reference at least one finding id, or it is dropped.

### 3.9 Risk Analyzer page

- **Header:** contract selector, overall score gauge (0–100, banded colours), level label,
  last-analysed time, Re-run button, Export button (Markdown / PDF).
- **Charts:**
  - Severity distribution: horizontal bar, 4 bars.
  - Category distribution: stacked bar by category, segmented by severity.
- **Critical findings:** cards at the top; each shows title, page/clause, quote, confidence.
- **High priority clauses:** table of Critical + High findings sorted by severity then
  confidence. Clicking a row opens the document viewer at that page with the quote highlighted.
- **Recommended actions:** a numbered list; each item links to its findings.
- **Finding drill-down:** explanation, reason, suggested improvement, an "Explain this clause"
  button (`explainClause`), and Accept / Dismiss with a note.
- **Empty states:** `Analyzing…` with progress by category, `No risks found in 23 checks` (still
  lists what was checked), and `Analysis failed` with Retry.

Every page shows the note "AI-assisted review, not legal advice."

### 3.10 Data model

```text
RiskReport
  id, contract_id, version, status (queued|running|complete|partial|failed),
  overall_score, overall_level, counts_by_severity{}, counts_by_category{},
  not_applicable[], failed_categories[], recommendations[{text, finding_ids[]}],
  model, prompt_versions{}, duration_ms, created_at

RiskFinding
  id, report_id, risk_type_id, category, title, severity, evidence_type,
  clause_ref, page, chunk_id, quote, explanation, reason, suggested_improvement,
  confidence, counted (bool), status, reviewer_note, search_queries[] (absence only)
```

### 3.11 API

```text
POST  /api/contracts/:id/risk-analysis         re-run (202 + job id)
GET   /api/contracts/:id/risk-report           latest report (+ ?version=n)
GET   /api/risk-reports/:id/export?format=md|pdf
PATCH /api/risk-findings/:id                   { status, reviewer_note }
```

---

## 4. Module 2 — Contract Comparison

### 4.1 Objective

Show what changed between two contracts, or two versions of one contract, by meaning rather
than by text diff, and explain the business impact of each change.

### 4.2 Workflow

```text
Select contract A → Select contract B
  → per category: retrieve clauses from A and from B (filtered by contract_id)
  → align clauses A↔B by embedding similarity
  → fast path: aligned pairs with normalised-text equality or cosine ≥ 0.98 → Unchanged (no LLM call)
  → contract.compare prompt for the rest → verify
  → impact.business for every Added / Removed / Modified change (one batched call)
  → summary.executive (comparison mode)
  → store comparison → side-by-side viewer + report
```

### 4.3 Categories

Payment terms · Notice period · Confidentiality · Intellectual property · Liability ·
Indemnification · Renewal terms · Governing law · Termination conditions · Deliverables ·
Service levels · Warranty · Pricing · Responsibilities.

Like the risk taxonomy, these are configuration (`comparisonCategories.json`): name,
retrieval queries, keyword hints, and an optional **structured value** to extract (e.g.
Notice period → `{days}`, Payment terms → `{frequency, due_days}`). This is what makes
"60 days → 90 days" possible instead of only prose.

### 4.4 Classification

| Status | Meaning |
| --- | --- |
| **Added** | Category addressed in B, not in A |
| **Removed** | Category addressed in A, not in B |
| **Modified** | Both address it and the **meaning** differs (obligations, amounts, periods, parties, conditions) |
| **Unchanged** | Both address it with the same meaning, even if reworded or renumbered; or neither addresses it (note: "Not addressed in either") |

Rewording, renumbering and formatting are **not** changes. A clause that moves from 4.2 to 5.1
with the same text is Unchanged, and the viewer shows the move.

### 4.5 Change record

```text
ComparisonChange
  id, comparison_id, category, status, headline,
  value_a, value_b,                          # e.g. "60 days" / "90 days" — must appear in quotes
  clause_ref_a, page_a, chunk_id_a, quote_a,
  clause_ref_b, page_b, chunk_id_b, quote_b,
  change_summary, business_impact, favours (party_a|party_b|neutral|unclear),
  risk_direction (increased|decreased|neutral), confidence

Comparison
  id, contract_a_id, contract_b_id, status, counts_by_status{},
  executive_summary, recommendations[{text, change_ids[]}],
  risk_delta{ score_a, score_b, new_findings[], resolved_findings[] },
  model, prompt_versions{}, created_at
```

The verification rules from §3.6 apply. In addition, `value_a` and `value_b` must appear in
`quote_a` and `quote_b`. If they do not, the values are cleared and only the prose summary is
kept.

**Risk delta:** if both contracts have risk reports, the comparison shows the score change and
which findings are new or resolved. This is a deterministic join on `risk_type_id`, with no
extra LLM call.

### 4.6 Executive summary (comparison)

```text
Contract comparison summary
Documents compared: Employment_Agreement_V1.pdf ↔ Employment_Agreement_V2.pdf
4 modified · 1 added · 0 removed · 9 unchanged · Risk 38 → 47 (+9)

Major changes
  Notice period      Modified   60 days → 90 days                  A p.4 §4.2 · B p.4 §4.2
  Payment terms      Modified   Monthly → Quarterly                A p.6 §6.1 · B p.6 §6.1
  Liability          Modified   New limitation clause added        B p.9 §9.4
  Termination        Modified   Employer gains additional termination rights
  Confidentiality    Unchanged
```

### 4.7 Business impact

Produced per change by `impact.business`. The user can set **"I represent: Party A / Party B /
Neutral"** on the compare screen, because whether a change is good or bad depends on the side.

```text
Business impact
The notice period increases from 60 to 90 days. This gives the employer more transition
time but reduces employee flexibility.
Favours: Employer · Risk for you: increased · Confidence 95%
```

### 4.8 Side-by-side viewer

- Two PDF panels, A on the left and B on the right, with a change navigator rail between them.
- **Synchronised scroll is anchored on aligned clauses, not on scroll percentage.** The two
  documents usually differ in length, so a percentage-based sync drifts. When the user scrolls
  A, the viewer finds the nearest aligned clause anchor and scrolls B to its counterpart.
  A "Lock scroll" toggle turns this on and off.
- **Highlights** come from chunk char offsets / page coordinates. Colours: Modified = amber,
  Added = green (B only), Removed = red (A only), Unchanged = none (optional faint outline).
- **Navigation:** Previous / Next change buttons, a category filter, a status filter, and
  clicking a change jumps both panels to its pages.
- **Narrow screens:** the panels become tabs (A | B | Changes), and the current change stays
  selected when switching tabs.

### 4.9 Export

| Format | Status | Implementation |
| --- | --- | --- |
| Markdown | Sprint 8 | Deterministic template rendered from the stored comparison (no LLM at export time) |
| PDF | Sprint 8 | Same template as HTML, rendered server-side with fpdf2 |
| DOCX | Future | Shown in the menu as disabled ("Coming soon") |

Sections: executive summary · all changes (table + detail) · risk analysis (risk delta) ·
AI recommendations · citations with quotes · page references · a footer with model, prompt
versions, timestamp and the "not legal advice" note.

The same export engine also serves risk reports (§3.11).

### 4.10 API

```text
POST /api/comparisons                       { contract_a_id, contract_b_id, perspective } → 202
GET  /api/comparisons/:id
GET  /api/comparisons                       list (for the dashboard and history)
GET  /api/comparisons/:id/export?format=md|pdf
```

---

## 5. Updated dashboard

| Tile / widget | Definition |
| --- | --- |
| Total contracts | Contracts the user can access |
| AI conversations | Chat conversations (not messages) |
| Overall portfolio risk score | §3.5 portfolio score, with level and "across N contracts" |
| Critical risks detected | Counted Critical findings with status `open`, in latest reports |
| Contracts compared | Completed comparisons |
| Recently uploaded contracts | Latest 5, each with its risk badge or analysis status |
| Recent AI searches | Latest 5 chat questions and searches, linking back to the conversation |

Also on the dashboard: a small risk-distribution chart (portfolio counts by severity) and a
"Top critical findings" list linking to the Risk Analyzer.

---

## 6. Non-functional requirements

- **Accuracy eval:** a golden set of at least 10 labelled contracts (Sprint 7). Targets: risk
  **precision ≥ 0.90** (the "never invent" rule makes precision the priority), recall ≥ 0.75,
  0 unverifiable quotes shown. For comparison: classification accuracy ≥ 0.90 on a labelled
  version-pair set. Run in CI on prompt or model changes.
- **Privacy:** contract text is sent only to the configured LLM endpoint. It is not logged in
  plaintext; logs store chunk ids.
- **Auditability:** every report stores the model, prompt versions and chunk ids used.
- **Disclaimer:** "AI-assisted review, not legal advice" on the risk, comparison and export
  surfaces.

---

## 7. Open questions

1. LLM provider and model (sets cost and the per-call token budget for retrieved context).
2. Scanned PDFs: does Sprint 1–5 extraction include OCR? Without it, page numbers and quotes fail.
3. Is `clause_ref` already parsed at chunking time? If not, S6-6 adds it.
4. Should "versions of the same contract" be a first-class link (contract family), or is any
   pair of contracts comparable? This spec allows any pair and adds a "Compare with previous
   version" shortcut when a family link exists.
5. Can a user override severity? Proposed: yes, as a reviewer override that is stored
   separately and never overwrites the AI value.
