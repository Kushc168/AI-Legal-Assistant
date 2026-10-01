import { api } from "../api.js";
import { $, html, render, setTitle, toast } from "../ui.js";

const PERSPECTIVES = [
  ["neutral", "Neutral (flag one-sided terms for either party)"],
  ["customer", "Customer / buyer"],
  ["supplier", "Supplier / vendor"],
  ["employer", "Employer"],
  ["employee", "Employee"],
];

export async function mount(view) {
  setTitle("Profile");
  const p = await api.get("/api/profile");
  render(view, html`<form class="card stack" id="form" style="max-width:620px">
    <h2>Your profile</h2>
    <label class="field">Name<input type="text" id="name" value="${p.name}" maxlength="80"></label>
    <label class="field">Role<input type="text" id="role" value="${p.role}" maxlength="80" placeholder="e.g. Legal counsel, Procurement manager"></label>
    <label class="field">Organization<input type="text" id="org" value="${p.organization}" maxlength="120"></label>
    <label class="field">Usual review perspective
      <select id="persp">${PERSPECTIVES.map(([v, l]) => html`<option value="${v}" ${v === p.default_perspective ? "selected" : ""}>${l}</option>`)}</select>
      <span class="hint">The risk analyzer uses this to judge which one-sided terms are risky for you.</span></label>
    <div><button class="btn primary">Save profile</button></div>
    <p class="small muted">This app runs locally for a single user; the profile is stored in the local database.</p>
  </form>`);
  $("#form", view).addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
      await api.put("/api/profile", { name: $("#name", view).value, role: $("#role", view).value,
        organization: $("#org", view).value, default_perspective: $("#persp", view).value });
      toast("Profile saved.");
    } catch (err) { toast(err.message, "err"); }
  });
}
