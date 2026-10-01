import { api } from "../api.js";
import { $, esc, html, loc, raw, render, setTitle } from "../ui.js";

function snippet(text, query) {
  const terms = query.toLowerCase().split(/\W+/).filter((t) => t.length > 2);
  const lower = text.toLowerCase();
  let pos = terms.map((t) => lower.indexOf(t)).filter((i) => i >= 0).sort((a, b) => a - b)[0] ?? 0;
  const start = Math.max(0, pos - 120);
  let s = (start ? "…" : "") + text.slice(start, start + 380) + (start + 380 < text.length ? "…" : "");
  s = esc(s);
  for (const t of terms) s = s.replace(new RegExp(`(${t.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")})`, "gi"), "<mark>$1</mark>");
  return raw(s);
}

export async function mount(view, params, query) {
  setTitle("Search");
  const contracts = (await api.get("/api/contracts")).filter((c) => c.status === "indexed");
  const q = query.get("q") || "";
  const cid = query.get("contract") || "";
  render(view, html`
    <form class="card row" id="form">
      <input type="search" id="q" value="${q}" placeholder="Search clauses, e.g. “termination for convenience”" style="flex:1;min-width:220px" aria-label="Search">
      <select id="c" aria-label="Contract"><option value="">All contracts</option>${contracts.map((c) => html`<option value="${c.id}" ${c.id === cid ? "selected" : ""}>${c.name}</option>`)}</select>
      <button class="btn primary">Search</button>
    </form>
    <div class="card" style="margin-top:16px" id="results">${q ? html`<div class="loading"><span class="spinner"></span></div>`
      : html`<p class="muted">Search runs over every indexed clause, by meaning and by keyword. No AI call is made, so results are instant.</p>`}</div>`);
  $("#form", view).addEventListener("submit", (e) => {
    e.preventDefault();
    const params = new URLSearchParams({ q: $("#q", view).value.trim() });
    if ($("#c", view).value) params.set("contract", $("#c", view).value);
    location.hash = `#/search?${params}`;
  });
  if (!q) { $("#q", view).focus(); return; }
  const hits = await api.get(`/api/search?q=${encodeURIComponent(q)}${cid ? `&contract_id=${cid}` : ""}`);
  render($("#results", view), hits.length ? html`<div class="small muted">${hits.length} results</div>${hits.map((h) => html`
    <div class="result">
      <div class="row"><a href="#/contracts/${h.contract_id}?hl=${h.start}-${h.start + h.text.length}"><b>${h.contract_name}</b> · ${loc(h.page, h.clause_ref)}</a>
        ${h.heading ? html`<span class="muted small">${h.heading}</span>` : ""}<span class="spacer"></span><span class="small muted num">relevance ${Math.round(h.score * 100)}</span></div>
      <div class="snippet">${snippet(h.text, q)}</div>
    </div>`)}` : html`<div class="empty"><h3>No matching clauses</h3><p>Try different words, or ask in AI Chat.</p></div>`);
}
