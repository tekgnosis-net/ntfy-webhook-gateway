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

async function copyText(text) {
  // navigator.clipboard only exists in secure contexts (HTTPS/localhost);
  // the admin UI commonly runs on plain LAN HTTP, so fall back to execCommand.
  if (navigator.clipboard && window.isSecureContext) {
    try { await navigator.clipboard.writeText(text); return true; } catch (e) { /* fall through */ }
  }
  const ta = document.createElement("textarea");
  ta.value = text;
  ta.style.position = "fixed";
  ta.style.opacity = "0";
  document.body.appendChild(ta);
  ta.select();
  let ok = false;
  try { ok = document.execCommand("copy"); } catch (e) { ok = false; }
  ta.remove();
  return ok;
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
        <td><span class="badge ${esc(d.status)}">${esc(d.status)}</span></td>
        <td>${esc(d.title)}</td></tr>`).join("")
        || "<tr><td colspan='4' class='muted'>Nothing yet.</td></tr>"}
      </tbody></table>
    </section>`;
};

views.settings = async (root) => {
  const [endpoints, presets, settings] = await Promise.all([
    api("/api/endpoints"), api("/api/presets"), api("/api/settings"),
  ]);
  const webhookBase = `${location.protocol}//${location.hostname}:${settings.webhook_port}/hooks/`;
  const PRIORITIES = ["min", "low", "default", "high", "urgent"];

  root.innerHTML = `<div id="settings-page">
    <section class="card">
      <div class="row-between"><h2>Webhook endpoints</h2>
        <button id="ep-new">New endpoint</button></div>
      <table><thead><tr><th>Name</th><th>Webhook URL</th><th>Topic</th>
        <th>Enabled</th><th></th></tr></thead>
      <tbody>${endpoints.map((e) => {
        const url = webhookBase + e.slug + (e.secret ? "?secret=" + encodeURIComponent(e.secret) : "");
        return `
        <tr><td>${esc(e.name)}</td>
        <td><code>${esc(url)}</code>
          <button class="ghost" data-copy="${esc(url)}">Copy</button></td>
        <td>${esc(e.ntfy_topic)}</td>
        <td>${e.enabled ? "yes" : "<span class='muted'>no</span>"}</td>
        <td class="actions">
          <button data-test="${e.id}" class="ghost">Test</button>
          <button data-edit="${e.id}" class="ghost">Edit</button>
          <button data-del="${e.id}" class="ghost danger">Delete</button>
        </td></tr>`;
      }).join("")
        || "<tr><td colspan='5' class='muted'>No endpoints yet.</td></tr>"}
      </tbody></table>
    </section>
    <section class="card hidden" id="ep-editor"></section>
    <section class="card">
      <h2>Global settings</h2>
      <form id="global-form" class="grid">
        <label>ntfy server URL
          <input name="ntfy_server" value="${esc(settings.ntfy_server)}"
                 placeholder="https://ntfy.example.com"></label>
        <label>Log retention (days)
          <input name="retention_days" type="number" min="1" max="365"
                 value="${settings.retention_days}"></label>
        <button>Save</button>
      </form>
    </section>
    <section class="card">
      <h2>Admin password</h2>
      <p class="muted">${settings.auth_mode === "open"
        ? "No password set — set one below to protect this UI."
        : settings.auth_mode === "env"
          ? "Using the ADMIN_PASSWORD environment variable; setting a password here overrides it."
          : "Password is set. Forgot it? Run scripts/reset_password.py inside the container."}</p>
      <form id="pw-form" class="grid">
        <label>Current password
          <input name="current" type="password" autocomplete="current-password"></label>
        <label>New password (min 8 chars)
          <input name="new" type="password" minlength="8" required
                 autocomplete="new-password"></label>
        <button>Change password</button>
      </form>
    </section></div>`;

  const page = $("#settings-page");

  const ruleRow = (level = "", rule = {}) => `<tr>
    <td><input class="rule-level" value="${esc(level)}" placeholder="WARN"></td>
    <td><select class="rule-priority">${PRIORITIES.map((p) =>
      `<option ${p === (rule.priority || "default") ? "selected" : ""}>${p}</option>`).join("")}
    </select></td>
    <td><input class="rule-tags" value="${esc((rule.extra_tags || []).join(","))}"
        placeholder="warning,fire"></td>
    <td><button type="button" class="ghost rule-del">✕</button></td></tr>`;

  function openEditor(endpoint) {
    const box = $("#ep-editor", page);
    box.classList.remove("hidden");
    const e = endpoint || {
      name: "", slug: "", ntfy_topic: "", ntfy_server: "", title_template: "",
      message_template: "{payload}", level_field: "", rules: {},
      default_priority: "default", tags: "", enabled: true, secret: "",
    };
    box.innerHTML = `
      <h2>${endpoint ? "Edit" : "New"} endpoint</h2>
      ${endpoint ? "" : `<label>Start from preset
        <select id="ep-preset"><option value="">—</option>
        ${presets.map((p) => `<option value="${esc(p.key)}">${esc(p.label)}</option>`).join("")}
        </select></label>`}
      <form id="ep-form" class="grid">
        <label>Name <input name="name" required value="${esc(e.name)}"></label>
        <label>Slug (URL path) <input name="slug" required
          pattern="[a-z0-9][a-z0-9_-]{0,63}" value="${esc(e.slug)}"></label>
        <label>ntfy topic <input name="ntfy_topic" required value="${esc(e.ntfy_topic)}"></label>
        <label>ntfy token${endpoint && e.ntfy_token_set
          ? ` <span class="muted">(saved ${esc(e.ntfy_token_hint)} — blank keeps it)</span>` : ""}
          <input name="ntfy_token" type="password" autocomplete="off"
                 placeholder="${endpoint && e.ntfy_token_set ? "unchanged" : "tk_…"}"></label>
        <label>Webhook secret <span class="muted">(optional — empty disables auth)</span>
          <span class="field-row"><input name="secret" value="${esc(e.secret || "")}"
            placeholder="appended to the URL as ?secret=…">
          <button type="button" id="secret-gen" class="ghost">Generate</button></span></label>
        <label>ntfy server override
          <input name="ntfy_server" value="${esc(e.ntfy_server || "")}"
                 placeholder="uses global setting"></label>
        <label>Title template <input name="title_template" value="${esc(e.title_template)}"></label>
        <label>Message template
          <textarea name="message_template">${esc(e.message_template)}</textarea></label>
        <label>Level field <input name="level_field" value="${esc(e.level_field)}"
          placeholder="event.level|level"></label>
        <label>Base tags <input name="tags" value="${esc(e.tags)}" placeholder="webhook,alerts"></label>
        <label>Default priority <select name="default_priority">${PRIORITIES.map((p) =>
          `<option ${p === e.default_priority ? "selected" : ""}>${p}</option>`).join("")}
        </select></label>
        <label class="check"><input type="checkbox" name="enabled"
          ${e.enabled ? "checked" : ""}> Enabled</label>
        <fieldset><legend>Level rules</legend>
          <table><thead><tr><th>Level</th><th>Priority</th><th>Extra tags</th><th></th></tr></thead>
          <tbody id="rule-rows">${Object.entries(e.rules)
            .map(([lvl, rule]) => ruleRow(lvl, rule)).join("")}</tbody></table>
          <button type="button" id="rule-add" class="ghost">Add rule</button>
        </fieldset>
        <div class="row"><button>Save</button>
          <button type="button" id="ep-cancel" class="ghost">Cancel</button></div>
      </form>`;

    const presetSelect = $("#ep-preset", box);
    if (presetSelect) presetSelect.addEventListener("change", () => {
      const preset = presets.find((p) => p.key === presetSelect.value);
      if (!preset) return;
      const form = $("#ep-form", box);
      for (const field of ["title_template", "message_template", "level_field",
                           "default_priority", "tags"]) {
        form.elements[field].value = preset[field];
      }
      $("#rule-rows", box).innerHTML = Object.entries(preset.rules)
        .map(([lvl, rule]) => ruleRow(lvl, rule)).join("");
    });
    $("#rule-add", box).addEventListener("click", () =>
      $("#rule-rows", box).insertAdjacentHTML("beforeend", ruleRow()));
    $("#rule-rows", box).closest("table").addEventListener("click", (event) => {
      const del = event.target.closest(".rule-del");
      if (del) del.closest("tr").remove();
    });
    $("#ep-cancel", box).addEventListener("click", () => box.classList.add("hidden"));
    $("#secret-gen", box).addEventListener("click", () => {
      const bytes = crypto.getRandomValues(new Uint8Array(24));
      $("[name=secret]", box).value = btoa(String.fromCharCode(...bytes))
        .replaceAll("+", "-").replaceAll("/", "_").replace(/=+$/, "");
    });

    $("#ep-form", box).addEventListener("submit", async (event) => {
      event.preventDefault();
      const form = event.target;
      const rules = {};
      for (const row of $("#rule-rows", box).querySelectorAll("tr")) {
        const level = $(".rule-level", row).value.trim();
        if (!level) continue;
        rules[level] = {
          priority: $(".rule-priority", row).value,
          extra_tags: $(".rule-tags", row).value.split(",")
            .map((t) => t.trim()).filter(Boolean),
        };
      }
      const tokenInput = form.elements.ntfy_token.value;
      const body = {
        name: form.elements.name.value, slug: form.elements.slug.value,
        ntfy_topic: form.elements.ntfy_topic.value,
        ntfy_token: endpoint ? (tokenInput === "" ? null : tokenInput) : tokenInput,
        ntfy_server: form.elements.ntfy_server.value || null,
        title_template: form.elements.title_template.value,
        message_template: form.elements.message_template.value,
        level_field: form.elements.level_field.value,
        rules,
        default_priority: form.elements.default_priority.value,
        tags: form.elements.tags.value,
        enabled: form.elements.enabled.checked,
        secret: form.elements.secret.value.trim(),
      };
      try {
        if (endpoint) await api(`/api/endpoints/${endpoint.id}`, { method: "PUT", body });
        else await api("/api/endpoints", { body });
        toast("Endpoint saved");
        route();
      } catch (err) { toast(err.message, true); }
    });
  }

  page.addEventListener("click", async (event) => {
    const btn = event.target.closest("button");
    if (!btn) return;
    if (btn.dataset.copy) {
      const ok = await copyText(btn.dataset.copy);
      toast(ok ? "Webhook URL copied"
        : "Copy failed — select the URL and copy manually", !ok);
    } else if (btn.dataset.test) {
      btn.disabled = true;
      try {
        const outcome = await api(`/api/endpoints/${btn.dataset.test}/test`, { body: {} });
        toast(outcome.status === "delivered" ? "Test notification delivered"
          : `Test failed: ${outcome.error}`, outcome.status !== "delivered");
      } catch (err) { toast(err.message, true); }
      btn.disabled = false;
    } else if (btn.dataset.edit) {
      openEditor(endpoints.find((e) => e.id === Number(btn.dataset.edit)));
    } else if (btn.dataset.del) {
      const target = endpoints.find((e) => e.id === Number(btn.dataset.del));
      if (confirm(`Delete endpoint "${target.name}" and its logs?`)) {
        await api(`/api/endpoints/${target.id}`, { method: "DELETE" });
        toast("Endpoint deleted");
        route();
      }
    } else if (btn.id === "ep-new") {
      openEditor(null);
    }
  });

  $("#global-form", page).addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.target;
    try {
      await api("/api/settings", { method: "PUT", body: {
        ntfy_server: form.elements.ntfy_server.value,
        retention_days: Number(form.elements.retention_days.value),
      } });
      toast("Settings saved");
    } catch (err) { toast(err.message, true); }
  });

  $("#pw-form", page).addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.target;
    try {
      await api("/api/password", { body: {
        current: form.elements.current.value, new: form.elements.new.value,
      } });
      toast("Password changed");
      form.reset();
      route();
    } catch (err) { toast(err.message, true); }
  });
};

views.reports = async (root) => {
  let range = "7d";
  root.innerHTML = `<div id="reports-page">
    <section class="card">
      <div class="row-between"><h2>Delivery report</h2>
        <div class="row" id="range-buttons">
          ${["24h", "7d", "30d"].map((r) =>
            `<button class="ghost ${r === range ? "active" : ""}" data-range="${r}">${r}</button>`
          ).join("")}
        </div></div>
      <div id="report-body"></div>
    </section></div>`;
  const page = $("#reports-page");

  async function load() {
    const summary = await api(`/api/reports/summary?range=${range}&tz_offset=${tzOffsetMinutes()}`);
    const max = Math.max(...summary.buckets.map((b) => b.delivered + b.failed + b.rejected), 1);
    $("#report-body", page).innerHTML = `
      ${summary.buckets.length ? `<div class="chart">${summary.buckets.map((b) => {
        const bad = b.failed + b.rejected;
        return `<div class="col" title="${esc(b.bucket)}: ${b.delivered} ok, ${bad} failed/rejected">
          <div class="seg failed" style="height:${(bad / max) * 100}%"></div>
          <div class="seg delivered" style="height:${(b.delivered / max) * 100}%"></div>
        </div>`;
      }).join("")}</div>` : "<p class='muted'>No deliveries in this range.</p>"}
      <table><thead><tr><th>Endpoint</th><th>Delivered</th><th>Failed</th>
        <th>Rejected</th><th>Total</th><th>Success</th></tr></thead>
      <tbody>${summary.endpoints.map((e) => `
        <tr><td>${esc(e.name)}</td><td>${e.delivered}</td>
        <td class="${e.failed ? "error" : ""}">${e.failed}</td>
        <td>${e.rejected}</td><td>${e.total}</td>
        <td>${e.success_rate == null ? "—" : e.success_rate + "%"}</td></tr>`).join("")}
      </tbody></table>`;
  }

  $("#range-buttons", page).addEventListener("click", (event) => {
    const btn = event.target.closest("button[data-range]");
    if (!btn) return;
    range = btn.dataset.range;
    page.querySelectorAll("[data-range]").forEach(
      (b) => b.classList.toggle("active", b === btn));
    load();
  });
  await load();
};

views.logs = async (root) => {
  const endpoints = await api("/api/endpoints");
  root.innerHTML = `<div id="logs-page">
    <section class="card">
      <form id="log-filters" class="row">
        <select name="endpoint_id" style="width:auto"><option value="">All endpoints</option>
          ${endpoints.map((e) => `<option value="${e.id}">${esc(e.name)}</option>`).join("")}
        </select>
        <select name="status" style="width:auto"><option value="">Any status</option>
          ${["delivered", "failed", "rejected"].map((s) => `<option>${s}</option>`).join("")}
        </select>
        <input name="q" placeholder="Search text" style="width:12rem">
        <button>Apply</button>
        <label class="check"><input type="checkbox" id="log-auto"> Auto-refresh</label>
      </form>
      <table><thead><tr><th>Time</th><th>Endpoint</th><th>Status</th><th>Title</th>
        <th>HTTP</th><th>Attempts</th><th>ms</th></tr></thead>
        <tbody id="log-rows"></tbody></table>
      <button id="log-more" class="ghost hidden">Load more</button>
    </section>
    <section class="card hidden" id="log-detail"></section>
  </div>`;
  const page = $("#logs-page");
  let items = [];

  async function load(append = false) {
    const form = $("#log-filters", page);
    const params = new URLSearchParams();
    for (const name of ["endpoint_id", "status", "q"]) {
      if (form.elements[name].value) params.set(name, form.elements[name].value);
    }
    if (append && items.length) params.set("before_id", items[items.length - 1].id);
    const batch = (await api(`/api/logs?${params}`)).items;
    items = append ? items.concat(batch) : batch;
    $("#log-more", page).classList.toggle("hidden", batch.length < 50);
    $("#log-rows", page).innerHTML = items.map((d) => `
      <tr data-id="${d.id}" class="clickable">
        <td>${fmtTime(d.received_at)}</td><td>${esc(d.endpoint_name)}</td>
        <td><span class="badge ${esc(d.status)}">${esc(d.status)}</span></td>
        <td>${esc(d.title)}</td><td>${d.ntfy_status ?? "—"}</td>
        <td>${d.attempts}</td><td>${d.duration_ms}</td></tr>`).join("")
      || "<tr><td colspan='7' class='muted'>No deliveries match.</td></tr>";
  }

  $("#log-filters", page).addEventListener("submit", (event) => {
    event.preventDefault();
    load();
  });
  $("#log-more", page).addEventListener("click", () => load(true));
  $("#log-auto", page).addEventListener("change", (event) => {
    clearInterval(logsTimer);
    if (event.target.checked) logsTimer = setInterval(() => load(), 5000);
  });
  page.addEventListener("click", async (event) => {
    const row = event.target.closest("tr[data-id]");
    if (!row) return;
    const detail = await api(`/api/logs/${row.dataset.id}`);
    const box = $("#log-detail", page);
    box.classList.remove("hidden");
    box.innerHTML = `
      <h2>Delivery #${detail.id}</h2>
      <p><span class="badge ${esc(detail.status)}">${esc(detail.status)}</span>
        ${fmtTime(detail.received_at)} · from ${esc(detail.source_ip) || "unknown"}
        · ${detail.attempts} attempt(s) · ${detail.duration_ms} ms
        ${detail.ntfy_status ? `· ntfy HTTP ${detail.ntfy_status}` : ""}</p>
      ${detail.error ? `<p class="error">${esc(detail.error)}</p>` : ""}
      <h3>Notification sent</h3>
      <pre>${esc(detail.title)}\n${esc(detail.message)}</pre>
      <h3>Received payload</h3>
      <pre>${esc(detail.request_body) || "(empty)"}</pre>`;
    box.scrollIntoView({ behavior: "smooth" });
  });
  await load();
};

route();
