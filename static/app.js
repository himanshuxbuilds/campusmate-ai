/* CampusMate AI frontend. Vanilla JS. SECURITY: this file never builds HTML from strings and never evaluates strings as code; every piece of
   server- or model-supplied text is inserted as a text node (textContent), so it cannot become markup. The session lives in an httpOnly
   cookie (invisible to JS); the CSRF token is kept only in memory and sent as X-CSRF-Token on state-changing requests. */
"use strict";
(() => {
  const state = { user: null, csrf: null, conversationId: null, lastMessage: null, busy: false, memoryCache: [] };
  const $ = (sel, root = document) => root.querySelector(sel);

  // ---------------------------------------------------------------- DOM helpers (no HTML strings anywhere)
  function el(tag, attrs, ...children) {
    const n = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (v === undefined || v === null || v === false) continue;
      if (k === "class") n.className = v;
      else if (k === "text") n.textContent = v;
      else if (k.startsWith("on") && typeof v === "function") n.addEventListener(k.slice(2), v);
      else n.setAttribute(k, v === true ? "" : String(v));
    }
    for (const c of children.flat()) if (c !== null && c !== undefined && c !== false) n.append(c.nodeType ? c : document.createTextNode(String(c)));
    return n;
  }
  const clear = (n) => { while (n.firstChild) n.removeChild(n.firstChild); return n; };
  const fmt = (iso) => { if (!iso) return "-"; const d = new Date(iso); return isNaN(d) ? String(iso) : d.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" }); };
  let toastTimer;
  function toast(msg) { const t = $("#toast"); t.textContent = msg; t.hidden = false; clearTimeout(toastTimer); toastTimer = setTimeout(() => (t.hidden = true), 3200); }
  const pill = (text, kind) => el("span", { class: "pill pill-" + (kind || "quiet"), text });
  const statusKind = (s) => ({ EXECUTED: "ok", APPROVED: "ok", PENDING: "warn", REJECTED: "bad", FAILED: "bad", EXPIRED: "bad", SUCCESS: "ok", DENIED: "bad", FAILURE: "bad" }[s] || "quiet");

  // ---------------------------------------------------------------- Markdown -> DOM (headings, bold, code, lists, tables, handbook refs)
  const REF = /\[([A-Z](?:\.\d+)+(?:,\s*[A-Z](?:\.\d+)+)*)\]/;
  function inline(text, parent, onRef) {
    const re = new RegExp(`(\\*\\*[^*]+\\*\\*|\`[^\`]+\`|\\*[^*\\s][^*]*\\*|${REF.source})`, "g");
    let last = 0, m;
    while ((m = re.exec(text))) {
      if (m.index > last) parent.append(text.slice(last, m.index));
      const tok = m[0];
      if (tok.startsWith("**")) parent.append(el("strong", { text: tok.slice(2, -2) }));
      else if (tok.startsWith("`")) parent.append(el("code", { text: tok.slice(1, -1) }));
      else if (tok.startsWith("[")) tok.slice(1, -1).split(/,\s*/).forEach((id) => parent.append(el("button", { class: "chip chip-ref", type: "button", "data-ref": id, text: id, onclick: () => onRef && onRef(id) })));
      else parent.append(el("em", { text: tok.slice(1, -1) }));
      last = m.index + tok.length;
    }
    if (last < text.length) parent.append(text.slice(last));
    return parent;
  }
  function renderMarkdown(text, onRef) {
    const out = document.createDocumentFragment();
    const lines = String(text || "").replace(/\r/g, "").split("\n");
    let para = [], list = null, table = null;
    const flushPara = () => { if (para.length) { const p = el("p"); para.forEach((l, i) => { if (i) p.append(el("br")); inline(l, p, onRef); }); out.append(p); para = []; } };
    const flushList = () => { if (list) { const ul = el("ul"); list.forEach((t) => ul.append(inline(t, el("li"), onRef))); out.append(ul); list = null; } };
    const flushTable = () => {
      if (!table) return;
      const [head, ...rows] = table, t = el("table"), thead = el("thead"), tr = el("tr");
      head.forEach((c) => tr.append(inline(c, el("th"), onRef))); thead.append(tr); t.append(thead);
      const tb = el("tbody"); rows.forEach((r) => { const row = el("tr"); r.forEach((c) => row.append(inline(c, el("td"), onRef))); tb.append(row); });
      t.append(tb); out.append(el("div", { class: "tablewrap" }, t)); table = null;
    };
    for (const raw of lines) {
      const line = raw.trimEnd();
      if (/^\s*\|.*\|\s*$/.test(line)) {
        flushPara(); flushList();
        const cells = line.trim().slice(1, -1).split("|").map((c) => c.trim());
        if (cells.every((c) => /^:?-{2,}:?$/.test(c))) continue;
        (table = table || []).push(cells); continue;
      }
      flushTable();
      const h = line.match(/^(#{1,4})\s+(.*)$/);
      if (h) { flushPara(); flushList(); const lvl = Math.min(h[1].length + 2, 5); out.append(inline(h[2], el("h" + lvl), onRef)); continue; }
      const li = line.match(/^\s*(?:[-*]|\d+[.)])\s+(.*)$/);
      if (li) { flushPara(); (list = list || []).push(li[1]); continue; }
      if (!line.trim()) { flushPara(); flushList(); continue; }
      flushList(); para.push(line);
    }
    flushPara(); flushList(); flushTable();
    return out;
  }

  // ---------------------------------------------------------------- API client
  class ApiError extends Error {
    constructor(status, body) { const e = (body && body.error) || {}; super(e.message || "Request failed"); this.status = status; this.code = e.code || "ERROR"; this.retryable = !!e.retryable; this.info = e; }
  }
  async function api(path, opts = {}) {
    const method = (opts.method || "GET").toUpperCase();
    const headers = { Accept: "application/json" };
    if (opts.body !== undefined) headers["Content-Type"] = "application/json";
    if (method !== "GET" && state.csrf) headers["X-CSRF-Token"] = state.csrf;
    let res;
    try { res = await fetch(path, { method, headers, credentials: "same-origin", body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined }); }
    catch (_) { throw new ApiError(0, { error: { code: "NETWORK", message: "Cannot reach the server. Check your connection.", retryable: true } }); }
    let body = null; try { body = await res.json(); } catch (_) { /* empty body */ }
    if (res.status === 401 && !opts.allow401) { sessionExpired(); throw new ApiError(401, body); }
    if (!res.ok) throw new ApiError(res.status, body);
    return body;
  }
  function sessionExpired() { if (state.user) { state.user = null; state.csrf = null; showLogin("Your session has ended. Please sign in again."); } }

  // ---------------------------------------------------------------- login
  function mount(tplId) { const root = $("#app"); clear(root); root.append(document.getElementById(tplId).content.cloneNode(true)); return root; }
  async function showLogin(notice) {
    mount("tpl-login");
    const err = $("#login-error"), form = $("#login-form"), pass = $("#login-pass"), user = $("#login-user");
    if (notice) { err.textContent = notice; err.hidden = false; }
    $("#pw-toggle").addEventListener("click", (e) => { const show = pass.type === "password"; pass.type = show ? "text" : "password"; e.currentTarget.textContent = show ? "Hide" : "Show"; e.currentTarget.setAttribute("aria-pressed", String(show)); e.currentTarget.setAttribute("aria-label", show ? "Hide password" : "Show password"); });
    form.addEventListener("submit", async (e) => {
      e.preventDefault(); err.hidden = true;
      if (!user.value.trim() || !pass.value) { err.textContent = "Enter your ID and password."; err.hidden = false; return; }
      const btn = $("#login-submit"); btn.disabled = true;
      try {
        const r = await api("/api/auth/login", { method: "POST", body: { username: user.value.trim(), password: pass.value }, allow401: true });
        state.user = r.user; state.csrf = r.user.csrf_token; pass.value = ""; enter();
      } catch (ex) {
        err.textContent = ex.status === 429 ? "Too many failed attempts. Please wait a few minutes and try again." : (ex.status === 401 ? "Invalid username or password." : ex.message);
        err.hidden = false; btn.disabled = false; pass.value = ""; pass.focus();
      }
    });
    try {                                                                   // only exists in development
      const d = await api("/api/auth/demo-accounts", { allow401: true });
      const box = $("#demo-list"); $("#demo").hidden = false;
      d.accounts.forEach((a) => box.append(el("button", { type: "button", class: "demo-item", onclick: () => { user.value = a.username; pass.value = a.password; pass.focus(); } }, el("b", { text: a.username }), el("div", { class: "muted", text: a.label }))));
    } catch (_) { /* not available: production */ }
    user.focus();
  }

  async function boot() {
    try { const r = await api("/api/auth/me", { allow401: true }); state.user = r.user; state.csrf = r.user.csrf_token; enter(); }
    catch (_) { showLogin(); }
  }
  async function logout() { try { await api("/api/auth/logout", { method: "POST" }); } catch (_) { /* already gone */ } state.user = null; state.csrf = null; state.conversationId = null; showLogin(); }
  function enter() { if (state.user.role === "student") studentView(); else staffView(); }
  window.__campus = { renderMarkdown, el };   // exposed only for the static-analysis / unit tests of the renderer

  // ---------------------------------------------------------------- student workspace
  const SUGGESTIONS = ["What is my current fee status?", "Am I eligible for a scholarship?", "What is the policy for changing courses?",
                       "I could not complete my fee payment. What should I do?", "Create a support request for me.", "What documents are required for a scholarship application?"];

  function openModal(title, bodyNode) {
    const m = $("#modal"); $("#modal-title").textContent = title; clear($("#modal-body")).append(bodyNode); m.hidden = false; $("#modal-close").focus();
  }
  const closeModal = () => { $("#modal").hidden = true; };

  async function studentView() {
    mount("tpl-student");
    const u = state.user;
    $("#who-name").textContent = u.name; $("#who-meta").textContent = u.programme || ""; $("#avatar").textContent = u.name.split(" ").map((w) => w[0]).join("").slice(0, 2);
    $("#btn-logout").addEventListener("click", logout);
    $("#btn-new").addEventListener("click", () => { state.conversationId = null; showWelcome(); renderActive(); $("#sidebar").classList.remove("is-open"); $("#chat-input").focus(); });
    $("#btn-menu").addEventListener("click", () => $("#sidebar").classList.toggle("is-open"));
    $("#btn-profile").addEventListener("click", showProfile);
    $("#btn-requests").addEventListener("click", showRequests);
    $("#modal-close").addEventListener("click", closeModal);
    $("#modal").addEventListener("click", (e) => { if (e.target.id === "modal") closeModal(); });
    document.addEventListener("keydown", (e) => { if (e.key === "Escape" && $("#modal") && !$("#modal").hidden) closeModal(); });
    const input = $("#chat-input");
    input.addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); $("#chat-form").requestSubmit(); } });
    input.addEventListener("input", () => { input.style.height = "auto"; input.style.height = Math.min(input.scrollHeight, 160) + "px"; });
    $("#chat-form").addEventListener("submit", (e) => { e.preventDefault(); const t = input.value.trim(); if (t) { input.value = ""; input.style.height = "auto"; send(t); } });
    $("#btn-retry").addEventListener("click", () => { if (state.lastUserEl) state.lastUserEl.remove(); $("#error-bar").hidden = true; if (state.lastMessage) send(state.lastMessage); });
    fetch("/api/health").then((r) => r.json()).then((h) => { if (!h.llm.configured) { const p = $("#provider-pill"); p.textContent = "AI not configured"; p.className = "pill pill-warn"; p.hidden = false; }
      else if (h.llm.provider === "mock") { const p = $("#provider-pill"); p.textContent = "offline test model"; p.className = "pill pill-warn"; p.hidden = false; } }).catch(() => {});
    showWelcome(); await loadConversations(); refreshRequestCount();
  }

  function showWelcome() {
    const box = clear($("#messages")); state.lastUserEl = null;
    box.append(el("div", { class: "welcome" }, el("h2", { text: `Hi ${state.user.name.split(" ")[0]}, how can I help?` }),
      el("p", { class: "muted", text: "I can check your fees and scholarship, look up Institute policies, dates and subjects, and raise support tickets. Changes to your record are sent to staff for approval, and I only ever see your own data." }),
      el("div", { class: "suggest" }, SUGGESTIONS.map((q) => el("button", { type: "button", onclick: () => send(q), text: q })))));
  }

  async function loadConversations() {
    try { const r = await api("/api/conversations"); renderConvList(r.conversations); } catch (_) { /* shown elsewhere */ }
  }
  let convCache = [];
  function renderConvList(list) {
    convCache = list; const ul = clear($("#conv-list")); $("#conv-empty").hidden = list.length > 0;
    list.forEach((c) => ul.append(el("li", { class: "conv-item" + (c.id === state.conversationId ? " is-active" : ""), "data-id": c.id },
      el("button", { class: "conv-open", type: "button", title: c.title, onclick: () => openConversation(c.id), text: c.title }),
      el("button", { class: "conv-del", type: "button", "aria-label": "Delete conversation " + c.title, onclick: () => deleteConversation(c), text: "x" }))));
  }
  const renderActive = () => document.querySelectorAll(".conv-item").forEach((li) => li.classList.toggle("is-active", li.dataset.id === state.conversationId));

  async function openConversation(id) {
    try {
      const r = await api(`/api/conversations/${id}/messages`); state.conversationId = id; $("#error-bar").hidden = true;
      const box = clear($("#messages")); let approvals = {};
      try { (await api("/api/student/approvals")).approvals.forEach((a) => (approvals[a.request_number] = a)); } catch (_) { /* optional */ }
      r.messages.forEach((m) => { if (m.role === "user") addUser(m.content); else addAgent(m.content, { sources: (m.meta || {}).sources || [], tickets: (m.meta || {}).tickets || [], approvals: ((m.meta || {}).approvals || []).map((n) => approvals[n]).filter(Boolean), outcome: (m.meta || {}).outcome, tools: ((m.meta || {}).tools || []) }, true); });
      renderActive(); box.scrollTop = box.scrollHeight; $("#sidebar").classList.remove("is-open");
    } catch (ex) { toast(ex.message); if (ex.status === 404) { state.conversationId = null; loadConversations(); showWelcome(); } }
  }
  async function deleteConversation(c) {
    if (!confirm(`Delete "${c.title}"? This removes its messages permanently.`)) return;
    try { await api(`/api/conversations/${c.id}`, { method: "DELETE" }); if (state.conversationId === c.id) { state.conversationId = null; showWelcome(); } await loadConversations(); toast("Conversation deleted"); } catch (ex) { toast(ex.message); }
  }

  const scrollDown = () => { const m = $("#messages"); m.scrollTop = m.scrollHeight; };
  function dropWelcome() { const w = $(".welcome"); if (w) w.remove(); }
  function addUser(text) { dropWelcome(); const n = el("div", { class: "msg msg-user" }, el("div", { class: "bubble", text })); $("#messages").append(n); scrollDown(); state.lastUserEl = n; return n; }

  async function showEntry(id) {
    try { const c = await api(`/api/policy/chunks/${encodeURIComponent(id)}`); openModal(`${c.id} - ${c.title}`, el("div", {}, el("p", { class: "muted small", text: `${c.section} · ${c.status} · ${c.source}` }), el("div", { class: "bubble" }, renderMarkdown(c.text)))); }
    catch (ex) { toast(ex.message); }
  }

  function approvalCard(a) {
    const changes = Object.entries(a.requested_changes || {});
    return el("div", { class: "approval-card is-" + a.status.toLowerCase(), "data-request": a.request_number },
      el("h4", {}, a.action_description + " ", pill(a.status, statusKind(a.status))),
      el("dl", { class: "kv" }, el("dt", { text: "Request" }), el("dd", { text: a.request_number }),
        ...changes.flatMap(([k, v]) => [el("dt", { text: k.replace(/_/g, " ") }), el("dd", { text: String(v) })]),
        a.reason ? [el("dt", { text: "Reason" }), el("dd", { text: a.reason })] : null,
        el("dt", { text: "Expires" }), el("dd", { text: fmt(a.expires_at) }),
        a.execution_result ? [el("dt", { text: "Result" }), el("dd", { text: a.execution_result })] : null),
      a.status === "PENDING" ? el("p", { class: "muted small", text: "Not applied yet. A staff member must approve it." }) : null);
  }

  function traceDetails(t) {
    const rows = (t.steps || []).filter((s) => ["model", "tool", "guardrail", "memory"].includes(s.kind));
    const describe = (s) => {
      if (s.kind === "tool") return `${s.name} -> ${s.status}${s.error_code ? " (" + s.error_code + ")" : ""} · ${s.latency_ms} ms`;
      if (s.kind === "model") return `${s.decision}${s.tools && s.tools.length ? ": " + s.tools.join(", ") : ""} · ${s.latency_ms} ms`;
      return s.detail || "";
    };
    const head = el("tr", {}, ["#", "step", "detail"].map((h) => el("th", { text: h })));
    const body = rows.map((s) => el("tr", {}, el("td", { text: s.n }), el("td", { text: s.kind }), el("td", { text: describe(s) })));
    const info = `${t.provider} · ${t.model} · ${t.iterations} model call(s) · model ${t.model_latency_ms} ms · tools ${t.tool_latency_ms} ms · tokens ${t.token_source}`;
    return el("details", { class: "trace" }, el("summary", { text: `Execution trace ${t.trace_id}` }),
      el("div", { class: "trace-body" }, el("p", { class: "muted small", text: info }), el("table", {}, el("thead", {}, head), el("tbody", {}, body))));
  }

  function addAgent(reply, d, fromHistory) {
    dropWelcome();
    const wrap = el("div", { class: "msg msg-agent" });
    wrap.append(el("div", { class: "bubble" }, renderMarkdown(reply, showEntry)));
    const meta = el("div", { class: "meta" });
    ((d.trace && d.trace.tools) || d.tools || []).forEach((t) => meta.append(el("span", { class: "chip " + (t.status === "blocked" ? "chip-bad" : t.status === "pending_approval" ? "chip-warn" : "chip-tool"), text: t.name + (t.status && t.status !== "ok" ? " (" + t.status.replace("_", " ") + ")" : "") })));
    if (d.outcome === "blocked") meta.append(el("span", { class: "chip chip-bad", text: "access blocked" }));
    (d.tickets || []).forEach((n) => meta.append(el("span", { class: "chip chip-ok", text: "ticket " + n })));
    if ((d.redactions || []).length) meta.append(el("span", { class: "chip chip-warn", text: "sensitive value masked, not stored" }));
    if (d.trace) meta.append(el("span", { class: "chip", text: `${d.trace.latency_ms} ms · ${d.trace.input_tokens}+${d.trace.output_tokens} tokens (${d.trace.token_source})` }));
    if (meta.children.length) wrap.append(meta);
    if ((d.sources || []).length) wrap.append(el("details", { class: "sources" }, el("summary", { text: `Sources (${d.sources.length})` }),
      el("ul", {}, d.sources.map((s) => el("li", {}, el("button", { class: "chip chip-ref", type: "button", onclick: () => showEntry(s.id), text: s.id }), " ", el("span", { class: "src-title", text: s.title }),
        el("div", { class: "src-meta", text: `${s.section || ""} · ${s.status}${s.score != null ? " · relevance " + s.score : ""}` }))))));
    (d.approvals || []).forEach((a) => wrap.append(approvalCard(a)));
    if (d.trace && !fromHistory) wrap.append(traceDetails(d.trace));
    $("#messages").append(wrap); scrollDown(); return wrap;
  }

  function showError(ex) {
    const bar = $("#error-bar"); $("#error-text").textContent = ex.message || "Something went wrong."; $("#btn-retry").hidden = !(ex.retryable || ex.status === 0 || ex.status >= 500); bar.hidden = false;
  }

  async function send(text) {
    if (state.busy) return;
    state.busy = true; state.lastMessage = text; $("#error-bar").hidden = true; $("#btn-send").disabled = true;
    addUser(text);
    const status = $("#status"); status.textContent = "Thinking"; status.hidden = false;
    let finished = false;
    try {
      const res = await fetch("/api/chat/stream", { method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json", Accept: "text/event-stream", "X-CSRF-Token": state.csrf || "" }, body: JSON.stringify({ message: text, conversation_id: state.conversationId || undefined }) });
      if (res.status === 401) { sessionExpired(); return; }
      if (!res.ok || !(res.headers.get("content-type") || "").includes("event-stream")) { let b = null; try { b = await res.json(); } catch (_) {} throw new ApiError(res.status, b); }
      const reader = res.body.getReader(), dec = new TextDecoder(); let buf = "";
      for (;;) {
        const { value, done } = await reader.read(); if (done) break;
        buf += dec.decode(value, { stream: true }); let i;
        while ((i = buf.indexOf("\n\n")) >= 0) {
          const chunk = buf.slice(0, i); buf = buf.slice(i + 2);
          if (!chunk.startsWith("data: ")) continue;
          const ev = JSON.parse(chunk.slice(6));
          if (ev.type === "status") status.textContent = ev.text + "...";
          else if (ev.type === "final") { finished = true; state.conversationId = ev.data.conversation_id; addAgent(ev.data.reply, ev.data); loadConversations(); refreshRequestCount(); }
          else if (ev.type === "error") { finished = true; showError({ message: ev.error.message, retryable: ev.error.retryable, status: ev.status }); }
        }
      }
      if (!finished) showError({ message: "The connection was interrupted before the answer arrived.", retryable: true });
    } catch (ex) { showError(ex instanceof ApiError ? ex : { message: "Something went wrong.", retryable: true }); }
    finally { state.busy = false; status.hidden = true; $("#btn-send").disabled = false; const i = $("#chat-input"); if (i) i.focus(); }
  }

  async function refreshRequestCount() {
    try { const r = await api("/api/student/approvals"); const n = r.approvals.filter((a) => a.status === "PENDING").length; const b = $("#req-count"); if (b) { b.textContent = n; b.hidden = n === 0; } } catch (_) { /* ignore */ }
  }

  async function showProfile() {
    try {
      const [p, mem] = await Promise.all([api("/api/student/profile"), api("/api/memory")]);
      const body = el("div", {}, el("dl", { class: "kv" }, ...[["Name", state.user.name], ["Student ID", p.student_id_masked], ["Programme", p.programme_name], ["Batch / trimester", `${p.batch} / ${p.current_trimester}`],
        ["Email", p.email || "-"], ["Mobile", p.mobile || "-"], ["Courses", (p.registered_courses || []).map((c) => c.course).join(", ") || "-"]].flatMap(([k, v]) => [el("dt", { text: k }), el("dd", { text: v })])),
        el("h3", { text: "What I remember about you" }), el("p", { class: "muted small", text: "Only durable, non-sensitive preferences are stored. Passwords, OTPs, card numbers and API keys are never saved. Delete anything here at any time." }));
      const list = el("div", {}); const drawMem = (items) => { clear(list); if (!items.length) list.append(el("p", { class: "muted", text: "Nothing stored yet." }));
        items.forEach((m) => list.append(el("div", { class: "memory-row" }, el("span", {}, pill(m.category, "info"), " ", el("b", { text: m.key.replace(/_/g, " ") + ": " }), m.value),
          el("button", { class: "btn btn-small btn-bad", type: "button", "aria-label": "Forget " + m.key, text: "Forget", onclick: async () => { try { await api(`/api/memory/${m.id}`, { method: "DELETE" }); drawMem((await api("/api/memory")).items); toast("Forgotten"); } catch (ex) { toast(ex.message); } } })))); };
      drawMem(mem.items); body.append(list);
      body.append(el("button", { class: "btn", type: "button", text: "Forget everything", onclick: async () => { if (!confirm("Delete everything I remember about you?")) return; try { await api("/api/memory", { method: "DELETE" }); drawMem([]); toast("Memory cleared"); } catch (ex) { toast(ex.message); } } }));
      openModal("Profile & memory", body);
    } catch (ex) { toast(ex.message); }
  }

  async function showRequests() {
    try {
      const [a, t] = await Promise.all([api("/api/student/approvals"), api("/api/student/tickets")]);
      const body = el("div", {}, el("h3", { text: "Change requests" }), a.approvals.length ? a.approvals.map(approvalCard) : el("p", { class: "muted", text: "No change requests yet." }),
        el("h3", { text: "Support tickets" }), t.tickets.length ? el("div", { class: "tablewrap-x" }, el("table", { class: "dtable" }, el("thead", {}, el("tr", {}, ["Ticket", "Category", "Subject", "Status"].map((h) => el("th", { text: h })))),
          el("tbody", {}, t.tickets.map((x) => el("tr", {}, el("td", { text: x.ticket_number }), el("td", { text: x.category_label }), el("td", { text: x.subject }), el("td", {}, pill(x.status, "info"))))))) : el("p", { class: "muted", text: "No tickets yet." }));
      openModal("My requests", body);
    } catch (ex) { toast(ex.message); }
  }

  // ---------------------------------------------------------------- staff / admin dashboard
  const EVENT_TYPES = ["LOGIN_SUCCESS", "LOGIN_FAILURE", "LOGIN_LOCKED", "LOGOUT", "SESSION_EXPIRED", "ACCESS_DENIED", "RECORD_ACCESS", "TICKET_CREATED", "TICKET_UPDATED", "SENSITIVE_REQUEST",
                       "APPROVAL_DECISION", "RECORD_MODIFIED", "TOOL_FAILURE", "TOOL_REJECTED", "LLM_FAILURE", "MEMORY_CREATED", "MEMORY_DELETED", "CONVERSATION_DELETED", "ADMIN_ACTION", "SECRET_SHARED", "RATE_LIMITED", "CSRF_REJECTED"];
  const STAFF_EVENT_TYPES = ["ACCESS_DENIED", "TOOL_FAILURE", "TOOL_REJECTED", "LLM_FAILURE", "SENSITIVE_REQUEST", "APPROVAL_DECISION", "RECORD_MODIFIED", "TICKET_CREATED", "TICKET_UPDATED", "RATE_LIMITED"];
  const TICKET_STATUSES = ["OPEN", "IN_PROGRESS", "AWAITING_STUDENT", "RESOLVED", "CLOSED", "ESCALATED"];

  const select = (options, value, onchange, label) => { const s = el("select", { "aria-label": label || "filter" }, options.map(([v, t]) => el("option", { value: v, text: t }))); s.value = value; if (onchange) s.addEventListener("change", () => onchange(s.value)); return s; };
  const dtable = (headers, rows) => el("div", { class: "tablewrap-x" }, el("table", { class: "dtable" }, el("thead", {}, el("tr", {}, headers.map((h) => el("th", { text: h })))), el("tbody", {}, rows)));
  const td = (...c) => el("td", {}, ...c);

  async function staffView() {
    mount("tpl-staff");
    $("#role-pill").textContent = state.user.role; $("#staff-name").textContent = state.user.name; $("#btn-logout").addEventListener("click", logout);
    const tabs = [["approvals", "Approvals"], ["tickets", "Support tickets"], ["denied", "Access denied"], ["failures", "Tool failures"], ["audit", "Audit events"], ["traces", "Agent traces"], ["evaluation", "Evaluation"]];
    if (state.user.role === "admin") tabs.push(["users", "Users"]);
    const bar = $("#tabs");
    const panels = { approvals: approvalsPanel, tickets: ticketsPanel, denied: () => auditPanel(["ACCESS_DENIED"]), failures: () => auditPanel(["TOOL_FAILURE", "TOOL_REJECTED", "LLM_FAILURE"]), audit: () => auditPanel(null), traces: tracesPanel, evaluation: evaluationPanel, users: usersPanel };
    const open = (id) => { bar.querySelectorAll(".tab").forEach((b) => b.setAttribute("aria-selected", String(b.dataset.tab === id))); const p = clear($("#panel")); panels[id](p).catch((ex) => p.append(el("p", { class: "error", text: ex.message }))); };
    tabs.forEach(([id, label]) => bar.append(el("button", { class: "tab", type: "button", role: "tab", "data-tab": id, "aria-selected": "false", text: label, onclick: () => open(id) })));
    await loadMetrics(); open("approvals");
  }

  async function loadMetrics() {
    try {
      const m = await api("/api/admin/metrics"), box = clear($("#metrics"));
      [["pending_approvals", "Pending approvals"], ["open_tickets", "Open tickets"], ["access_denied_24h", "Access denied (24h)"], ["tool_failures_24h", "Tool failures (24h)"], ["llm_failures_24h", "LLM failures (24h)"], ["failed_logins_24h", "Failed logins (24h)"]]
        .forEach(([k, t]) => box.append(el("div", { class: "metric" }, el("b", { text: m[k] }), el("span", { text: t }))));
    } catch (_) { /* tab content will show the error */ }
  }

  async function approvalsPanel(root) {
    const ctl = el("div", { class: "filters" }); const list = el("div", {}); root.append(ctl, list);
    const draw = async (status) => {
      const r = await api(status === "ALL" ? "/api/approvals?limit=100" : `/api/approvals?status=${status}&limit=100`); clear(list);
      if (!r.approvals.length) list.append(el("p", { class: "muted", text: status === "PENDING" ? "No requests are waiting. New sensitive changes asked for in chat appear here." : "Nothing to show." }));
      r.approvals.forEach((a) => {
        const note = el("input", { type: "text", placeholder: "Comment for the audit log (optional)", "aria-label": "Decision comment", maxlength: "500" });
        const act = async (decision) => { try { const out = await api(`/api/approvals/${a.request_number}/${decision}`, { method: "POST", body: { comment: note.value } });
          toast(decision === "approve" ? (out.approval.status === "EXECUTED" ? "Approved and applied" : `Result: ${out.approval.status}`) : "Rejected: nothing was changed"); } catch (ex) { toast(ex.message); } await draw(status); loadMetrics(); };
        list.append(el("div", { class: "queue-item is-" + a.status.toLowerCase() },
          el("h3", {}, `${a.action_description} `, pill(a.status, statusKind(a.status))),
          el("p", { class: "muted small", text: `${a.request_number} · ${a.requester_name || a.requester_id} (${a.requester_id}) · requested ${fmt(a.created_at)} · expires ${fmt(a.expires_at)}` }),
          el("dl", { class: "kv" }, ...Object.entries(a.requested_changes || {}).flatMap(([k, v]) => [el("dt", { text: k.replace(/_/g, " ") }), el("dd", { text: String(v) })]), a.reason ? [el("dt", { text: "Reason" }), el("dd", { text: a.reason })] : null,
            a.status !== "PENDING" ? [el("dt", { text: "Decision" }), el("dd", { text: `${a.approver_id || "system"} · ${fmt(a.decided_at)} · ${a.execution_result || ""}${a.approval_comment ? " · " + a.approval_comment : ""}` })] : null),
          a.status === "PENDING" ? el("div", { class: "queue-actions" }, note, el("button", { class: "btn btn-small btn-ok", type: "button", text: "Approve and apply", onclick: () => act("approve") }), el("button", { class: "btn btn-small btn-bad", type: "button", text: "Reject", onclick: () => act("reject") })) : null));
      });
    };
    ctl.append(el("span", { class: "muted", text: "Status" }), select([["PENDING", "Pending"], ["ALL", "All"], ["EXECUTED", "Executed"], ["REJECTED", "Rejected"], ["FAILED", "Failed"], ["EXPIRED", "Expired"]], "PENDING", draw, "Status filter"));
    await draw("PENDING");
  }

  async function ticketsPanel(root) {
    const ctl = el("div", { class: "filters" }), out = el("div", {}); root.append(ctl, out);
    const f = { status: "", category: "" };
    const draw = async () => {
      const q = new URLSearchParams({ limit: "100" }); if (f.status) q.set("status", f.status); if (f.category) q.set("category", f.category);
      const r = await api("/api/admin/tickets?" + q); clear(out);
      if (!r.tickets.length) { out.append(el("p", { class: "muted", text: "No tickets match." })); return; }
      out.append(dtable(["Ticket", "Student", "Category", "Subject", "Priority", "Status", "Note", ""], r.tickets.map((t) => {
        const st = select(TICKET_STATUSES.map((x) => [x, x]), t.status, null, "Status"), note = el("input", { type: "text", value: t.staff_note || "", "aria-label": "Staff note", maxlength: "1000" });
        const save = async (extra) => { try { await api(`/api/admin/tickets/${t.ticket_number}`, { method: "PATCH", body: { status: st.value, staff_note: note.value, ...extra } }); toast("Ticket updated"); await draw(); loadMetrics(); } catch (ex) { toast(ex.message); } };
        return el("tr", {}, td(t.ticket_number), td(t.student_id), td(t.category_label), td(t.subject), td(t.priority), td(st), td(note),
          td(el("button", { class: "btn btn-small", type: "button", text: "Save", onclick: () => save({}) }), " ", el("button", { class: "btn btn-small", type: "button", text: t.assigned_staff_id ? "Assigned: " + t.assigned_staff_id : "Assign to me", onclick: () => save({ assign_to_me: true }) })));
      })));
    };
    ctl.append(el("span", { class: "muted", text: "Status" }), select([["", "Any"], ...TICKET_STATUSES.map((x) => [x, x])], "", (v) => { f.status = v; draw(); }, "Status filter"),
      el("span", { class: "muted", text: "Category" }), select([["", "Any"], ["FEES", "FEES"], ["SCHOLARSHIP", "SCHOLARSHIP"], ["ACADEMICS", "ACADEMICS"], ["EXAMINATION", "EXAMINATION"], ["TECHNICAL_SUPPORT", "TECHNICAL_SUPPORT"], ["HOSTEL", "HOSTEL"], ["LIBRARY", "LIBRARY"], ["DOCUMENTS", "DOCUMENTS"], ["PLACEMENT", "PLACEMENT"], ["OTHER", "OTHER"]], "", (v) => { f.category = v; draw(); }, "Category filter"));
    await draw();
  }

  async function auditPanel(presetTypes) {
    const root = $("#panel"); const ctl = el("div", { class: "filters" }), out = el("div", {}); root.append(ctl, out);
    const f = { type: presetTypes && presetTypes.length === 1 ? presetTypes[0] : "", outcome: "", offset: 0 }, limit = 25;
    const allowed = state.user.role === "admin" ? EVENT_TYPES : STAFF_EVENT_TYPES;
    const draw = async () => {
      let events = [], total = 0;
      if (presetTypes && presetTypes.length > 1) { for (const t of presetTypes) { const r = await api(`/api/admin/audit-events?event_type=${t}&limit=${limit}`); events.push(...r.events); total += r.total; } events.sort((a, b) => (b.timestamp || "").localeCompare(a.timestamp || "")); events = events.slice(0, limit); }
      else { const q = new URLSearchParams({ limit: String(limit), offset: String(f.offset) }); if (f.type) q.set("event_type", f.type); if (f.outcome) q.set("outcome", f.outcome); const r = await api("/api/admin/audit-events?" + q); events = r.events; total = r.total; }
      clear(out);
      out.append(dtable(["Time", "Event", "Actor", "Action", "Target", "Decision", "Approval", "Outcome", "Reason"], events.map((e) => el("tr", {}, td(fmt(e.timestamp)), td(e.event_type), td(`${e.actor_id || "-"} (${e.actor_role || "-"})`), td(e.action), td(e.target_type ? `${e.target_type}:${e.target_id || ""}` : "-"),
        td(e.authorization_decision || "-"), td(e.approval_status || "-"), td(pill(e.outcome, statusKind(e.outcome))), td(e.reason || "")))));
      if (!events.length) out.append(el("p", { class: "muted", text: "No events." }));
      if (!(presetTypes && presetTypes.length > 1)) out.append(el("div", { class: "pager" }, el("button", { class: "btn btn-small", type: "button", text: "Previous", disabled: f.offset === 0, onclick: () => { f.offset = Math.max(0, f.offset - limit); draw(); } }),
        el("span", { class: "muted small", text: `${total ? f.offset + 1 : 0}-${Math.min(f.offset + limit, total)} of ${total}` }), el("button", { class: "btn btn-small", type: "button", text: "Next", disabled: f.offset + limit >= total, onclick: () => { f.offset += limit; draw(); } })));
    };
    if (!presetTypes) ctl.append(el("span", { class: "muted", text: "Event" }), select([["", "All"], ...allowed.map((x) => [x, x])], "", (v) => { f.type = v; f.offset = 0; draw(); }, "Event type"),
      el("span", { class: "muted", text: "Outcome" }), select([["", "Any"], ["SUCCESS", "SUCCESS"], ["FAILURE", "FAILURE"], ["DENIED", "DENIED"], ["PENDING", "PENDING"]], "", (v) => { f.outcome = v; f.offset = 0; draw(); }, "Outcome"));
    else ctl.append(el("span", { class: "muted", text: "Showing: " + presetTypes.join(", ") }));
    await draw();
  }

  async function tracesPanel(root) {
    const ctl = el("div", { class: "filters" }), out = el("div", {}); root.append(ctl, out);
    const draw = async (outcome) => {
      const r = await api("/api/admin/traces?limit=50" + (outcome ? "&outcome=" + outcome : "")); clear(out);
      out.append(dtable(["Time", "User", "Provider / model", "Tools", "Retrieved", "Iter.", "Tokens (in+out)", "Latency", "Outcome"], r.traces.map((t) => el("tr", {}, td(fmt(t.timestamp)), td(t.user_id), td(`${t.provider} / ${t.model}`),
        td((t.tools_used || []).map((x) => x.name).join(", ") || "-"), td(String((t.retrieval_references || []).length)), td(String(t.iterations)), td(`${t.input_tokens}+${t.output_tokens} (${t.token_source})`), td(`${t.latency_ms} ms`), td(pill(t.outcome, t.outcome === "answered" ? "ok" : t.outcome === "blocked" || t.outcome === "llm_error" ? "bad" : "warn"))))));
      if (!r.traces.length) out.append(el("p", { class: "muted", text: "No traces." }));
    };
    ctl.append(el("span", { class: "muted", text: "Outcome" }), select([["", "All"], ["answered", "answered"], ["blocked", "blocked"], ["pending_approval", "pending_approval"], ["llm_error", "llm_error"], ["max_iterations", "max_iterations"]], "", draw, "Outcome filter"));
    await draw("");
  }

  async function evaluationPanel(root) {
    const r = await api("/api/admin/evaluation");
    if (!r.available) { root.append(el("p", { class: "muted", text: r.message })); return; }
    root.append(el("p", { class: "muted small", text: `Run ${fmt(r.generated_at)} · provider ${r.provider} / ${r.model} · ${r.token_source}` }));
    const m = r.metrics || {};
    root.append(dtable(["Metric", "Value"], Object.entries(m).map(([k, v]) => el("tr", {}, td(k.replace(/_/g, " ")), td(typeof v === "object" ? JSON.stringify(v) : String(v))))));
    root.append(el("h3", { text: "Scenarios" }), dtable(["#", "Scenario", "Result", "Latency", "Tools", "Observed"], (r.scenarios || []).map((s) => el("tr", {}, td(String(s.id)), td(s.name), td(pill(s.passed ? "PASS" : "FAIL", s.passed ? "ok" : "bad")), td(`${s.latency_ms} ms`), td((s.tools || []).join(", ") || "-"), td(s.observed || "")))));
  }

  async function usersPanel(root) {
    const r = await api("/api/admin/users");
    root.append(dtable(["User", "Name", "Role", "Active", ""], r.users.map((u) => {
      const role = select([["student", "student"], ["staff", "staff"], ["admin", "admin"]], u.role, null, "Role"), act = el("input", { type: "checkbox", "aria-label": "Active" }); act.checked = u.is_active;
      const self = u.user_code === state.user.user_code; role.disabled = self; act.disabled = self;
      return el("tr", {}, td(u.user_code), td(u.name), td(role), td(act), td(self ? "(you)" : el("button", { class: "btn btn-small", type: "button", text: "Save", onclick: async () => { try { await api(`/api/admin/users/${encodeURIComponent(u.user_code)}`, { method: "PATCH", body: { role: role.value, is_active: act.checked } }); toast("User updated"); } catch (ex) { toast(ex.message); } } })));
    })));
  }

  boot();
})();
