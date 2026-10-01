import { api, aiService, download } from "../api.js";
import { flash, renderDocument, scrollToEl } from "../viewer.js";
import {
  $, $$, confirmDialog, disclaimer, fromNow, html, icon, loc, on, pct, render, setActions, setTitle, STATUS_LABEL,
  statusChip, toast,
} from "../ui.js";

export async function mount(view, params, query) {
  return params[0] ? comparison(view, params[0], query) : setup(view);
}

// ---------------------------------------------------------------- setup

async function setup(view) {
  setTitle("Compare Contracts");
  const [contracts, history] = await Promise.all([api.get("/api/contracts"), api.get("/api/comparisons")]);
  const indexed = contracts.filter((c) => c.status === "indexed");
  const byId = Object.fromEntries(contracts.map((c) => [c.id, c]));
  const pairs = indexed.filter((c) => c.parent_id && byId[c.parent_id]);
  const options = (sel) => indexed.map((c) => html`<option value="${c.id}" ${c.id === sel ? "selected" : ""}>${c.name}</option>`);

  render(view, html`
    <div class="grid grid-2">
      <div class="card">
        <div class="card-head"><h2>New comparison</h2></div>
        ${indexed.length < 2 ? html`<div class="empty"><h3>You need two indexed contracts</h3><p>Upload another contract or a new version to compare.</p>
          <div class="row"><a class="btn primary" href="#/contracts?upload=1">${icon.upload}Upload contract</a></div></div>` : html`
        <div class="stack">
          <label class="field">Contract A (original)<select id="a">${options(pairs[0]?.parent_id || indexed[1]?.id)}</select></label>
          <label class="field">Contract B (revised)<select id="b">${options(pairs[0]?.id || indexed[0]?.id)}</select></label>
          <fieldset class="field" style="border:0;padding:0;margin:0"><legend style="font-weight:550;font-size:13px;margin-bottom:6px">I represent</legend>
            <div class="row">
              <label><input type="radio" name="persp" value="neutral" checked> Neutral review</label>
              <label><input type="radio" name="persp" value="party_a"> First party named</label>
              <label><input type="radio" name="persp" value="party_b"> Second party named</label>
            </div><span class="small muted">Business impact depends on whose side you are on.</span></fieldset>
          <div><button class="btn primary" id="go">${icon.compare}Compare contracts</button></div>
          <p class="small muted">Compares 14 topics by meaning, not by wording: payment terms, notice period, confidentiality, IP, liability, indemnification, renewal, governing law, termination, deliverables, service levels, warranty, pricing and responsibilities.</p>
        </div>`}
      </div>
      <div class="card">
        <div class="card-head"><h2>Version pairs</h2><span class="sub">Contracts uploaded as a new version</span></div>
        <div class="list">${pairs.length ? pairs.map((c) => html`<div class="list-item">
          <div class="grow"><div class="title">${byId[c.parent_id].name} → ${c.name}</div></div>
          <button class="btn sm" data-pair="${c.parent_id}|${c.id}">Compare</button></div>`) : html`<p class="muted small">No linked versions. Choose “new version of” when uploading.</p>`}</div>
        <div class="card-head" style="margin-top:18px"><h2>Previous comparisons</h2></div>
        <div class="list">${history.length ? history.map((h) => html`<div class="list-item">
          <a class="grow" href="#/compare/${h.id}"><div class="title">${h.name_a || "Deleted"} ↔ ${h.name_b || "Deleted"}</div>
            <div class="meta">${h.status === "complete" ? Object.entries(h.counts || {}).filter(([k]) => k !== "unchanged").map(([k, v]) => `${v} ${k}`).join(" · ") : h.status} · ${fromNow(h.created_at)}</div></a>
          <button class="icon-btn" data-del="${h.id}" title="Delete comparison" aria-label="Delete comparison">${icon.trash}</button></div>`) : html`<p class="muted small">None yet.</p>`}</div>
      </div>
    </div>`);

  const start = async (a, b) => {
    if (a === b) return toast("Choose two different contracts.", "err");
    const persp = view.querySelector("input[name=persp]:checked")?.value || "neutral";
    try { const r = await aiService.compareContracts(a, b, persp); location.hash = `#/compare/${r.id}`; }
    catch (err) { toast(err.message, "err"); }
  };
  $("#go", view)?.addEventListener("click", () => start($("#a", view).value, $("#b", view).value));
  on(view, "click", "[data-pair]", (e, b) => { const [a, bb] = b.dataset.pair.split("|"); start(a, bb); });
  on(view, "click", "[data-del]", async (e, b) => {
    if (!(await confirmDialog("Delete comparison?", "The comparison report will be removed. The contracts are not affected.", "Delete", true))) return;
    await api.del(`/api/comparisons/${b.dataset.del}`);
    setup(view);
  });
}

// ---------------------------------------------------------------- result

async function comparison(view, id, query = new URLSearchParams()) {
  const cmp = await api.get(`/api/comparisons/${id}`);
  const a = cmp.contract_a, b = cmp.contract_b;
  setTitle(`Compare · ${a?.name || "?"} ↔ ${b?.name || "?"}`);
  if (cmp.status === "queued" || cmp.status === "running") {
    const p = cmp.progress || { done: 0, total: 14 };
    render(view, html`<div class="card empty"><h3><span class="spinner"></span> Comparing contracts…</h3>
      <p>${p.done} of ${p.total} topics compared. Business impact and the summary come next.</p>
      <div class="progress" style="max-width:360px;margin:12px auto"><span style="width:${(p.done / p.total) * 100}%"></span></div></div>`);
    const t = setTimeout(() => comparison(view, id, query), 1500);
    return () => clearTimeout(t);
  }
  if (cmp.status === "failed") {
    render(view, html`<div class="card empty"><h3>Comparison failed</h3><p>${cmp.error}</p><div class="row"><a class="btn" href="#/compare">Back</a></div></div>`);
    return;
  }

  setActions(html`
    <button class="btn" data-export="md">${icon.download}Markdown</button>
    <button class="btn" data-export="pdf">${icon.download}PDF</button>
    <button class="btn" disabled title="Coming soon">DOCX</button>
    <a class="btn" href="#/compare">${icon.plus}New comparison</a>`);
  $$("[data-export]").forEach((btn) => btn.addEventListener("click", async () => {
    try { await download(`/api/comparisons/${id}/export?format=${btn.dataset.export}`, `comparison.${btn.dataset.export}`); }
    catch (err) { toast(err.message, "err"); }
  }));

  const [docA, docB] = await Promise.all([api.get(`/api/contracts/${a.id}/document`), api.get(`/api/contracts/${b.id}/document`)]);
  const changes = cmp.changes;
  const byId = Object.fromEntries(changes.map((c) => [c.id, c]));
  const counts = cmp.counts || {};
  const delta = cmp.risk_delta;
  const hlA = changes.filter((c) => c.quote_a_start != null).map((c) => ({ start: c.quote_a_start, end: c.quote_a_end, cls: c.status === "unchanged" ? "" : `m-${c.status}`, id: c.id }));
  const hlB = changes.filter((c) => c.quote_b_start != null).map((c) => ({ start: c.quote_b_start, end: c.quote_b_end, cls: c.status === "unchanged" ? "" : `m-${c.status}`, id: c.id }));
  const valueText = (c) => (c.value_a || c.value_b ? `${c.value_a || "—"} → ${c.value_b || "—"}` : "");

  render(view, html`
    ${cmp.mode === "offline" ? html`<div class="notice" style="margin-bottom:16px">Offline mode: differences come from rule-based value matching, and business impact is generic. Add an API key for semantic AI comparison.</div>` : ""}
    <div class="card">
      <div class="card-head"><h2>Contract comparison summary</h2><span class="sub">${cmp.model} · perspective: ${cmp.perspective}</span></div>
      <p class="small muted">Documents compared: <a href="#/contracts/${a.id}">${a.filename}</a> (A) and <a href="#/contracts/${b.id}">${b.filename}</a> (B)</p>
      <div class="row" style="margin:10px 0">${["modified", "added", "removed", "unchanged"].map((s) => html`${statusChip(s)}<b class="num" style="margin-right:8px">${counts[s] || 0}</b>`)}
        ${delta ? html`<span class="spacer"></span><span class="small">Risk score <b class="num">${delta.score_a}</b> → <b class="num">${delta.score_b}</b>
          <span class="muted">(${delta.score_b - delta.score_a >= 0 ? "+" : ""}${delta.score_b - delta.score_a})</span></span>` : ""}</div>
      <p>${cmp.executive_summary}</p>
      ${cmp.recommendations?.length ? html`<h3 style="margin:12px 0 6px">Recommended actions</h3><ol class="recs">${cmp.recommendations.map((r) => html`<li>${r.text}
        <div class="refs">${r.item_ids.filter((i) => byId[i]).map((i) => html`<button class="chip" data-go="${i}" style="cursor:pointer">${byId[i].category}</button>`)}</div></li>`)}</ol>` : ""}
      ${delta && (delta.new_findings.length || delta.resolved_findings.length) ? html`<h3 style="margin:12px 0 6px">Risk changes</h3>
        <div class="row">${delta.new_findings.map((f) => html`<span class="chip sev sev-${f.severity}"><span class="dot"></span>New: ${f.title}</span>`)}
        ${delta.resolved_findings.map((f) => html`<span class="chip"><span class="dot" style="background:var(--good)"></span>Resolved: ${f.title}</span>`)}</div>` : ""}
    </div>

    <div class="tabs" style="margin-top:16px"><button class="active" data-view="side">Side-by-side</button><button data-view="report">Change report</button></div>

    <div id="v-side">
      <div class="tabs cmp-tabs"><button data-tab="a">Version A</button><button class="active" data-tab="changes">Changes</button><button data-tab="b">Version B</button></div>
      <div class="cmp-layout" id="layout" data-tab="changes">
        <div class="cmp-pane a"><div class="pane-head"><span class="chip">A</span><b>${a.name}</b></div><div class="pane-body" id="paneA">${renderDocument(docA, hlA, "A")}</div></div>
        <div class="cmp-nav">
          <div class="nav-head">
            <div class="row"><button class="btn sm" id="prev" aria-label="Previous change">${icon.up}</button><button class="btn sm" id="next" aria-label="Next change">${icon.down}</button>
              <span class="spacer"></span><label class="small"><input type="checkbox" id="lock" checked> Lock scroll</label></div>
            <div class="row"><select id="f-status" style="flex:1"><option value="changed">Changes only</option><option value="all">All topics</option>
              ${["modified", "added", "removed", "unchanged"].map((s) => html`<option value="${s}">${STATUS_LABEL[s]}</option>`)}</select></div>
          </div>
          <div class="changes" id="changes"></div>
        </div>
        <div class="cmp-pane b"><div class="pane-head"><span class="chip">B</span><b>${b.name}</b></div><div class="pane-body" id="paneB">${renderDocument(docB, hlB, "B")}</div></div>
      </div>
    </div>

    <div id="v-report" hidden><div class="card">${changes.map((c) => html`<div class="change-row">
      <div><b>${c.category}</b><div style="margin-top:4px">${statusChip(c.status)}</div></div>
      <div class="vals">${valueText(c) || html`<span class="muted">–</span>`}</div>
      <div><div>${c.headline}</div>
        ${c.change_summary ? html`<div class="small muted">${c.change_summary}</div>` : ""}
        ${c.business_impact ? html`<div class="impact"><b>Business impact:</b> ${c.business_impact}
          <span class="muted">· favours ${String(c.favours || "unclear").replace("_", " ")} · risk for you ${c.risk_direction || "neutral"} · ${pct(c.impact_confidence)}</span></div>` : ""}
        <div class="small muted" style="margin-top:4px">${c.quote_a ? html`<a href="" data-go="${c.id}">A: ${loc(c.page_a, c.clause_ref_a)}</a>` : ""}
          ${c.quote_a && c.quote_b ? " · " : ""}${c.quote_b ? html`<a href="" data-go="${c.id}">B: ${loc(c.page_b, c.clause_ref_b)}</a>` : ""} · confidence ${pct(c.confidence)}</div></div>
    </div>`)}</div></div>
    ${disclaimer()}`);

  // ---- change navigator
  const paneA = $("#paneA", view), paneB = $("#paneB", view);
  let filter = "changed", current = null, list = [];
  const drawList = () => {
    list = changes.filter((c) => filter === "all" || (filter === "changed" ? c.status !== "unchanged" : c.status === filter));
    render($("#changes", view), list.length ? html`${list.map((c) => html`<div class="change-item ${c.id === current ? "active" : ""}" data-change="${c.id}" tabindex="0">
      <div class="c">${statusChip(c.status)}<span>${c.category}</span></div>
      <div class="v">${valueText(c) || c.headline}</div></div>`)}` : html`<p class="muted small" style="padding:10px">No topics match this filter.</p>`);
  };
  drawList();

  // ---- synchronised scrolling, anchored on aligned clauses (not scroll percentage)
  const lock = $("#lock", view);
  const pairs = (cmp.anchors || []).map((x) => ({ a: document.getElementById(`A-a-${x.a}`), b: document.getElementById(`B-a-${x.b}`) })).filter((p) => p.a && p.b);
  let posA = [], posB = [];
  const top = (el, c) => el.getBoundingClientRect().top - c.getBoundingClientRect().top + c.scrollTop;
  const measure = () => { posA = pairs.map((p) => top(p.a, paneA)); posB = pairs.map((p) => top(p.b, paneB)); };
  measure();
  const muteUntil = { A: 0, B: 0 };
  let programmaticUntil = 0;
  const sync = (from) => {
    if (!lock.checked || Date.now() < programmaticUntil || Date.now() < muteUntil[from]) return;
    const [src, dst, ps, pd, to] = from === "A" ? [paneA, paneB, posA, posB, "B"] : [paneB, paneA, posB, posA, "A"];
    const y = src.scrollTop + 40;
    let i = -1;
    for (let k = 0; k < ps.length; k++) if (ps[k] <= y && (i < 0 || ps[k] > ps[i])) i = k;
    const target = i < 0 ? src.scrollTop : pd[i] + (src.scrollTop - ps[i]);
    if (Math.abs(dst.scrollTop - target) > 2) { muteUntil[to] = Date.now() + 80; dst.scrollTop = target; }
  };
  paneA.addEventListener("scroll", () => sync("A"), { passive: true });
  paneB.addEventListener("scroll", () => sync("B"), { passive: true });
  window.addEventListener("resize", measure);

  const go = (changeId, instant = false) => {
    const c = byId[changeId];
    if (!c) return;
    current = changeId;
    showView("side");
    drawList();
    const elA = document.getElementById(`A-h-${changeId}`), elB = document.getElementById(`B-h-${changeId}`);
    programmaticUntil = Date.now() + 900;
    if (elA) { scrollToEl(paneA, elA, 60, instant); flash(elA); }
    if (elB) { scrollToEl(paneB, elB, 60, instant); flash(elB); }
    if (window.matchMedia("(max-width: 760px)").matches) setTab(elB ? "b" : "a");
    // Scroll only the change list (scrollIntoView would also scroll the page).
    const item = $(`[data-change="${changeId}"]`, view), listEl = $("#changes", view);
    if (item && listEl) {
      const top = item.offsetTop - listEl.offsetTop;
      if (top < listEl.scrollTop || top + item.offsetHeight > listEl.scrollTop + listEl.clientHeight) listEl.scrollTop = top - 8;
    }
  };
  const step = (dir) => {
    if (!list.length) return;
    const i = list.findIndex((c) => c.id === current);
    go(list[(i + dir + list.length) % list.length].id);
  };
  const showView = (name) => {
    $$(".tabs [data-view]", view).forEach((b) => b.classList.toggle("active", b.dataset.view === name));
    $("#v-side", view).hidden = name !== "side";
    $("#v-report", view).hidden = name !== "report";
    if (name === "side") requestAnimationFrame(measure);
  };
  const setTab = (name) => {
    $("#layout", view).dataset.tab = name;
    $$(".cmp-tabs button", view).forEach((b) => b.classList.toggle("active", b.dataset.tab === name));
    requestAnimationFrame(measure);
  };
  on(view, "click", "[data-change]", (e, el) => go(el.dataset.change));
  on(view, "keydown", "[data-change]", (e, el) => { if (e.key === "Enter") go(el.dataset.change); });
  on(view, "click", "[data-go]", (e, el) => { e.preventDefault(); go(el.dataset.go); });
  on(view, "click", ".tabs [data-view]", (e, b) => showView(b.dataset.view));
  on(view, "click", ".cmp-tabs button", (e, b) => setTab(b.dataset.tab));
  $("#prev", view).addEventListener("click", () => step(-1));
  $("#next", view).addEventListener("click", () => step(1));
  $("#f-status", view).addEventListener("change", (e) => { filter = e.target.value; drawList(); });
  const onKey = (e) => {
    if (e.target.closest("input, textarea, select")) return;
    if (e.key === "j" || e.key === "ArrowDown" && e.altKey) step(1);
    if (e.key === "k" || e.key === "ArrowUp" && e.altKey) step(-1);
  };
  document.addEventListener("keydown", onKey);
  // Deep links: ?change=<id> selects a change, ?view=report opens the change report.
  if (query.get("change") && byId[query.get("change")]) go(query.get("change"), true);
  if (query.get("view") === "report") showView("report");
  return () => { window.removeEventListener("resize", measure); document.removeEventListener("keydown", onKey); };
}
