"use strict";

const $ = (sel, root = document) => root.querySelector(sel);

const esc = (value) => String(value ?? "").replace(/[&<>"']/g,
  (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

let displayTz = null;  // IANA zone from the server's TZ env; null = browser-local

const fmtTime = (iso) => {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleString(undefined, displayTz ? { timeZone: displayTz } : {});
  } catch (e) {
    return new Date(iso).toLocaleString();  // unknown zone name: browser-local fallback
  }
};

function tzOffsetMinutes() {
  // Same convention as Date.getTimezoneOffset(): minutes UTC is ahead of display time.
  if (!displayTz) return new Date().getTimezoneOffset();
  const now = new Date();
  try {
    const inZone = new Date(now.toLocaleString("en-US", { timeZone: displayTz }));
    return Math.round((now - inZone) / 60000);
  } catch (e) {
    return new Date().getTimezoneOffset();
  }
}

function toast(message, isError = false) {
  const box = $("#toast");
  box.textContent = message;
  box.classList.toggle("error", isError);
  box.classList.remove("hidden");
  clearTimeout(toast._timer);
  toast._timer = setTimeout(() => box.classList.add("hidden"), 3500);
}

async function api(path, options = {}) {
  const opts = { credentials: "same-origin", ...options };
  if (opts.body !== undefined) {
    opts.method = opts.method || "POST";
    opts.headers = { "Content-Type": "application/json", ...opts.headers };
    opts.body = JSON.stringify(opts.body);
  }
  const response = await fetch(path, opts);
  if (response.status === 401) {
    showLogin(true);
    throw new Error("authentication required");
  }
  if (!response.ok) {
    let detail = response.statusText;
    try { detail = (await response.json()).detail || detail; } catch (e) { /* not json */ }
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return response.status === 204 ? null : response.json();
}

function showLogin(show) {
  $("#login").classList.toggle("hidden", !show);
  if (show) $("#login-password").focus();
}

$("#login-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    await api("/api/login", { body: { password: $("#login-password").value } });
    $("#login-password").value = "";
    $("#login-error").classList.add("hidden");
    showLogin(false);
    route();
  } catch (err) {
    $("#login-error").classList.remove("hidden");
  }
});

$("#logout").addEventListener("click", async () => {
  await api("/api/logout", { body: {} });
  showLogin(true);
});

async function refreshAuthUi() {
  const status = await fetch("/api/auth/status", { credentials: "same-origin" })
    .then((r) => r.json());
  displayTz = status.display_timezone || null;
  $("#banner").classList.toggle("hidden", status.mode !== "open");
  $("#logout").classList.toggle("hidden", status.mode === "open");
  if (status.mode !== "open" && !status.authenticated) {
    showLogin(true);
    return false;
  }
  return true;
}

const views = {};
let logsTimer = null;

async function route() {
  clearInterval(logsTimer);
  const tab = (location.hash || "#/dashboard").slice(2) || "dashboard";
  document.querySelectorAll("nav a").forEach(
    (a) => a.classList.toggle("active", a.dataset.tab === tab));
  const root = $("#view");
  root.innerHTML = "<p class='muted'>Loading…</p>";
  if (!(await refreshAuthUi())) return;
  try {
    await (views[tab] || views.dashboard)(root);
  } catch (err) {
    root.innerHTML = `<p class="error">${esc(err.message)}</p>`;
  }
}

window.addEventListener("hashchange", route);

views.dashboard = async (root) => {
  const data = await api("/api/dashboard");
  root.innerHTML = `
    <section class="cards">
      <div class="card stat"><b>${data.endpoint_count}</b>
        <span>endpoints (${data.enabled_count} enabled)</span></div>
      <div class="card stat"><b>${data.deliveries_24h}</b><span>deliveries, 24 h</span></div>
      <div class="card stat ${data.failures_24h ? "bad" : ""}"><b>${data.failures_24h}</b>
        <span>failures, 24 h</span></div>
    </section>
    <section class="card">
      <h2>Endpoints</h2>
      <table><thead><tr><th>Name</th><th>Status</th><th>Delivered 24 h</th>
        <th>Failed</th><th>Rejected</th><th>Last event</th></tr></thead>
      <tbody>${data.endpoints.map((e) => `
        <tr><td>${esc(e.name)}</td>
        <td>${e.enabled ? "enabled" : "<span class='muted'>disabled</span>"}</td>
        <td>${e.delivered}</td><td class="${e.failed ? "error" : ""}">${e.failed}</td>
        <td>${e.rejected}</td><td>${fmtTime(e.last_at)}</td></tr>`).join("")
        || "<tr><td colspan='6' class='muted'>No endpoints yet — create one in Settings.</td></tr>"}
      </tbody></table>
    </section>
    <section class="card">
      <h2>Recent activity</h2>
      <table><thead><tr><th>Time</th><th>Endpoint</th><th>Status</th><th>Title</th></tr></thead>
      <tbody>${data.recent.map((d) => `
        <tr><td>${fmtTime(d.received_at)}</td><td>${esc(d.endpoint_name)}</td>
        <td><span class="badge ${d.status}">${d.status}</span></td>
        <td>${esc(d.title)}</td></tr>`).join("")
        || "<tr><td colspan='4' class='muted'>Nothing yet.</td></tr>"}
      </tbody></table>
    </section>`;
};

route();
