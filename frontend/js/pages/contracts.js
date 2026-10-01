import { api, aiService } from "../api.js";
import { renderDocument, flash } from "../viewer.js";
import {
  $, confirmDialog, contractStatus, disclaimer, fileSize, fmtDate, fromNow, html, icon, loc, on, overlay, render,
  riskBadge, setActions, setTitle, toast,
} from "../ui.js";

export async function mount(view, params, query) {
  return params[0] ? detail(view, params[0], query) : list(view, query);
}

// ---------------------------------------------------------------- list + upload

async function list(view, query) {
  setTitle("Contracts");
  setActions(html`<button class="btn primary" id="upload-btn">${icon.upload}Upload contract</button>`);
  let timer = null;

  async function refresh() {
    const rows = await api.get("/api/contracts");
    render($("#table", view), rows.length ? html`
      <div class="table-wrap"><table class="table">
        <thead><tr><th>Contract</th><th>Type</th><th class="r">Pages</th><th>Status</th><th>Risk</th><th>Uploaded</th></tr></thead>
        <tbody>${rows.map((c) => html`
          <tr class="click" data-href="#/contracts/${c.id}">
            <td><b>${c.name}</b><div class="small muted">${c.filename} · ${fileSize(c.size_bytes)}${c.parent_id ? " · new version" : ""}</div>
              ${c.status === "failed" ? html`<div class="small" style="color:var(--sev-critical)">${c.error}</div>` : ""}</td>
            <td>${c.contract_type || "–"}</td>
            <td class="r">${c.page_count || "–"}</td>
            <td>${contractStatus(c)}</td>
            <td>${c.status === "indexed" ? html`<a href="#/risk/${c.id}">${riskBadge(c.risk)}</a>` : ""}</td>
            <td class="small muted">${fromNow(c.uploaded_at)}</td>
          </tr>`)}</tbody></table></div>`
      : html`<div class="empty"><h3>No contracts yet</h3><p>Drop a file above, or load the sample contracts.</p>
          <div class="row"><button class="btn" id="load-samples">Load sample contracts</button></div></div>`);
    const busy = rows.some((c) => c.status === "processing" || (c.risk && ["queued", "running"].includes(c.risk.status)));
    clearTimeout(timer);
    if (busy) timer = setTimeout(refresh, 2000);
    return rows;
  }

  render(view, html`
    <div class="dropzone" id="drop">
      ${icon.upload}
      <div><b>Drop a contract here</b> or <a href="" id="browse">choose a file</a></div>
      <div class="small muted">PDF, DOCX or TXT · scanned PDFs need OCR first · the risk analyzer runs automatically</div>
      <input type="file" id="file" accept=".pdf,.docx,.txt,.md" hidden>
    </div>
    <div class="card" style="margin-top:16px"><div id="table"><div class="loading"><span class="spinner"></span></div></div></div>
    ${disclaimer()}`);

  const fileInput = $("#file", view);
  const drop = $("#drop", view);
  const pick = (e) => { e.preventDefault(); fileInput.click(); };
  $("#browse", view).addEventListener("click", pick);
  $("#upload-btn").addEventListener("click", pick);
  fileInput.addEventListener("change", () => fileInput.files[0] && askUpload(fileInput.files[0]));
  drop.addEventListener("dragover", (e) => { e.preventDefault(); drop.classList.add("drag"); });
  drop.addEventListener("dragleave", () => drop.classList.remove("drag"));
  drop.addEventListener("drop", (e) => { e.preventDefault(); drop.classList.remove("drag"); e.dataTransfer.files[0] && askUpload(e.dataTransfer.files[0]); });
  on(view, "click", "tr[data-href]", (e, tr) => { if (!e.target.closest("a")) location.hash = tr.dataset.href; });
  on(view, "click", "#load-samples", async (e, btn) => {
    btn.disabled = true;
    try { await api.post("/api/samples/load"); toast("Sample contracts loaded. Analysis is running."); refresh(); }
    catch (err) { toast(err.message, "err"); btn.disabled = false; }
  });

  async function askUpload(file) {
    const existing = (await api.get("/api/contracts")).filter((c) => c.status === "indexed");
    const name = file.name.replace(/\.[^.]+$/, "").replace(/_/g, " ");
    const { box, close } = overlay(html`
      <h2>Upload contract</h2>
      <p class="muted small">${file.name} · ${fileSize(file.size)}</p>
      <label class="field">Name<input type="text" id="u-name" value="${name}"></label>
      <label class="field">Is this a new version of an existing contract?
        <select id="u-parent"><option value="">No, it's a new contract</option>
          ${existing.map((c) => html`<option value="${c.id}">${c.name}</option>`)}</select>
        <span class="hint">Linking versions adds a "Compare with previous version" shortcut.</span></label>
      <div class="row" style="justify-content:flex-end"><button class="btn" data-close>Cancel</button><button class="btn primary" id="u-go">${icon.upload}Upload and analyse</button></div>`);
    $("#u-go", box).addEventListener("click", async (e) => {
      e.target.disabled = true;
      const form = new FormData();
      form.append("file", file);
      form.append("name", $("#u-name", box).value.trim());
      if ($("#u-parent", box).value) form.append("parent_id", $("#u-parent", box).value);
      try {
        await api.post("/api/contracts", form);
        close();
        toast("Uploaded. Indexing and risk analysis are running.");
        refresh();
      } catch (err) { toast(err.message, "err"); e.target.disabled = false; }
    });
    fileInput.value = "";
  }

  await refresh();
  if (query.get("upload")) fileInput.click();
  return () => clearTimeout(timer);
}

// ---------------------------------------------------------------- detail

async function detail(view, id, query) {
  let timer = null;
  const c = await api.get(`/api/contracts/${id}`);
  setTitle(c.name);
  const prev = c.versions.find((v) => v.relation === "previous version");
  setActions(html`
    ${c.status === "indexed" ? html`<button class="btn" id="ask">${icon.chat}Ask AI</button>
    <a class="btn" href="#/risk/${id}">${icon.risk}Risk report</a>
    ${prev ? html`<button class="btn" id="cmp-prev">${icon.compare}Compare with previous version</button>` : ""}` : ""}
    <button class="btn ghost danger" id="del" title="Delete contract">${icon.trash}</button>`);

  if (c.status !== "indexed") {
    render(view, html`<div class="card empty">
      ${c.status === "processing" ? html`<h3><span class="spinner"></span> Indexing ${c.filename}…</h3><p>Extracting text, chunking clauses and building the search index.</p>`
        : html`<h3>Indexing failed</h3><p>${c.error}</p><div class="row"><button class="btn" id="reindex">${icon.refresh}Re-index</button></div>`}</div>`);
    if (c.status === "processing") timer = setTimeout(() => detail(view, id, query), 1500);
  } else {
    const doc = await api.get(`/api/contracts/${id}/document`);
    const s = c.summary;
    const hl = query.get("hl");
    const highlights = [];
    if (hl) { const [a, b] = hl.split("-").map(Number); highlights.push({ start: a, end: b, cls: "", id: "target" }); }
    render(view, html`
      <div class="tabs" role="tablist"><button class="active" data-tab="summary">Summary</button><button data-tab="document">Document</button></div>
      <div id="tab-summary">
        <div class="grid grid-2">
          <div class="card">
            <div class="card-head"><h2>Overview</h2>${s && s.mode === "offline" ? html`<span class="chip offline">Offline summary</span>` : ""}</div>
            <dl class="kv">
              <dt>File</dt><dd>${c.filename} · ${fileSize(c.size_bytes)} · ${c.page_count} page${c.page_count === 1 ? "" : "s"}</dd>
              <dt>Contract type</dt><dd>${c.contract_type || "–"}</dd>
              <dt>Personal data</dt><dd>${c.involves_personal_data ? "Yes — involves personal data" : "Not detected"}</dd>
              <dt>Parties</dt><dd>${s && s.parties.length ? s.parties.map((p) => html`<div>${p.name} <span class="muted">(${p.role})</span></div>`) : html`<span class="muted">Not identified</span>`}</dd>
              <dt>Uploaded</dt><dd>${fmtDate(c.uploaded_at, true)}</dd>
              <dt>Versions</dt><dd>${c.versions.length ? c.versions.map((v) => html`<div><a href="#/contracts/${v.id}">${v.name}</a> <span class="muted">(${v.relation})</span></div>`) : html`<span class="muted">No linked versions</span>`}</dd>
              <dt>Risk</dt><dd><a href="#/risk/${id}">${riskBadge(c.risk && { ...c.risk, score: c.risk.overall_score, level: c.risk.overall_level })}</a></dd>
            </dl>
          </div>
          <div class="card">
            <div class="card-head"><h2>Key terms</h2><button class="btn sm" id="resum">${icon.refresh}Regenerate</button></div>
            ${s ? html`<dl class="kv">${Object.entries(s.fields).map(([k, f]) => html`
              <dt>${k.replace(/_/g, " ").replace(/^./, (m) => m.toUpperCase())}</dt>
              <dd>${f ? html`${f.text} <a class="small" href="" data-jump="${f.chunk_ids[0]}">${loc(f.page, f.clause_ref)}</a>` : html`<span class="muted">Not addressed</span>`}</dd>`)}</dl>`
              : html`<p class="muted">Summary not available yet.</p>`}
          </div>
        </div>
        ${disclaimer()}
      </div>
      <div id="tab-document" hidden>
        <div class="row" style="margin-bottom:12px"><span class="muted small">${doc.pages.length} pages · ${doc.chunks.length} clauses indexed</span><span class="spacer"></span>
          <a class="btn sm" href="/api/contracts/${id}/file">${icon.download}Original file</a></div>
        <div id="doc">${renderDocument(doc, highlights, "d")}</div>
      </div>`);

    const showTab = (name) => {
      view.querySelectorAll(".tabs button").forEach((b) => b.classList.toggle("active", b.dataset.tab === name));
      $("#tab-summary", view).hidden = name !== "summary";
      $("#tab-document", view).hidden = name !== "document";
    };
    on(view, "click", ".tabs button", (e, b) => showTab(b.dataset.tab));
    on(view, "click", "[data-jump]", (e, a) => {
      e.preventDefault();
      showTab("document");
      const el = document.getElementById(`d-a-${a.dataset.jump}`);
      if (el) el.scrollIntoView({ behavior: "smooth", block: "center" });
    });
    $("#resum", view).addEventListener("click", async (e) => {
      e.target.disabled = true;
      try { await aiService.summarizeContract(id); toast("Summary regenerated."); detail(view, id, query); }
      catch (err) { toast(err.message, "err"); e.target.disabled = false; }
    });
    if (hl || query.get("page") || query.get("tab") === "document") {
      showTab("document");
      requestAnimationFrame(() => {
        const target = document.getElementById("d-h-target") || document.getElementById(`d-p-${query.get("page")}`);
        if (target) { target.scrollIntoView({ block: "center" }); flash(target); }
      });
    }
  }

  $("#reindex")?.addEventListener("click", async () => { await api.post(`/api/contracts/${id}/reindex`); detail(view, id, query); });
  $("#ask")?.addEventListener("click", async () => {
    try { const conv = await api.post("/api/conversations", { contract_ids: [id] }); location.hash = `#/chat/${conv.id}`; }
    catch (err) { toast(err.message, "err"); }
  });
  $("#cmp-prev")?.addEventListener("click", async () => {
    try { const r = await aiService.compareContracts(prev.id, id, "neutral"); location.hash = `#/compare/${r.id}`; }
    catch (err) { toast(err.message, "err"); }
  });
  $("#del").addEventListener("click", async () => {
    if (!(await confirmDialog("Delete contract?", `This permanently deletes ${c.name}, its risk reports and comparisons.`, "Delete", true))) return;
    await api.del(`/api/contracts/${id}`);
    toast("Contract deleted.");
    location.hash = "#/contracts";
  });
  return () => clearTimeout(timer);
}
