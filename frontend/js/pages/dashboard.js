import { api } from "../api.js";
import { hbar, SEV_COLOR } from "../charts.js";
import { $, disclaimer, fromNow, html, icon, loc, pct, render, riskBadge, setActions, setTitle, sevChip, toast } from "../ui.js";

export async function mount(view) {
  setTitle("Dashboard");
  setActions(html`<a class="btn primary" href="#/contracts?upload=1">${icon.upload}Upload contract</a>`);
  const d = await api.get("/api/dashboard");

  if (!d.total_contracts) {
    render(view, html`<div class="card empty">
      <h3>No contracts yet</h3>
      <p>Upload a contract (PDF, DOCX or TXT). The risk analyzer runs automatically as soon as it is indexed.</p>
      <div class="row"><a class="btn primary" href="#/contracts?upload=1">${icon.upload}Upload contract</a>
      <button class="btn" id="load-samples">Load sample contracts</button></div>
      <p class="small muted" style="margin-top:14px">The samples are fictitious: two versions of a services agreement and an NDA.</p></div>`);
    $("#load-samples", view).addEventListener("click", async (e) => {
      e.target.disabled = true;
      try { await api.post("/api/samples/load"); toast("Sample contracts loaded. Analysis is running."); location.hash = "#/contracts"; }
      catch (err) { toast(err.message, "err"); e.target.disabled = false; }
    });
    return;
  }

  const pr = d.portfolio_risk;
  render(view, html`
    <div class="tiles">
      <a class="tile" href="#/contracts"><div class="label">Total contracts</div><div class="value num">${d.total_contracts}</div><div class="foot">in your workspace</div></a>
      <a class="tile" href="#/chat"><div class="label">AI conversations</div><div class="value num">${d.ai_conversations}</div><div class="foot">grounded Q&amp;A threads</div></a>
      <a class="tile" href="#/analytics"><div class="label">Portfolio risk score</div><div class="value num">${pr.score ?? "–"}</div>
        <div class="foot">${pr.level ? sevChip(pr.level) : ""}<span>across ${pr.contracts} contract${pr.contracts === 1 ? "" : "s"}</span></div></a>
      <a class="tile" href="#/risk"><div class="label">Critical risks detected</div><div class="value num">${d.critical_risks}</div><div class="foot">open, in latest reports</div></a>
      <a class="tile" href="#/compare"><div class="label">Contracts compared</div><div class="value num">${d.contracts_compared}</div><div class="foot">completed comparisons</div></a>
    </div>
    <div class="grid grid-2">
      <div class="card">
        <div class="card-head"><h2>Portfolio risk distribution</h2><span class="sub">Counted findings in each contract's latest report</span></div>
        <div id="dist"></div>
      </div>
      <div class="card">
        <div class="card-head"><h2>Top critical and high findings</h2><a class="small" href="#/risk">Open Risk Analyzer</a></div>
        <div class="list">${d.top_findings.length ? d.top_findings.map((f) => html`
          <a class="list-item" href="#/risk/${f.contract_id}?finding=${f.id}">
            ${sevChip(f.severity)}
            <div class="grow"><div class="title">${f.title}</div><div class="meta">${f.contract_name} · ${loc(f.page, f.clause_ref)} · ${pct(f.confidence)}</div></div>
          </a>`) : html`<p class="muted small">No critical or high findings.</p>`}</div>
      </div>
      <div class="card">
        <div class="card-head"><h2>Recently uploaded contracts</h2><a class="small" href="#/contracts">All contracts</a></div>
        <div class="list">${d.recent_contracts.map((c) => html`
          <a class="list-item" href="#/contracts/${c.id}">
            <div class="grow"><div class="title">${c.name}</div><div class="meta">${c.filename} · ${fromNow(c.uploaded_at)}</div></div>
            ${c.status === "indexed" ? riskBadge(c.risk) : c.status === "failed" ? html`<span class="chip fail"><span class="dot"></span>Failed</span>` : html`<span class="chip run"><span class="dot"></span>Indexing…</span>`}
          </a>`)}</div>
      </div>
      <div class="card">
        <div class="card-head"><h2>Recent AI searches</h2><a class="small" href="#/search">Search</a></div>
        <div class="list">${d.recent_searches.length ? d.recent_searches.map((s) => html`
          <a class="list-item" href="${s.kind === "chat" ? `#/chat/${s.conversation_id}` : `#/search?q=${encodeURIComponent(s.query)}`}">
            <span class="chip">${s.kind === "chat" ? "Chat" : "Search"}</span>
            <div class="grow"><div class="title">${s.query}</div><div class="meta">${fromNow(s.created_at)}</div></div>
          </a>`) : html`<p class="muted small">No questions asked yet. Try AI Chat.</p>`}</div>
      </div>
    </div>
    ${disclaimer()}`);
  hbar($("#dist", view), Object.entries(d.risk_distribution).map(([k, v]) => ({ label: k, value: v, color: SEV_COLOR[k], metric: "Findings" })),
    { labelWidth: 80, empty: "No counted findings yet." });

  const busy = d.recent_contracts.some((c) => c.status === "processing" || (c.risk && ["queued", "running"].includes(c.risk.status)));
  if (busy) {
    const t = setTimeout(() => { if (location.hash.startsWith("#/dashboard") || !location.hash) mount(view); }, 2500);
    return () => clearTimeout(t);
  }
}
