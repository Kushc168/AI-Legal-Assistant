// UI helpers. All interpolated values in html`` are escaped unless wrapped with raw().
// Contract text is untrusted input, so never build markup by string concatenation elsewhere.

const ESC = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
export const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ESC[c]);

class Raw { constructor(s) { this.s = s; } toString() { return this.s; } }
export const raw = (s) => new Raw(String(s));

function fmt(v) {
  if (v === null || v === undefined || v === false) return "";
  if (Array.isArray(v)) return v.map(fmt).join("");
  if (v instanceof Raw) return v.s;
  return esc(v);
}

export function html(strings, ...values) {
  let out = "";
  strings.forEach((s, i) => { out += s + (i < values.length ? fmt(values[i]) : ""); });
  return new Raw(out);
}

export function render(el, tpl) { el.innerHTML = tpl instanceof Raw ? tpl.s : esc(tpl); return el; }
export const $ = (sel, root = document) => root.querySelector(sel);
export const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

export function on(root, event, selector, handler) {
  root.addEventListener(event, (e) => {
    const target = e.target.closest(selector);
    if (target && root.contains(target)) handler(e, target);
  });
}

// ---------- formatting ----------
export function fmtDate(iso, withTime = false) {
  if (!iso) return "";
  const d = new Date(iso);
  const opts = { day: "numeric", month: "short", year: "numeric" };
  if (withTime) Object.assign(opts, { hour: "2-digit", minute: "2-digit" });
  return d.toLocaleString(undefined, opts);
}
export function fromNow(iso) {
  if (!iso) return "";
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
  return fmtDate(iso);
}
export const pct = (v) => `${Math.round((v || 0) * 100)}%`;
export const fileSize = (b) => (b > 1048576 ? `${(b / 1048576).toFixed(1)} MB` : `${Math.max(1, Math.round(b / 1024))} KB`);
export function loc(page, clause) {
  const parts = [];
  if (page) parts.push(`Page ${page}`);
  if (clause) parts.push(/^[A-Z][a-z]+ /.test(clause) ? clause : `Clause ${clause}`);
  return parts.join(" · ") || "Not found in document";
}

// ---------- badges ----------
export const SEVERITIES = ["Critical", "High", "Medium", "Low"];
export const sevChip = (s) => html`<span class="chip sev sev-${s}"><span class="dot"></span>${s}</span>`;
export const STATUS_LABEL = { modified: "Modified", added: "Added", removed: "Removed", unchanged: "Unchanged" };
export const statusChip = (s) => html`<span class="chip st-${s}"><span class="dot"></span>${STATUS_LABEL[s] || s}</span>`;

export function riskBadge(risk) {
  if (!risk) return html`<span class="chip">Not analysed</span>`;
  if (risk.status === "queued" || risk.status === "running") return html`<span class="chip run"><span class="dot"></span>Analyzing…</span>`;
  if (risk.status === "failed") return html`<span class="chip fail"><span class="dot"></span>Analysis failed</span>`;
  const level = risk.level || risk.overall_level;
  const score = risk.score ?? risk.overall_score;
  return html`<span class="chip sev sev-${level}"><span class="dot"></span>${level} · ${score}</span>`;
}

export function contractStatus(c) {
  if (c.status === "processing") return html`<span class="chip run"><span class="dot"></span>Indexing…</span>`;
  if (c.status === "failed") return html`<span class="chip fail"><span class="dot"></span>Failed</span>`;
  return html`<span class="chip ok"><span class="dot"></span>Indexed</span>`;
}

export const disclaimer = () => html`<p class="disclaimer">AI-assisted review, not legal advice. Check every finding against the contract text.</p>`;

// ---------- feedback ----------
export function toast(message, kind = "") {
  const el = document.createElement("div");
  el.className = `toast ${kind}`;
  el.textContent = message;
  document.getElementById("toasts").appendChild(el);
  setTimeout(() => el.remove(), kind === "err" ? 6000 : 3500);
}

export function overlay(content, { drawer = false, onClose } = {}) {
  const root = document.getElementById("overlay-root");
  const wrap = document.createElement("div");
  wrap.className = `backdrop ${drawer ? "" : "modal-wrap"}`;
  const box = document.createElement("div");
  box.className = drawer ? "drawer" : "modal";
  box.setAttribute("role", "dialog");
  box.setAttribute("aria-modal", "true");
  render(box, content);
  wrap.appendChild(box);
  root.appendChild(wrap);
  const close = () => { wrap.remove(); document.removeEventListener("keydown", onKey); onClose && onClose(); };
  const onKey = (e) => { if (e.key === "Escape") close(); };
  document.addEventListener("keydown", onKey);
  wrap.addEventListener("mousedown", (e) => { if (e.target === wrap) close(); });
  on(box, "click", "[data-close]", close);
  const focusable = box.querySelector("input, textarea, select, button:not([data-close])");
  (focusable || box).focus?.();
  return { box, close };
}

export function confirmDialog(title, message, confirmLabel = "Confirm", danger = false) {
  return new Promise((resolve) => {
    let done = false;
    const { box, close } = overlay(html`
      <h2>${title}</h2><p class="muted">${message}</p>
      <div class="row" style="justify-content:flex-end">
        <button class="btn" data-close>Cancel</button>
        <button class="btn ${danger ? "danger" : "primary"}" data-ok>${confirmLabel}</button>
      </div>`, { onClose: () => { if (!done) resolve(false); } });
    box.querySelector("[data-ok]").addEventListener("click", () => { done = true; close(); resolve(true); });
  });
}

export const icon = {
  upload: raw('<svg viewBox="0 0 24 24"><path d="M12 16V4m0 0-5 5m5-5 5 5M4 16v4h16v-4"/></svg>'),
  download: raw('<svg viewBox="0 0 24 24"><path d="M12 4v12m0 0-5-5m5 5 5-5M4 20h16"/></svg>'),
  refresh: raw('<svg viewBox="0 0 24 24"><path d="M20 11a8 8 0 1 0-2.3 5.7M20 4v7h-7"/></svg>'),
  chat: raw('<svg viewBox="0 0 24 24"><path d="M4 5h16v11H9l-5 4z"/></svg>'),
  risk: raw('<svg viewBox="0 0 24 24"><path d="M12 3 2 20h20zM12 10v4m0 3v.5"/></svg>'),
  compare: raw('<svg viewBox="0 0 24 24"><path d="M4 4h6v16H4zm10 0h6v16h-6z"/></svg>'),
  trash: raw('<svg viewBox="0 0 24 24"><path d="M4 7h16M10 11v6m4-6v6M6 7l1 13h10l1-13M9 7V4h6v3"/></svg>'),
  close: raw('<svg viewBox="0 0 24 24"><path d="M6 6l12 12M18 6 6 18"/></svg>'),
  doc: raw('<svg viewBox="0 0 24 24"><path d="M6 2h8l6 6v14H6zm8 1.5V9h5.5"/></svg>'),
  up: raw('<svg viewBox="0 0 24 24"><path d="m6 15 6-6 6 6"/></svg>'),
  down: raw('<svg viewBox="0 0 24 24"><path d="m6 9 6 6 6-6"/></svg>'),
  send: raw('<svg viewBox="0 0 24 24"><path d="M4 12 20 4l-6 16-3-7z"/></svg>'),
  plus: raw('<svg viewBox="0 0 24 24"><path d="M12 5v14M5 12h14"/></svg>'),
};

export function setActions(tpl) { render(document.getElementById("topbar-actions"), tpl || ""); }
export function setTitle(t) { document.getElementById("page-title").textContent = t; document.title = `${t} · AI Legal Assistant`; }
