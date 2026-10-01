import { api, aiService } from "../api.js";
import { $, confirmDialog, fromNow, html, icon, loc, on, overlay, render, setActions, setTitle, toast } from "../ui.js";

const SUGGESTIONS = [
  "What is the notice period for termination?",
  "How and when are invoices paid?",
  "Is liability capped?",
  "Does the contract renew automatically?",
];

export async function mount(view, params) {
  setTitle("AI Chat");
  const convId = params[0];
  const [conversations, contracts] = await Promise.all([api.get("/api/conversations"), api.get("/api/contracts")]);
  const indexed = contracts.filter((c) => c.status === "indexed");
  setActions(html`<button class="btn primary" id="new-conv">${icon.plus}New conversation</button>`);

  if (!convId && conversations.length) { location.replace(`#/chat/${conversations[0].id}`); return; }

  render(view, html`<div class="chat">
    <div class="chat-side card" style="padding:10px">
      <div class="small muted" style="padding:4px 6px">Conversations</div>
      <div class="list">${conversations.length ? conversations.map((c) => html`
        <a class="conv-item ${c.id === convId ? "active" : ""}" href="#/chat/${c.id}">
          <div class="t">${c.title}</div><div class="small muted">${c.message_count} messages · ${fromNow(c.updated_at)}</div></a>`)
        : html`<p class="muted small" style="padding:6px">No conversations yet.</p>`}</div>
    </div>
    <div class="chat-main card" id="main"></div>
  </div>`);

  $("#new-conv").addEventListener("click", () => newConversation(indexed));
  const main = $("#main", view);
  if (!convId) {
    render(main, html`<div class="empty" style="margin:auto"><h3>Ask questions about your contracts</h3>
      <p>Answers are based only on the contract text, and every answer links to the clause it comes from.</p>
      <div class="row">${indexed.length ? html`<button class="btn primary" id="start">${icon.chat}Start a conversation</button>`
        : html`<a class="btn primary" href="#/contracts?upload=1">${icon.upload}Upload a contract first</a>`}</div></div>`);
    $("#start", main)?.addEventListener("click", () => newConversation(indexed));
    return;
  }
  await conversation(main, convId);
}

function newConversation(indexed) {
  if (!indexed.length) return toast("Upload and index a contract first.", "err");
  const { box, close } = overlay(html`<h2>New conversation</h2>
    <p class="muted small">Choose the contracts to ask about. Answers only use these documents.</p>
    <div class="stack" style="gap:8px;max-height:300px;overflow-y:auto">${indexed.map((c, i) => html`
      <label class="row" style="gap:8px"><input type="checkbox" value="${c.id}" ${i === 0 ? "checked" : ""}> ${c.name}</label>`)}</div>
    <div class="row" style="justify-content:flex-end"><button class="btn" data-close>Cancel</button><button class="btn primary" id="create">Start</button></div>`);
  $("#create", box).addEventListener("click", async () => {
    const ids = [...box.querySelectorAll("input:checked")].map((i) => i.value);
    if (!ids.length) return toast("Choose at least one contract.", "err");
    try { const conv = await api.post("/api/conversations", { contract_ids: ids }); close(); location.hash = `#/chat/${conv.id}`; }
    catch (err) { toast(err.message, "err"); }
  });
}

function citeLink(c, i) {
  return html`<a class="cite" href="#/contracts/${c.contract_id}?hl=${c.start}-${c.end}">
    <span class="n">[${i + 1}]</span><span><b>${c.contract_name}</b> · ${loc(c.page, c.clause_ref)}<br><span class="muted">“${c.quote.slice(0, 220)}${c.quote.length > 220 ? "…" : ""}”</span></span></a>`;
}

function message(m) {
  const cites = m.citations || [];
  return html`<div class="msg ${m.role}"><div class="body">${m.content}</div>
    ${m.role === "assistant" && cites.length ? html`<div class="cites">${cites.map(citeLink)}</div>` : ""}
    ${m.role === "assistant" && (m.found === 0 || m.found === false) ? html`<div class="small muted" style="margin-top:6px">No supporting text found in the contract.</div>` : ""}</div>`;
}

async function conversation(main, convId) {
  let conv;
  try { conv = await api.get(`/api/conversations/${convId}`); }
  catch (err) { if (err.status === 404) { location.replace("#/chat"); return; } throw err; }
  setTitle(conv.title);
  render(main, html`
    <div class="chat-scope"><span class="small muted">Asking about:</span>${conv.contracts.map((c) => html`<a class="chip" href="#/contracts/${c.id}">${c.name}</a>`)}
      <span class="spacer"></span><button class="btn sm ghost" id="rename">Rename</button><button class="btn sm ghost danger" id="delete">${icon.trash}</button></div>
    <div class="messages" id="msgs">${conv.messages.length ? conv.messages.map(message) : html`<div class="empty" style="margin:auto">
      <h3>Ask anything about ${conv.contracts.map((c) => c.name).join(", ")}</h3>
      <div class="suggestions">${SUGGESTIONS.map((s) => html`<button class="btn sm" data-suggest="${s}">${s}</button>`)}</div></div>`}</div>
    <form class="composer" id="composer"><textarea id="q" rows="1" placeholder="Ask a question about the contract…" aria-label="Question"></textarea>
      <button class="btn primary" type="submit" id="send">${icon.send}Ask</button></form>`);
  const msgs = $("#msgs", main), q = $("#q", main), form = $("#composer", main);
  msgs.scrollTop = msgs.scrollHeight;

  const ask = async (text) => {
    text = text.trim();
    if (!text) return;
    if (!conv.messages.length) msgs.innerHTML = "";
    conv.messages.push({});
    msgs.insertAdjacentHTML("beforeend", message({ role: "user", content: text }).s);
    msgs.insertAdjacentHTML("beforeend", `<div class="msg assistant" id="pending"><span class="spinner"></span> Searching the contract…</div>`);
    msgs.scrollTop = msgs.scrollHeight;
    q.value = "";
    $("#send", main).disabled = true;
    try {
      const r = await aiService.generateAnswer(convId, text);
      $("#pending", msgs).outerHTML = message({ ...r.assistant, found: r.assistant.found }).s;
    } catch (err) {
      $("#pending", msgs).outerHTML = `<div class="msg assistant">Sorry, that failed: ${err.message.replace(/[<>&]/g, "")}</div>`;
    }
    $("#send", main).disabled = false;
    msgs.scrollTop = msgs.scrollHeight;
    q.focus();
  };
  form.addEventListener("submit", (e) => { e.preventDefault(); ask(q.value); });
  q.addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); ask(q.value); } });
  on(main, "click", "[data-suggest]", (e, b) => ask(b.dataset.suggest));
  $("#rename", main).addEventListener("click", () => {
    const { box, close } = overlay(html`<h2>Rename conversation</h2><input type="text" id="t" value="${conv.title}" maxlength="120">
      <div class="row" style="justify-content:flex-end"><button class="btn" data-close>Cancel</button><button class="btn primary" id="ok">Save</button></div>`);
    $("#ok", box).addEventListener("click", async () => {
      const title = $("#t", box).value.trim();
      if (!title) return;
      await api.patch(`/api/conversations/${convId}`, { title });
      close();
      location.reload();
    });
  });
  $("#delete", main).addEventListener("click", async () => {
    if (!(await confirmDialog("Delete conversation?", "The messages in this conversation will be removed.", "Delete", true))) return;
    await api.del(`/api/conversations/${convId}`);
    location.hash = "#/chat";
  });
  q.focus();
}
