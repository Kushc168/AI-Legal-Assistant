// Small SVG charts: thin marks, rounded data-ends anchored to the baseline, 2px gaps between fills,
// recessive grid, hover tooltips on every mark. Colours are CSS variables so dark mode follows.
import { esc, html, raw, render } from "./ui.js";

export const SEV_COLOR = { Critical: "var(--sev-critical)", High: "var(--sev-high)", Medium: "var(--sev-medium)", Low: "var(--sev-low)" };

let tip;
function tooltip() {
  if (!tip) {
    tip = document.createElement("div");
    tip.className = "tooltip";
    tip.hidden = true;
    document.body.appendChild(tip);
  }
  return tip;
}
function showTip(e, content) {
  const t = tooltip();
  t.innerHTML = content;
  t.hidden = false;
  const pad = 14;
  const { innerWidth: w, innerHeight: h } = window;
  const rect = t.getBoundingClientRect();
  let x = e.clientX + pad, y = e.clientY + pad;
  if (x + rect.width > w - 8) x = e.clientX - rect.width - pad;
  if (y + rect.height > h - 8) y = e.clientY - rect.height - pad;
  t.style.left = `${x}px`;
  t.style.top = `${y}px`;
}
const hideTip = () => { if (tip) tip.hidden = true; };

function bindTips(el, lookup) {
  el.addEventListener("mousemove", (e) => {
    const target = e.target.closest("[data-tip]");
    if (!target) return hideTip();
    showTip(e, lookup(target.dataset.tip));
  });
  el.addEventListener("mouseleave", hideTip);
}

// Rect with only the right (data-end) corners rounded.
function barPath(x, y, w, h, r = 4) {
  if (w <= 0) return "";
  r = Math.min(r, w, h / 2);
  return `M${x},${y}h${w - r}a${r},${r} 0 0 1 ${r},${r}v${h - 2 * r}a${r},${r} 0 0 1 -${r},${r}h-${w - r}z`;
}

/** Horizontal bars. data: [{label, value, color, note}] */
export function hbar(el, data, { unit = "", max, labelWidth = 120, empty = "No data yet" } = {}) {
  if (!data.length || data.every((d) => !d.value)) {
    render(el, html`<p class="muted small">${empty}</p>`);
    return;
  }
  const width = 520, rowH = 26, barH = 12, valueW = 44;
  const plotW = width - labelWidth - valueW;
  const top = max ?? Math.max(...data.map((d) => d.value), 1);
  const height = data.length * rowH;
  const rows = data.map((d, i) => {
    const y = i * rowH + (rowH - barH) / 2;
    const w = (d.value / top) * plotW;
    return `
      <text x="${labelWidth - 10}" y="${y + barH / 2 + 4}" text-anchor="end">${esc(d.label)}</text>
      <rect x="${labelWidth}" y="${y}" width="${plotW}" height="${barH}" rx="4" style="fill:var(--surface-3)" opacity=".55"/>
      <path d="${barPath(labelWidth, y, Math.max(w, d.value ? 3 : 0), barH)}" style="fill:${d.color || "var(--accent)"}"/>
      <text x="${width}" y="${y + barH / 2 + 4}" text-anchor="end" class="axis-label" style="fill:var(--text)">${d.value}${unit}</text>
      <rect class="hit" data-tip="${i}" x="0" y="${i * rowH}" width="${width}" height="${rowH}"/>`;
  }).join("");
  render(el, raw(`<div class="chart"><svg viewBox="0 0 ${width} ${height}" role="img" aria-label="Bar chart">${rows}</svg></div>`));
  bindTips(el, (i) => {
    const d = data[i];
    return `<b>${esc(d.label)}</b><div class="tt-row"><span>${esc(d.metric || "Count")}</span><span>${d.value}${esc(unit)}</span></div>${d.note ? `<div class="muted">${esc(d.note)}</div>` : ""}`;
  });
}

/** Stacked horizontal bars. rows: [{label, values: {key: n}}], keys: [{key, color}] */
export function stacked(el, rows, keys, { labelWidth = 110, empty = "No findings" } = {}) {
  const totals = rows.map((r) => keys.reduce((s, k) => s + (r.values[k.key] || 0), 0));
  if (!rows.length || totals.every((t) => !t)) {
    render(el, html`<p class="muted small">${empty}</p>`);
    return;
  }
  const width = 520, rowH = 28, barH = 14, valueW = 34, gap = 2;
  const plotW = width - labelWidth - valueW;
  const top = Math.max(...totals, 1);
  let svg = "";
  rows.forEach((r, i) => {
    const y = i * rowH + (rowH - barH) / 2;
    let x = labelWidth;
    svg += `<text x="${labelWidth - 10}" y="${y + barH / 2 + 4}" text-anchor="end">${esc(r.label)}</text>`;
    const present = keys.filter((k) => r.values[k.key]);
    present.forEach((k, j) => {
      const w = (r.values[k.key] / top) * plotW;
      const last = j === present.length - 1;
      const drawW = Math.max(w - (last ? 0 : gap), 2);
      svg += last
        ? `<path d="${barPath(x, y, drawW, barH)}" style="fill:${k.color}"/>`
        : `<rect x="${x}" y="${y}" width="${drawW}" height="${barH}" style="fill:${k.color}"/>`;
      x += w;
    });
    svg += `<text x="${width}" y="${y + barH / 2 + 4}" text-anchor="end" class="axis-label" style="fill:var(--text)">${totals[i]}</text>
            <rect class="hit" data-tip="${i}" x="0" y="${i * rowH}" width="${width}" height="${rowH}"/>`;
  });
  const legend = keys.map((k) => `<span><i style="background:${k.color}"></i>${esc(k.label || k.key)}</span>`).join("");
  render(el, raw(`<div class="chart"><svg viewBox="0 0 ${width} ${rows.length * rowH}" role="img" aria-label="Stacked bar chart">${svg}</svg></div><div class="legend">${legend}</div>`));
  bindTips(el, (i) => {
    const r = rows[i];
    const lines = keys.filter((k) => r.values[k.key]).map((k) =>
      `<div class="tt-row"><span><i style="display:inline-block;width:8px;height:8px;border-radius:2px;background:${k.color};margin-right:6px"></i>${esc(k.label || k.key)}</span><span>${r.values[k.key]}</span></div>`).join("");
    return `<b>${esc(r.label)}</b>${lines}<div class="tt-row muted"><span>Total</span><span>${totals[i]}</span></div>`;
  });
}

/** Line chart with crosshair tooltip. points: [{label, value, note}] */
export function line(el, points, { max = 100, unit = "", empty = "Not enough data yet" } = {}) {
  if (points.length < 1) {
    render(el, html`<p class="muted small">${empty}</p>`);
    return;
  }
  const width = 560, height = 200, left = 34, right = 12, top = 10, bottom = 26;
  const plotW = width - left - right, plotH = height - top - bottom;
  const xs = points.map((_, i) => left + (points.length === 1 ? plotW / 2 : (i / (points.length - 1)) * plotW));
  const ys = points.map((p) => top + plotH - (p.value / max) * plotH);
  let svg = "";
  [0, 25, 50, 75, 100].filter((t) => t <= max).forEach((t) => {
    const y = top + plotH - (t / max) * plotH;
    svg += `<line class="${t === 0 ? "baseline" : "gridline"}" x1="${left}" x2="${width - right}" y1="${y}" y2="${y}"/>
            <text class="axis-label" x="${left - 8}" y="${y + 4}" text-anchor="end">${t}</text>`;
  });
  const step = Math.max(1, Math.ceil(points.length / 6));
  points.forEach((p, i) => {
    if (i % step === 0 || i === points.length - 1) svg += `<text class="axis-label" x="${xs[i]}" y="${height - 6}" text-anchor="middle">${esc(p.label)}</text>`;
  });
  svg += `<polyline points="${xs.map((x, i) => `${x},${ys[i]}`).join(" ")}" fill="none" style="stroke:var(--accent)" stroke-width="2" stroke-linejoin="round"/>`;
  xs.forEach((x, i) => { svg += `<circle cx="${x}" cy="${ys[i]}" r="4" style="fill:var(--accent);stroke:var(--surface)" stroke-width="2"/>`; });
  svg += `<line id="xh" class="gridline" y1="${top}" y2="${top + plotH}" style="stroke:var(--axis)" visibility="hidden"/>`;
  svg += `<rect class="hit" x="${left}" y="${top}" width="${plotW}" height="${plotH}" data-zone="1"/>`;
  render(el, raw(`<div class="chart"><svg viewBox="0 0 ${width} ${height}" role="img" aria-label="Line chart">${svg}</svg></div>`));
  const svgEl = el.querySelector("svg");
  const xh = svgEl.querySelector("#xh");
  svgEl.addEventListener("mousemove", (e) => {
    const box = svgEl.getBoundingClientRect();
    const sx = ((e.clientX - box.left) / box.width) * width;
    let best = 0;
    xs.forEach((x, i) => { if (Math.abs(x - sx) < Math.abs(xs[best] - sx)) best = i; });
    xh.setAttribute("x1", xs[best]); xh.setAttribute("x2", xs[best]); xh.setAttribute("visibility", "visible");
    const p = points[best];
    showTip(e, `<b>${esc(p.label)}</b><div class="tt-row"><span>Score</span><span>${p.value}${esc(unit)}</span></div>${p.note ? `<div class="muted">${esc(p.note)}</div>` : ""}`);
  });
  svgEl.addEventListener("mouseleave", () => { xh.setAttribute("visibility", "hidden"); hideTip(); });
}

/** Risk score meter: hero number + banded track with a needle. */
export function gauge(score, level, bands) {
  const colors = { Low: "var(--sev-low)", Medium: "var(--sev-medium)", High: "var(--sev-high)", Critical: "var(--sev-critical)" };
  const sorted = [...bands].sort((a, b) => a.min - b.min);
  const segs = sorted.map((b, i) => {
    const end = i + 1 < sorted.length ? sorted[i + 1].min : 100;
    return `<span class="${b.level === level ? "on" : ""}" style="background:${colors[b.level]};flex:${end - b.min}" title="${esc(b.level)}: ${b.min}–${end}"></span>`;
  }).join("");
  const labels = sorted.map((b) => `<span>${esc(b.level)}</span>`).join("");
  return raw(`<div class="gauge">
    <div class="score"><b class="num">${score ?? "–"}</b><span class="muted">/ 100</span>
      <span class="chip sev sev-${esc(level || "")}" style="margin-left:6px"><span class="dot"></span>${esc(level || "Not scored")}</span></div>
    <div class="meter">${segs}${score != null ? `<i class="needle" style="left:${Math.min(100, Math.max(0, score))}%"></i>` : ""}</div>
    <div class="bands">${labels}</div></div>`);
}
