# BUILD_PLAN.md — Sprints 6–10

> Agile backlog for the AI modules. Specs: [SPEC.md](SPEC.md). Prompts: [PROMPTS.md](PROMPTS.md).
> When asked to "build Sprint N", implement that sprint's stories in order.
> Story ids are referenced from PRs and commits (e.g. `S7-3`).

## Implementation status (2026-10-01)

| Sprint | Status | Notes |
| --- | --- | --- |
| 6 | Done | Prompt registry, Claude client and verifier, cited Q&A, conversation history, summaries, clause metadata |
| 7 | Done | Taxonomy config, background analysis, presence and absence detection, scoring, recommendations, Risk Analyzer UI, reviewer actions. S7-9 eval: sample contracts plus a fake-LLM test only. A real labelled golden set of 10+ contracts and a live-model run are still needed |
| 8 | Done | Comparison setup, clause alignment, semantic diff, business impact, risk delta, side-by-side viewer with clause-anchored sync scroll, Markdown/PDF export (DOCX disabled), dashboard and sidebar |
| 9 | Partly done | Analytics page and responsive layout (375px checked) are done. Not done: per-user auth, rate limits, streaming chat |
| 10 | Partly done | README, run script and 33 automated tests. Not done: containers, deployment, monitoring |

Built with Python/FastAPI rather than Node.js (Node wasn't installed on the build machine).
`aiService.js` in the original plan is `backend/services/ai_service.py`.

## Definition of done (every story)

- Acceptance criteria pass, and tests are written alongside the code (unit tests, plus an
  integration test for each endpoint).
- No prompt text in controllers or routes. All LLM calls go through `aiService.js` and
  `PROMPTS.md`.
- Every AI output shown to a user has passed verification (SPEC §3.6).
- Loading, empty and error states exist for each new UI surface.
- The "AI-assisted review, not legal advice" note is shown wherever AI analysis is displayed.

## Dependency map

```text
S6-1 prompt loader ─┬─► S6-2 aiService + verifier ─┬─► S6-3 Q&A + citations ─► S6-4 history
S6-6 chunk metadata ┘                              ├─► S6-5 summarize ─┐
                                                   │                   ▼
                                                   ├─► S7-1…S7-9 Risk Analyzer
                                                   └─► S8-1…S8-8 Comparison (uses S7 risk delta + export)
```

---

## Sprint 6 — AI question answering

**Goal:** a grounded chat that answers from the contract, cites the page, and remembers the
conversation. This sprint also builds the shared AI foundation that Sprints 7 and 8 need.

| Id | Story | Acceptance criteria |
| --- | --- | --- |
| S6-1 | **Prompt registry.** `promptLoader.js` parses PROMPTS.md (format in its §"File format"), supports `{{var}}` and `{{> shared.rules}}`, and exposes `prompts.render(id, vars)` and `prompts.version(id)`. | Loads all 7 prompts. Throws on a missing variable. A test fails if a service references an unknown id. A CI grep blocks prompt literals in `controllers/` and `routes/`. |
| S6-2 | **aiService core + verifier.** LLM client (provider and model from env), JSON-mode calls, schema validation (zod, generated from the output schemas), retry-once on schema failure, and a `verify()` toolkit: citation-in-context, verbatim quote, number grounding. | Unit tests for each verifier rule, including quote normalisation (whitespace, curly quotes) and a fabricated-quote case that gets dropped. Token usage and latency are logged per call, without contract text. |
| S6-3 | **`generateAnswer` + citation engine.** Retrieval (top-k, contract-scoped), `qa.answer`, verification, and citation chips `[p.18 §9.3]` that open the viewer at that page with the quote highlighted. | Out-of-document questions return "I couldn't find this in the contract." Every displayed citation resolves to a real chunk. A prompt-injection test contract (a clause saying "ignore instructions…") does not change behaviour. |
| S6-4 | **Conversation history.** Persist conversations and messages per user and contract. List, resume, rename, delete. Send the last N turns as `{{history}}`. | Reloading the page restores the thread. History is labelled as context, not evidence. Feeds the "AI conversations" and "Recent AI searches" tiles. |
| S6-5 | **`summarizeContract`.** Runs after indexing. Stores `contract_type` and `involves_personal_data`, which S7 uses for applicability. | Null fields render as "Not addressed". Every non-null field has citations. |
| S6-6 | **Chunk metadata check.** Confirm chunks carry `page_start`, `page_end`, char offsets and `clause_ref`. If any are missing, add heading-based `clause_ref` parsing and a backfill job. | 95% of chunks in the sample set get the correct `clause_ref`. Re-indexing an existing contract fills in the metadata. |

---

## Sprint 7 — AI Risk Analyzer

**Goal:** every newly indexed contract gets an evidence-backed risk report and an interactive
risk dashboard, with no user action needed.

| Id | Story | Acceptance criteria |
| --- | --- | --- |
| S7-1 | **Risk taxonomy config.** `riskTaxonomy.json` with all 23 risk types (SPEC §3.3): mode, default severity, definition, retrieval queries, keyword hints, applicability rules. `riskConfig.json` holds weights, thresholds and bands. | Schema-validated at startup. Adding a risk type needs no code change (test: add a dummy type and see it run). |
| S7-2 | **Analysis job.** On the "index complete" event, enqueue `riskAnalyzer(contractId)`. Statuses: `queued`, `running`, `complete`, `partial`, `failed`. Progress is reported per category. Includes the Re-run endpoint. | Upload returns without waiting. The contract list shows the live status. A failed category gives a `partial` report, not a lost one. Re-run creates report version n+1. |
| S7-3 | **Presence and quality detection.** Per category: run the retrieval queries, dedupe the chunks, call `risk.analyze`, verify (SPEC §3.6), attach page and clause from metadata, clamp severity. The 5 category calls run in parallel. | Fabricated or altered quotes never reach the DB (test with a mocked LLM). Page and clause always come from metadata. A 50-page contract finishes in under 60 s. |
| S7-4 | **Absence detection.** Combine the retrieval similarity threshold, the full-text keyword scan and the model's `absence_checks`. Store the search queries as evidence. Apply the applicability rules (GDPR and data protection need personal data; SLA and acceptance criteria need a services contract). | An NDA without a governing-law clause → "Missing governing law", shown as Not found in document. An NDA with no personal data → GDPR shows Not applicable, not a finding. Confidence for absence findings is at most 85%. |
| S7-5 | **Scoring + persistence.** Confidence formula, the `counted` flag (≥ `min_confidence`), overall score and level, severity and category counts, and the portfolio score. Stores `RiskReport` and `RiskFinding`. | Unit test: 1 Critical + 1 Medium + 1 Low = 40, Medium. Scores never come from the LLM. Below-threshold findings appear only under "Needs human review". |
| S7-6 | **Recommendations.** `generateExecutiveSummary('risk report', …)` over the verified findings. Drop recommendations that have no `item_ids`. | Each recommendation links to its findings. With zero findings, the summary says so and returns no recommendations. |
| S7-7 | **Risk Analyzer page.** Score gauge, severity bar chart, category stacked bar, critical findings cards, high-priority clauses table, recommended actions, finding drill-down with "Explain this clause" (`explainClause`), Accept/Dismiss with a note, and click-through to the viewer at the quote. | Layout matches SPEC §3.9. All empty, loading and failed states work. Dismissed findings are excluded from the dashboard counts of open critical risks. |
| S7-8 | **Sidebar: Risk Analyzer entry** and a risk badge on each contract row and card. | The badge colour matches the level band. Clicking it opens that contract's report. |
| S7-9 | **Risk eval harness.** A golden set of at least 10 labelled contracts (mix of NDA, services and employment, with planted risks and planted prompt injections). Reports precision, recall, verifier drop rates and per-type confusion. Runs in CI when prompts or the model change. | Baseline recorded. Gate: precision ≥ 0.90, recall ≥ 0.75, 0 unverifiable quotes. |

---

## Sprint 8 — Contract comparison

**Goal:** pick two contracts or versions, see the meaningful differences side by side with
their business impact, and export the report.

| Id | Story | Acceptance criteria |
| --- | --- | --- |
| S8-1 | **Compare setup screen.** Pick A and B (searchable), with a "Compare with previous version" shortcut when a contract family link exists, and a perspective selector (I represent A / B / Neutral). | A and B cannot be the same contract. Compare is disabled until both are indexed. |
| S8-2 | **Clause extraction and alignment.** `comparisonCategories.json` (14 categories, queries, value specs). Per category, retrieve from A and B, then align pairs by cosine similarity with greedy matching above a threshold. Fast-path identical pairs to Unchanged. | Renumbered identical clauses come out Unchanged with no LLM call (test). Alignment anchors are stored for the viewer. |
| S8-3 | **`compareContracts` semantic diff.** Call `contract.compare` for the categories that are not fast-pathed, in parallel. Verify citations and quotes. `value_a` and `value_b` must appear in their quotes, or they are cleared. | On the golden version-pair set (6+ pairs), classification accuracy ≥ 0.90. A pure-rewording pair gives 0 Modified. "60 days → 90 days" is extracted correctly. |
| S8-4 | **Business impact.** One batched `impact.business` call covering all non-Unchanged changes, using the perspective. Merged into `ComparisonChange`. | Impact never introduces a number missing from the change (number-grounding test). Changing the perspective flips `risk_direction` where expected. |
| S8-5 | **Comparison summary and risk delta.** `generateExecutiveSummary('comparison', …)`, plus a deterministic risk delta from both contracts' latest risk reports (triggering analysis if one is missing). | The summary header shows counts by status and the risk score change. Recommendations reference change ids. |
| S8-6 | **Side-by-side viewer.** Two PDF panels, clause-anchored synchronised scroll with a lock toggle, status-coloured highlights, a change navigator (previous/next, filter by category and status), and jump to page. Narrow screens use tabs. | Scroll sync stays aligned on contracts with different page counts (test: A has 20 pages, B has 26). Clicking a change scrolls both panels to its clauses within 300 ms. |
| S8-7 | **Export engine.** Markdown and PDF from one deterministic template, for comparisons and risk reports. DOCX is shown disabled ("Coming soon"). Sections are listed in SPEC §4.9. | Exports make no LLM calls. The PDF includes page references and quotes. The footer has the model, prompt versions, timestamp and disclaimer. |
| S8-8 | **Updated dashboard and sidebar.** Seven tiles and widgets (SPEC §5), a portfolio risk distribution chart, top critical findings, and the Compare Contracts sidebar entry. | Each tile number matches a direct DB query (test). The tiles link to their source pages. |

---

## Sprint 9 — Analytics, performance, security, responsive design

| Id | Story | Acceptance criteria |
| --- | --- | --- |
| S9-1 | **Analytics page.** Portfolio risk trend over time, most frequent risk types, risk by contract type, open vs. resolved findings, comparison activity. | Filters by date range and contract type. Charts use the same severity colours as the Risk Analyzer. |
| S9-2 | **Performance.** Cache retrieval results per contract and query, reuse embeddings across re-runs, cap job-queue concurrency, set token budgets per prompt, stream chat answers. | p95 time to risk report < 60 s at 50 pages. p95 time to first chat token < 3 s. LLM cost per contract is recorded. |
| S9-3 | **Security hardening.** Per-contract authorisation on every endpoint (including export and viewer assets), an upload type and size allow-list, rate limits on AI endpoints, no contract text in logs, a prompt-injection regression suite, and dependency audit. | Cross-user access tests return 403. Injection suite passes. Secrets only come from the environment. |
| S9-4 | **Responsive design.** Dashboard, Risk Analyzer and the comparison viewer (tabs mode) work at 375 px and 768 px. Charts reflow. Tables collapse into cards. | No horizontal page scroll at 375 px. Touch targets are at least 44 px. |

---

## Sprint 10 — Deployment and production readiness

| Id | Story | Acceptance criteria |
| --- | --- | --- |
| S10-1 | **Deployment.** Containerised services, environment-based config, managed DB and vector store, a separate worker process for jobs, health checks. | One-command deploy to staging. Rollback is documented and tested. |
| S10-2 | **Documentation.** User guide (Risk Analyzer, Compare, exports), admin guide (taxonomy, thresholds, adding a prompt version), API reference, architecture overview. | A new developer can add a risk type by following the docs alone. |
| S10-3 | **Final testing.** Full regression, both eval gates (S7-9, S8-3), load test (concurrent uploads), UAT with 3–5 real contracts per contract type. | All gates green. UAT issues are triaged, and none are open at Critical. |
| S10-4 | **Production readiness.** Monitoring (job failures, verifier drop rate, LLM latency and cost), alerts, backups, data-retention policy for contracts and reports, disclaimer review. | Runbook signed off. A dashboard tracks the verifier drop rate, which is the early-warning signal for hallucination regressions. |

---

## Risks to the plan

| Risk | Mitigation |
| --- | --- |
| Chunk metadata (page, clause) is missing or poor in Sprints 1–5 | S6-6 runs first in Sprint 6 and blocks S6-3 |
| Scanned PDFs without OCR break quotes and pages | Detect at upload. Show "Text not extractable — risk analysis unavailable" instead of a misleading report |
| False "missing clause" findings | Three-signal absence rule, applicability conditions, an 85% confidence cap, eval gate |
| LLM cost and latency per contract | One call per category instead of per risk type, fast-path Unchanged pairs, batched impact calls |
| Prompt drift degrades quality silently | Versioned prompts, CI eval gate, verifier drop-rate monitoring |
