import { api } from "../api.js";
import { hbar, line, SEV_COLOR, stacked } from "../charts.js";
import { $, disclaimer, html, render, setTitle, SEVERITIES } from "../ui.js";

export async function mount(view) {
  setTitle("Analytics");
  const a = await api.get("/api/analytics");
  if (!a.contracts_analysed) {
    render(view, html`<div class="card empty"><h3>No analysed contracts yet</h3><p>Analytics appear once at least one risk report is complete.</p></div>`);
    return;
  }
  const status = a.findings_by_status || {};
  render(view, html`
    <div class="tiles">
      <div class="tile"><div class="label">Contracts analysed</div><div class="value num">${a.contracts_analysed}</div></div>
      <div class="tile"><div class="label">Findings citing a clause</div><div class="value num">${a.evidence.clause}</div><div class="foot">verbatim quote verified</div></div>
      <div class="tile"><div class="label">Missing-clause findings</div><div class="value num">${a.evidence.absence}</div><div class="foot">confirmed by 3 checks</div></div>
      <div class="tile"><div class="label">Reviewed findings</div><div class="value num">${(status.accepted || 0) + (status.dismissed || 0)}</div>
        <div class="foot">${status.accepted || 0} accepted · ${status.dismissed || 0} dismissed</div></div>
    </div>
    <div class="grid grid-2">
      <div class="card"><div class="card-head"><h2>Portfolio risk trend</h2><span class="sub">Average latest score per contract, by analysis date</span></div><div id="trend"></div></div>
      <div class="card"><div class="card-head"><h2>Most frequent risks</h2><span class="sub">Counted findings across latest reports</span></div><div id="frequent"></div></div>
      <div class="card"><div class="card-head"><h2>Risk by category</h2></div><div id="cat"></div></div>
      <div class="card"><div class="card-head"><h2>Average risk by contract type</h2></div><div id="types"></div></div>
      <div class="card"><div class="card-head"><h2>Comparisons over time</h2></div><div id="cmp"></div></div>
    </div>
    ${disclaimer()}`);
  line($("#trend", view), a.trend.map((t) => ({ label: t.day.slice(5), value: t.score, note: `${t.contracts} contracts` })));
  hbar($("#frequent", view), a.frequent_risks.map((r) => ({ label: r.name, value: r.count, metric: "Contracts flagged", note: r.risk_type_id })), { labelWidth: 190 });
  stacked($("#cat", view), ["Financial", "Legal", "Business", "Compliance", "Operational"].map((c) => ({ label: c, values: a.by_category[c] || {} })),
    SEVERITIES.map((s) => ({ key: s, color: SEV_COLOR[s] })), { labelWidth: 90 });
  hbar($("#types", view), a.risk_by_contract_type.map((t) => ({ label: t.contract_type, value: t.avg_score, metric: "Average score", note: `${t.contracts} contracts` })), { max: 100, labelWidth: 100 });
  hbar($("#cmp", view), a.comparisons.map((c) => ({ label: c.day, value: c.n, metric: "Comparisons" })), { labelWidth: 100, empty: "No comparisons yet." });
}
