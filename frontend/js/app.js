import { api } from "./api.js";
import { $$, html, render, setActions, setTitle } from "./ui.js";

const routes = {
  dashboard: () => import("./pages/dashboard.js"),
  contracts: () => import("./pages/contracts.js"),
  chat: () => import("./pages/chat.js"),
  risk: () => import("./pages/risk.js"),
  compare: () => import("./pages/compare.js"),
  search: () => import("./pages/search.js"),
  analytics: () => import("./pages/analytics.js"),
  profile: () => import("./pages/profile.js"),
  settings: () => import("./pages/settings.js"),
};

let cleanup = null;
let navToken = 0;

function parseHash() {
  const hash = location.hash.replace(/^#\/?/, "") || "dashboard";
  const [path, qs] = hash.split("?");
  const [name, ...params] = path.split("/").filter(Boolean);
  return { name: routes[name] ? name : "dashboard", params: params.map(decodeURIComponent), query: new URLSearchParams(qs || "") };
}

async function navigate() {
  const token = ++navToken;
  if (typeof cleanup === "function") { try { cleanup(); } catch { /* ignore */ } }
  cleanup = null;
  const { name, params, query } = parseHash();
  $$("#nav a").forEach((a) => a.classList.toggle("active", a.dataset.route === name));
  document.getElementById("sidebar").classList.remove("open");
  const view = document.getElementById("view");
  setActions("");
  render(view, html`<div class="loading"><span class="spinner"></span></div>`);
  try {
    const mod = await routes[name]();
    if (token !== navToken) return;
    const result = await mod.mount(view, params, query, () => token !== navToken);
    if (token !== navToken) { if (typeof result === "function") result(); return; }
    cleanup = result;
    view.focus({ preventScroll: true });
  } catch (err) {
    if (token !== navToken) return;
    console.error(err);
    setTitle("Something went wrong");
    render(view, html`<div class="card empty"><h3>This page could not be loaded</h3><p>${err.message}</p>
      <div class="row"><a class="btn" href="#/dashboard">Go to dashboard</a></div></div>`);
  }
}

async function showMode() {
  try {
    const { mode } = await api.get("/api/health");
    const el = document.getElementById("mode-badge");
    render(el, mode === "llm"
      ? html`<span class="chip ok"><span class="dot"></span>AI connected</span>`
      : html`<span class="chip offline" title="No LLM configured. Results come from deterministic rules."><span class="dot" style="background:var(--muted)"></span>Offline mode</span>
             <div class="small muted" style="margin-top:6px">Add an API key in .env for AI answers.</div>`);
  } catch { /* server unreachable; pages will show errors */ }
}

document.getElementById("menu-btn").addEventListener("click", () => document.getElementById("sidebar").classList.toggle("open"));
window.addEventListener("hashchange", navigate);
showMode();
navigate();
