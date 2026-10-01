import { api, aiService, download } from "../api.js";
import { gauge, hbar, SEV_COLOR, stacked } from "../charts.js";
import {
  $, disclaimer, fmtDate, html, icon, loc, on, overlay, pct, render, riskBadge, setActions, setTitle, SEVERITIES,
  sevChip, toast,
} from "../ui.js";

export async function mount(view, params, query) {
  return params[0] ? report(view, params[0], query) : portfolio(view);
}

async function portfolio(view) {
  setTitle("Risk Analyzer");
  const rows = (await api.get("/api/contracts")).filter((c) => c.status !== "failed");
  if (!rows.length) {
    render(view, html`<div class="card empty"><h3>No contracts to analyse</h3><p>Upload a contract and the risk analyzer runs automatically.</p>
      <div class="row"><a class="btn primary" href="#/contracts?upload=1">${icon.upload}Upload contract</a></div></div>`);
    return;
  }
  const order = { Critical: 0, High: 1, Medium: 2, Low: 3 };
  rows.sort((a, b) => (order[a.risk?.level] ?? 9) - (order[b.risk?.level] ?? 9) || (b.risk?.score ?? 0) - (a.risk?.score ?? 0));
  render(view, html`<div class="card">
    <div class="card-head"><h2>Contracts by risk</h2><span class="sub">Each contract is analysed automatically after upload</span></div>
    <div class="table-wrap"><table class="table"><thead><tr><th>Contract</th><th>Type</th><th>Risk</th><th class="r">Score</th></tr></thead>
    <tbody>${rows.map((c) => html`<tr class="click" data-href="#/risk/${c.id}">
      <td><b>${c.name}</b><div class="small muted">${c.filename}</div></td><td>${c.contract_type || "–"}</td>
      <td>${c.status === "processing" ? html`<span class="chip run"><span class="dot"></span>Indexing…</span>` : riskBadge(c.risk)}</td>
      <td class="r">${c.risk?.score ?? "–"}</td></tr>`)}</tbody></table></div></div>${disclaimer()}`);
  on(view, "click", "tr[data-href]", (e, tr) => { location.hash = tr.dataset.href; });
  if (rows.some((c) => c.status === "processing" || ["queued", "running"].includes(c.risk?.status))) {
    const t = setTimeout(() => portfolio(view), 2500);
    return () => clearTimeout(t);
  }
}

async function report(view, contractId, query) {
  let timer = null;
  const contract = await api.get(`/api/contracts/${contractId}`);
  setTitle(`Risk Analyzer · ${contract.name}`);
  let r;
  try { r = await api.get(`/api/contracts/${contractId}/risk-report${query.get("version") ? `?version=${query.get("version")}` : ""}`); }
  catch (err) { if (err.status !== 404) throw err; }

  const rerun = async () => {
    try { await aiService.riskAnalyzer(contractId); toast("Risk analysis started."); report(view, contractId, new URLSearchParams()); }
    catch (err) { toast(err.message, "err"); }
  };

  if (!r || r.status === "queued" || r.status === "running" || contract.status === "processing") {
    setActions("");
    const p = r?.progress || { done: [], total: 5 };
    render(view, html`<div class="card empty">
      ${contract.status === "indexed" && !r ? html`<h3>Not analysed yet</h3><p>Run the analyzer to check this contract against 23 risk types.</p>
        <div class="row"><button class="btn primary" id="run">${icon.risk}Analyse contract</button></div>`
      : html`<h3><span class="spinner"></span> Analysing ${contract.name}…</h3>
        <p>Checking ${p.done.length} of ${p.total} risk categories${p.done.length ? `: ${p.done.join(", ")} done` : ""}.</p>
        <div class="progress" style="max-width:360px;margin:12px auto"><span style="width:${(p.done.length / p.total) * 100}%"></span></div>`}
    </div>`);
    $("#run", view)?.addEventListener("click", rerun);
    if (r || contract.status === "processing") timer = setTimeout(() => report(view, contractId, query), 1500);
    return () => clearTimeout(timer);
  }

  if (r.status === "failed") {
    setActions(html`<button class="btn" id="rerun">${icon.refresh}Re-run analysis</button>`);
    render(view, html`<div class="card empty"><h3>Analysis failed</h3><p>${r.error || "Unknown error"}</p>
      <div class="row"><button class="btn primary" id="retry">${icon.refresh}Retry</button></div></div>`);
    $("#retry", view).addEventListener("click", rerun);
    $("#rerun").addEventListener("click", rerun);
    return;
  }

  setActions(html`
    <select id="ver" title="Report version">${r.versions.map((v) => html`<option value="${v.version}" ${v.id === r.id ? "selected" : ""}>Version ${v.version} · ${fmtDate(v.created_at)}</option>`)}</select>
    <button class="btn" id="rerun">${icon.refresh}Re-run</button>
    <button class="btn" data-export="md">${icon.download}Markdown</button>
    <button class="btn" data-export="pdf">${icon.download}PDF</button>
    <button class="btn" disabled title="Coming soon">DOCX</button>`);

  const all = r.findings;
  const active = all.filter((f) => f.status !== "dismissed");
  const counted = active.filter((f) => f.counted);
  const clauseFindings = counted.filter((f) => f.evidence_type === "clause");
  const absent = counted.filter((f) => f.evidence_type === "absence");
  const review = active.filter((f) => !f.counted);
  const dismissed = all.filter((f) => f.status === "dismissed");
  const critical = counted.filter((f) => f.severity === "Critical");
  const priority = counted.filter((f) => f.severity === "Critical" || f.severity === "High");
  const byId = Object.fromEntries(all.map((f) => [f.id, f]));
  const cats = r.counts?.by_category || {};
  const settings = await api.get("/api/settings");

  const findingRow = (f) => html`<tr class="click" data-finding="${f.id}">
    <td>${sevChip(f.severity)}</td><td><b>${f.title}</b><div class="small muted">${f.category} · ${f.risk_type_id}</div></td>
    <td class="small">${loc(f.page, f.clause_ref)}</td><td class="r">${pct(f.confidence)}</td></tr>`;

  render(view, html`
    ${r.status === "partial" ? html`<div class="notice warn" style="margin-bottom:16px">Some checks failed, so this report is partial:
      ${r.failed_categories.map((f) => f.category).join(", ")}. Re-run to try again.</div>` : ""}
    ${r.mode === "offline" ? html`<div class="notice" style="margin-bottom:16px">Offline mode: findings come from rule-based pattern checks. Every quote is still verified against the contract text. Add an API key for AI analysis.</div>` : ""}
    <div class="grid grid-2">
      <div class="card">
        <div class="card-head"><h2>Overall contract risk</h2><span class="sub">${r.checks_run} checks · ${r.not_applicable.length} not applicable</span></div>
        ${gauge(r.overall_score, r.overall_level, settings.risk.level_bands)}
        ${r.executive_summary ? html`<p style="margin-top:14px">${r.executive_summary}</p>` : ""}
        <p class="small muted">Analysed ${fmtDate(r.created_at, true)} · ${r.model} · ${(r.prompt_versions || []).join(", ") || "rule set v1"} · ${Math.round((r.duration_ms || 0) / 100) / 10}s</p>
      </div>
      <div class="card">
        <div class="card-head"><h2>Risk distribution</h2><span class="sub">${r.counts.counted} counted findings</span></div>
        <div id="sev-chart"></div>
        <h3 style="margin:14px 0 6px">By category</h3>
        <div id="cat-chart"></div>
      </div>
    </div>

    <div class="card" style="margin-top:16px">
      <div class="card-head"><h2>Critical findings</h2><span class="sub">${critical.length} found</span></div>
      ${critical.length ? html`<div class="grid grid-2">${critical.map((f) => html`
        <div class="finding critical" data-finding="${f.id}" tabindex="0">
          <div class="head">${sevChip(f.severity)}<h3>${f.title}</h3><span class="small muted">${pct(f.confidence)}</span></div>
          <div class="loc">${loc(f.page, f.clause_ref)} · ${f.category}</div>
          ${f.quote ? html`<div class="quote">${f.quote}</div>` : ""}
          <div class="small">${f.reason}</div>
        </div>`)}</div>` : html`<p class="muted small">No critical findings.</p>`}
    </div>

    <div class="grid grid-2" style="margin-top:16px">
      <div class="card">
        <div class="card-head"><h2>High priority clauses</h2><span class="sub">Critical and high, by severity then confidence</span></div>
        ${priority.length ? html`<div class="table-wrap"><table class="table"><thead><tr><th>Severity</th><th>Finding</th><th>Location</th><th class="r">Conf.</th></tr></thead>
          <tbody>${priority.map(findingRow)}</tbody></table></div>` : html`<p class="muted small">None.</p>`}
      </div>
      <div class="card">
        <div class="card-head"><h2>Recommended actions</h2></div>
        ${r.recommendations?.length ? html`<ol class="recs">${r.recommendations.map((rec) => html`<li>${rec.text}
          <div class="refs">${rec.item_ids.filter((i) => byId[i]).map((i) => html`<button class="chip" data-finding="${i}" style="cursor:pointer">${byId[i].title}</button>`)}</div></li>`)}</ol>`
          : html`<p class="muted small">No recommendations: no counted findings.</p>`}
      </div>
    </div>

    <div class="card" style="margin-top:16px">
      <div class="card-head"><h2>All findings</h2><span class="sub">Click a finding for details, explanation and review</span></div>
      ${clauseFindings.length ? html`<div class="table-wrap"><table class="table"><thead><tr><th>Severity</th><th>Finding</th><th>Location</th><th class="r">Conf.</th></tr></thead>
        <tbody>${clauseFindings.map(findingRow)}</tbody></table></div>` : html`<p class="muted small">No clause-level findings.</p>`}
    </div>

    <div class="grid grid-2" style="margin-top:16px">
      <div class="card">
        <div class="card-head"><h2>Not found in document</h2><span class="sub">Missing clauses: confirmed by keyword scan, search and review</span></div>
        ${absent.length ? html`<div class="table-wrap"><table class="table"><tbody>${absent.map(findingRow)}</tbody></table></div>` : html`<p class="muted small">No missing clauses detected.</p>`}
      </div>
      <div class="card">
        <div class="card-head"><h2>Needs human review</h2><span class="sub">Below the ${pct(settings.risk.min_confidence)} confidence threshold; not scored</span></div>
        ${review.length ? html`<div class="table-wrap"><table class="table"><tbody>${review.map(findingRow)}</tbody></table></div>` : html`<p class="muted small">Nothing below the threshold.</p>`}
        ${dismissed.length ? html`<h3 style="margin:14px 0 6px">Dismissed by reviewer</h3><div class="table-wrap"><table class="table"><tbody>${dismissed.map(findingRow)}</tbody></table></div>` : ""}
      </div>
    </div>

    ${r.not_applicable.length ? html`<div class="card" style="margin-top:16px"><div class="card-head"><h2>Checks not applicable</h2></div>
      <p class="small muted">${r.not_applicable.map((n) => n.name).join(" · ")}. These depend on personal data or a services contract, which this contract doesn't involve.</p></div>` : ""}
    ${disclaimer()}`);

  hbar($("#sev-chart", view), SEVERITIES.map((s) => ({ label: s, value: r.counts.by_severity[s] || 0, color: SEV_COLOR[s], metric: "Findings" })), { labelWidth: 80 });
  stacked($("#cat-chart", view), ["Financial", "Legal", "Business", "Compliance", "Operational"].map((cat) => ({ label: cat, values: cats[cat] || {} })),
    SEVERITIES.map((s) => ({ key: s, color: SEV_COLOR[s] })), { labelWidth: 90 });

  on(view, "click", "[data-finding]", (e, el) => openFinding(byId[el.dataset.finding], contractId, () => report(view, contractId, query)));
  on(view, "keydown", ".finding", (e, el) => { if (e.key === "Enter") openFinding(byId[el.dataset.finding], contractId, () => report(view, contractId, query)); });
  $("#rerun").addEventListener("click", rerun);
  $("#ver").addEventListener("change", (e) => { location.hash = `#/risk/${contractId}?version=${e.target.value}`; });
  document.querySelectorAll("[data-export]").forEach((b) => b.addEventListener("click", async () => {
    try { await download(`/api/risk-reports/${r.id}/export?format=${b.dataset.export}`, `risk-report.${b.dataset.export}`); }
    catch (err) { toast(err.message, "err"); }
  }));
  if (query.get("finding") && byId[query.get("finding")]) openFinding(byId[query.get("finding")], contractId, () => report(view, contractId, query));
}

function openFinding(f, contractId, onChange) {
  if (!f) return;
  const flags = f.flags || [];
  const { box, close } = overlay(html`
    <div class="drawer-head"><h2>${f.title}</h2><button class="icon-btn" data-close aria-label="Close">${icon.close}</button></div>
    <div class="row">${sevChip(f.severity)}<span class="chip">${f.category}</span><span class="chip">${f.risk_type_id}</span>
      <span class="chip">Confidence ${pct(f.confidence)}</span>${f.counted ? "" : html`<span class="chip">Not scored</span>`}</div>
    <div class="small muted">${loc(f.page, f.clause_ref)}${f.status !== "open" ? ` · ${f.status}` : ""}</div>
    ${f.quote ? html`<div class="quote">${f.quote}</div>
      <div><a class="btn sm" href="#/contracts/${contractId}?hl=${f.quote_start}-${f.quote_end}">${icon.doc}Open in document</a>
      <button class="btn sm" id="explain">Explain this clause</button></div>
      <div id="explanation"></div>`
    : html`<div class="notice">Not found in the document. Searched for: ${(f.search_queries || []).slice(0, 8).join(", ")}</div>`}
    <dl class="kv">
      <dt>What it says</dt><dd>${f.explanation}</dd>
      <dt>Why it matters</dt><dd>${f.reason}</dd>
      <dt>Suggested improvement</dt><dd>${f.suggested_improvement}</dd>
      ${f.severity_reason ? html`<dt>Severity</dt><dd>${f.severity_reason}</dd>` : ""}
    </dl>
    ${flags.length ? html`<div class="notice warn small">Verifier notes: ${flags.map((x) => x.replace(/_/g, " ")).join(", ")}.</div>` : ""}
    <label class="field">Reviewer note<textarea id="note" rows="3" placeholder="Why you accepted or dismissed this finding">${f.reviewer_note || ""}</textarea></label>
    <div class="row">
      ${f.status !== "accepted" ? html`<button class="btn" data-status="accepted">Accept risk</button>` : ""}
      ${f.status !== "dismissed" ? html`<button class="btn" data-status="dismissed">Dismiss</button>` : ""}
      ${f.status !== "open" ? html`<button class="btn" data-status="open">Reopen</button>` : ""}
      <span class="spacer"></span><button class="btn primary" data-status="${f.status}">Save note</button>
    </div>
    <p class="disclaimer">Dismissed findings are excluded from the score. The AI's original assessment is kept.</p>`, { drawer: true });

  on(box, "click", "[data-status]", async (e, b) => {
    try {
      await api.patch(`/api/risk-findings/${f.id}`, { status: b.dataset.status, reviewer_note: $("#note", box).value });
      toast("Finding updated.");
      close();
      onChange();
    } catch (err) { toast(err.message, "err"); }
  });
  $("#explain", box)?.addEventListener("click", async (e) => {
    e.target.disabled = true;
    const out = $("#explanation", box);
    render(out, html`<div class="loading"><span class="spinner"></span></div>`);
    try {
      const x = await aiService.explainClause(f.chunk_id);
      render(out, html`<div class="card" style="box-shadow:none">
        <h3 style="margin-bottom:6px">Plain-language explanation ${x.mode === "offline" ? html`<span class="chip offline">Offline</span>` : ""}</h3>
        <p>${x.plain_language}</p>
        ${x.obligations.length ? html`<dl class="kv">${x.obligations.map((o) => html`<dt>${o.party}</dt><dd>
          ${o.must.length ? html`<div><b>Must:</b> ${o.must.join("; ")}</div>` : ""}
          ${o.may.length ? html`<div><b>May:</b> ${o.may.join("; ")}</div>` : ""}
          ${o.must_not.length ? html`<div><b>Must not:</b> ${o.must_not.join("; ")}</div>` : ""}</dd>`)}</dl>` : ""}
        ${x.watch_outs.length ? html`<h3 style="margin:10px 0 4px">Watch out for</h3><ul style="margin:0;padding-left:18px">${x.watch_outs.map((w) => html`<li>${w}</li>`)}</ul>` : ""}
      </div>`);
    } catch (err) { render(out, html`<div class="notice err">${err.message}</div>`); e.target.disabled = false; }
  });
}
