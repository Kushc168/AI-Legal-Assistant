// Thin fetch wrapper for the backend API.
async function request(method, url, body, { raw = false } = {}) {
  const opts = { method, headers: {} };
  if (body instanceof FormData) opts.body = body;
  else if (body !== undefined) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  const res = await fetch(url, opts);
  if (raw) return res;
  if (res.status === 204) return null;
  let data = null;
  try { data = await res.json(); } catch { /* empty body */ }
  if (!res.ok) {
    const detail = data && data.detail;
    const message = Array.isArray(detail) ? detail.map((d) => d.msg).join("; ") : detail || `Request failed (${res.status})`;
    const err = new Error(message);
    err.status = res.status;
    throw err;
  }
  return data;
}

export const api = {
  get: (url) => request("GET", url),
  post: (url, body) => request("POST", url, body),
  put: (url, body) => request("PUT", url, body),
  patch: (url, body) => request("PATCH", url, body),
  del: (url) => request("DELETE", url),
  raw: (url) => request("GET", url, undefined, { raw: true }),
};

// AI service endpoints (the server-side ai_service functions they map to).
export const aiService = {
  generateAnswer: (conversationId, content) => api.post(`/api/conversations/${conversationId}/messages`, { content }),
  summarizeContract: (contractId) => api.post(`/api/contracts/${contractId}/summary`),
  explainClause: (chunkId) => api.get(`/api/clauses/${encodeURIComponent(chunkId)}/explain`),
  riskAnalyzer: (contractId) => api.post(`/api/contracts/${contractId}/risk-analysis`),
  compareContracts: (a, b, perspective) => api.post("/api/comparisons", { contract_a_id: a, contract_b_id: b, perspective }),
};

export async function download(url, fallbackName) {
  const res = await api.raw(url);
  if (!res.ok) {
    let msg = `Export failed (${res.status})`;
    try { msg = (await res.json()).detail || msg; } catch { /* not JSON */ }
    throw new Error(msg);
  }
  const blob = await res.blob();
  const cd = res.headers.get("Content-Disposition") || "";
  const match = cd.match(/filename="([^"]+)"/);
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = match ? match[1] : fallbackName;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 2000);
}
