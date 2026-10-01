import { api } from "../api.js";
import { $, confirmDialog, html, render, setTitle, toast } from "../ui.js";

export async function mount(view) {
  setTitle("Settings");
  const s = await api.get("/api/settings");
  let theme = "system";
  try { theme = localStorage.getItem("theme") || "system"; } catch { /* storage unavailable */ }
  const llm = s.llm;
  render(view, html`<div class="grid grid-2">
    <div class="card stack">
      <h2>AI provider</h2>
      ${llm.mode === "llm" ? html`<div class="notice"><span class="chip ok"><span class="dot"></span>Connected</span> Using <b>${llm.model}</b> via the Anthropic API. Refusal fallback: ${llm.fallbacks}.</div>`
        : html`<div class="notice warn"><div><b>Offline mode.</b> No LLM is configured, so risk findings, answers and comparisons come from deterministic rules.
          Every quote is still verified against the contract text.<br><br>To enable AI analysis, copy <code>.env.example</code> to <code>.env</code>, set <code>ANTHROPIC_API_KEY</code>, and restart the server.</div></div>`}
      <h3>Prompt templates</h3>
      <p class="small muted">Loaded from PROMPTS.md. Reports record the version they were produced with.</p>
      <div class="row">${Object.values(s.prompts).map((p) => html`<span class="chip mono">${p}</span>`)}</div>
    </div>
    <form class="card stack" id="risk">
      <h2>Risk analysis</h2>
      <label class="field">Minimum confidence to count a finding
        <input type="number" id="minc" min="0.1" max="0.95" step="0.05" value="${s.risk.min_confidence}">
        <span class="hint">Findings below this go to “Needs human review” and don't affect the score.</span></label>
      <label class="field">Long notice period threshold (days)
        <input type="number" id="notice" min="7" max="365" value="${s.risk.notice_days_threshold}"></label>
      <label class="field">High penalty threshold (% of value)
        <input type="number" id="pen" min="1" max="100" value="${s.risk.penalty_percent_threshold}"></label>
      <div class="small muted">Score weights: ${Object.entries(s.risk.severity_weights).map(([k, v]) => `${k} ${v}`).join(" · ")}. Missing-clause confidence is capped at ${Math.round(s.risk.absence_confidence_cap * 100)}%.</div>
      <div><button class="btn primary">Save</button> <span class="small muted">Applies to new analyses. Re-run a report to apply it there.</span></div>
    </form>
    <div class="card stack">
      <h2>Appearance</h2>
      <label class="field">Theme<select id="theme">
        ${[["system", "Match system"], ["light", "Light"], ["dark", "Dark"]].map(([v, l]) => html`<option value="${v}" ${v === theme ? "selected" : ""}>${l}</option>`)}</select></label>
    </div>
    <div class="card stack">
      <h2>Workspace</h2>
      <p class="small muted">Data folder: <span class="mono">${s.storage.data_dir}</span> · max upload ${s.storage.max_upload_mb} MB</p>
      <div><button class="btn danger" id="clear">Clear workspace</button></div>
      <p class="small muted">Deletes every contract, report, comparison and conversation. This can't be undone.</p>
    </div>
  </div>`);
  $("#risk", view).addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
      await api.put("/api/settings", { min_confidence: Number($("#minc", view).value),
        notice_days_threshold: Number($("#notice", view).value), penalty_percent_threshold: Number($("#pen", view).value) });
      toast("Settings saved.");
    } catch (err) { toast(err.message, "err"); }
  });
  $("#theme", view).addEventListener("change", (e) => {
    const v = e.target.value;
    try { v === "system" ? localStorage.removeItem("theme") : localStorage.setItem("theme", v); } catch { /* ignore */ }
    if (v === "system") delete document.documentElement.dataset.theme; else document.documentElement.dataset.theme = v;
  });
  $("#clear", view).addEventListener("click", async () => {
    if (!(await confirmDialog("Clear workspace?", "Every contract, report, comparison and conversation will be permanently deleted.", "Delete everything", true))) return;
    await api.post("/api/settings/clear-workspace");
    toast("Workspace cleared.");
    location.hash = "#/dashboard";
  });
}
