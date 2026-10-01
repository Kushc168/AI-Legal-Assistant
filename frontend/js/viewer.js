// Document viewer: renders extracted pages with highlight ranges and clause anchors.
// Offsets are character positions into the contract's full_text (pages joined by "\n\n").
import { esc, raw } from "./ui.js";

/**
 * @param doc {pages:[{page,start,end,text}], chunks:[{id,start_offset}]}
 * @param highlights [{start, end, cls, id}]
 * @param prefix id prefix so two viewers on one page don't collide
 */
export function renderDocument(doc, highlights = [], prefix = "d") {
  const out = [];
  const sorted = [...highlights].filter((h) => h.start != null && h.end > h.start).sort((a, b) => a.start - b.start);
  for (const page of doc.pages) {
    const points = new Set([page.start, page.end]);
    const anchors = new Map();
    for (const c of doc.chunks) {
      if (c.start_offset >= page.start && c.start_offset < page.end) {
        points.add(c.start_offset);
        anchors.set(c.start_offset, c.id);
      }
    }
    const inPage = sorted.filter((h) => h.end > page.start && h.start < page.end);
    for (const h of inPage) {
      points.add(Math.max(h.start, page.start));
      points.add(Math.min(h.end, page.end));
    }
    const cuts = [...points].sort((a, b) => a - b);
    let body = "";
    const opened = new Set();
    for (let i = 0; i < cuts.length - 1; i++) {
      const s = cuts[i], e = cuts[i + 1];
      if (anchors.has(s)) body += `<span class="anchor" id="${prefix}-a-${esc(anchors.get(s))}" data-chunk="${esc(anchors.get(s))}"></span>`;
      const text = esc(page.text.slice(s - page.start, e - page.start));
      const active = inPage.filter((h) => h.start <= s && h.end >= e);
      if (active.length) {
        // Overlapping highlights: every id gets a scroll target; the mark lists all ids it belongs to.
        for (const h of active) {
          if (h.id && !opened.has(h.id)) {
            opened.add(h.id);
            body += `<span class="anchor" id="${prefix}-h-${esc(h.id)}" data-h="${esc(h.id)}"></span>`;
          }
        }
        const top = active[active.length - 1];
        body += `<mark class="${esc(top.cls || "")}" data-h="${esc(active.map((h) => h.id || "").join(" "))}">${text}</mark>`;
      } else {
        body += text;
      }
    }
    out.push(`<section class="doc-page" data-page="${page.page}" id="${prefix}-p-${page.page}">
      <span class="pno">Page ${page.page}</span><div class="doc-text">${body}</div></section>`);
  }
  if (!out.length) return raw(`<p class="muted">No text was extracted from this document.</p>`);
  return raw(`<div class="doc">${out.join("")}</div>`);
}

export function scrollToEl(container, el, offset = 60, instant = false) {
  if (!el) return;
  const cRect = container.getBoundingClientRect();
  const eRect = el.getBoundingClientRect();
  container.scrollTo({ top: container.scrollTop + eRect.top - cRect.top - offset, behavior: instant ? "auto" : "smooth" });
}

export function flash(el) {
  if (!el) return;
  const marks = el.dataset.h ? el.closest(".doc").querySelectorAll(`mark[data-h~="${CSS.escape(el.dataset.h)}"]`) : [el];
  marks.forEach((m) => { m.classList.remove("flash"); void m.offsetWidth; m.classList.add("flash"); });
}
