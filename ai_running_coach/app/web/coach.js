"use strict";

const byId = id => document.getElementById(id);
const connection = byId("connection-form");
const profile = byId("profile-form");
const schedule = byId("schedule-form");
const field = (form, name) => form.elements.namedItem(name);
const weekdays = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];
let state = {};
let csrf = "";
let working = false;
let initialized = false;
let dashboardRevision = "";
let displayedRevision = null;

for (const day of weekdays) {
  const label = document.createElement("label");
  const input = document.createElement("input");
  input.type = "checkbox";
  input.name = "days";
  input.value = day;
  label.append(input, day.slice(0, 3));
  byId("days").append(label);
}
for (let zone = 1; zone <= 5; zone += 1) {
  const row = document.createElement("div");
  row.className = "zone-row";
  const name = document.createElement("span");
  name.className = "zone-name";
  name.textContent = "Zone " + zone;
  row.append(name);
  for (const edge of ["low", "high"]) {
    const input = document.createElement("input");
    input.name = "zone_" + zone + "_" + edge;
    input.type = "number";
    input.min = "1";
    input.max = "300";
    input.placeholder = edge === "low" ? "Min" : "Max";
    input.setAttribute("aria-label", "Zone " + zone + " " + edge + " bpm");
    row.append(input);
  }
  byId("zone-fields").append(row);
}
const zones = typeof Intl.supportedValuesOf === "function" ? Intl.supportedValuesOf("timeZone") : [];
for (const name of ["UTC", ...zones]) {
  const option = document.createElement("option");
  option.value = name;
  byId("timezones").append(option);
}

async function api(path, payload) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 10000);
  try {
    const options = {cache: "no-store", credentials: "same-origin", signal: controller.signal};
    if (payload !== undefined) {
      options.method = "POST";
      options.headers = {"Content-Type": "application/json", "X-Coach-CSRF": csrf};
      options.body = JSON.stringify(payload);
    }
    const response = await fetch("./api/" + path, options);
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "The coach could not complete this request.");
    return result;
  } finally {
    clearTimeout(timer);
  }
}

function connectionHints() {
  const config = state.config || {};
  const provider = field(connection, "provider").value;
  byId("provider-key-hint").textContent = config["has_" + provider + "_api_key"]
    ? "A key is saved. Leave blank to keep it." : "Enter the API key for this provider.";
  byId("garmin-key-hint").textContent = config.has_garmin_api_key
    ? "A key is saved. Leave blank to keep it." : "Use the Personal API key from your Garmin account.";
  byId("garmin-fields").hidden = field(connection, "source").value !== "garmin";
}

function draw(next) {
  state = next;
  csrf = next.csrf || csrf;
  const last = next.last_run || {};
  const lastStatus = last.status || last.state || "";
  if (lastStatus === "success" && last.finished_at) dashboardRevision = last.finished_at;
  const busy = Boolean(next.running || next.queued);
  let title = "Ready for a plan";
  let detail = "Generate a plan when your settings are ready.";
  if (next.worker_ok === false) {
    title = "Coach worker needs attention";
    detail = next.worker_error || "Restart the app from Home Assistant.";
  } else if (next.running) {
    title = "Coach is running";
    detail = "This page checks for updates automatically.";
  } else if (next.queued) {
    title = "Coach run queued";
    detail = "Your request is waiting for the worker.";
  } else if (!next.setup_ready || !next.profile_ready) {
    title = "Setup needs attention";
    detail = "Save your connection and athlete profile to continue.";
  } else if (["failed", "error", "input_required", "interrupted"].includes(lastStatus)) {
    title = ["provider_auth", "garmin_auth"].includes(last.code) || lastStatus === "input_required"
      ? "Input required" : "Last run failed";
    detail = last.message || last.error || "Check your connection and try again.";
  } else if (next.plan_ready) {
    title = "Your plan is ready";
    detail = next.schedule?.enabled
      ? "Daily review at " + next.schedule.time + " (" + next.schedule.timezone + ")."
      : "Daily reviews are paused.";
  }
  byId("run-title").textContent = title;
  byId("run-detail").textContent = detail;
  byId("run-detail").classList.remove("error");
  const issues = byId("issues");
  issues.replaceChildren();
  for (const issue of next.setup_issues || []) {
    const row = document.createElement("li");
    row.textContent = issue;
    issues.append(row);
  }
  issues.hidden = issues.children.length === 0;
  byId("new-plan").disabled = busy || working || next.worker_ok === false || !next.setup_ready || !next.profile_ready;
  byId("run-now").disabled = byId("new-plan").disabled || !next.plan_ready;
  byId("dashboard-tab").disabled = !next.dashboard_ready;
  field(schedule, "enabled").disabled = !next.plan_ready;
  connectionHints();
  updateDashboard();
}

async function refresh() {
  if (working) return;
  try {
    const next = await api("status");
    if (working) return;
    draw(next);
    if (!initialized) {
      const saved = await api("profile");
      for (const [key, value] of Object.entries(next.config || {})) {
        if (["provider", "model", "source", "garmin_region"].includes(key)) field(connection, key).value = value;
      }
      field(schedule, "enabled").checked = Boolean(next.schedule?.enabled);
      field(schedule, "time").value = next.schedule?.time || "03:00";
      field(schedule, "timezone").value = next.schedule?.timezone || "UTC";
      for (const key of ["name", "goal", "experience", "injuries", "event", "race_date", "target_time"]) {
        field(profile, key).value = saved[key] || "";
      }
      for (const input of byId("days").querySelectorAll("input")) input.checked = (saved.days || []).includes(input.value);
      for (const [zone, bounds] of Object.entries(saved.zones || {})) {
        field(profile, "zone_" + zone + "_low").value = bounds[0];
        field(profile, "zone_" + zone + "_high").value = bounds[1];
      }
      field(profile, "zone_mode").value = Object.keys(saved.zones || {}).length ? "known" : "unknown";
      byId("zones").hidden = field(profile, "zone_mode").value !== "known";
      initialized = true;
      connectionHints();
      if (next.setup_ready && next.profile_ready && next.dashboard_ready) selectView(true);
    }
  } catch {
    byId("run-title").textContent = "Coach connection unavailable";
    byId("run-detail").textContent = "Check Home Assistant and refresh the page. This page will retry.";
    byId("run-detail").classList.add("error");
    byId("new-plan").disabled = true;
    byId("run-now").disabled = true;
  }
}

function formAction(form, endpoint, read, onSaved) {
  form.addEventListener("submit", async event => {
    event.preventDefault();
    if (working || !initialized) return;
    working = true;
    const feedback = form.querySelector(".form-result");
    const submit = form.querySelector("button[type=submit]");
    submit.disabled = true;
    feedback.classList.remove("error");
    feedback.textContent = "Saving…";
    try {
      const result = await api(endpoint, read());
      if (onSaved) onSaved();
      draw(result);
      feedback.textContent = "Saved.";
    } catch (error) {
      feedback.textContent = error.message || "Could not save. Try again.";
      feedback.classList.add("error");
    } finally {
      working = false;
      submit.disabled = false;
      await refresh();
    }
  });
}

field(connection, "provider").addEventListener("change", () => {
  field(connection, "provider_key").value = "";
  field(connection, "model").value = field(connection, "provider").value === "gemini" ? "gemini-3.5-flash-lite" : "";
  connectionHints();
});
field(connection, "source").addEventListener("change", connectionHints);
field(profile, "zone_mode").addEventListener("change", () => { byId("zones").hidden = field(profile, "zone_mode").value !== "known"; });

formAction(connection, "config", () => {
  const get = key => field(connection, key).value;
  return {provider: get("provider"), model: get("model"), source: get("source"),
    garmin_region: get("garmin_region"), garmin_api_key: get("garmin_api_key"),
    [get("provider") + "_api_key"]: get("provider_key")};
}, () => {
  field(connection, "provider_key").value = "";
  field(connection, "garmin_api_key").value = "";
});
formAction(profile, "profile", () => {
  const payload = {};
  for (const key of ["name", "goal", "experience", "injuries", "event", "race_date", "target_time"]) payload[key] = field(profile, key).value;
  payload.days = Array.from(byId("days").querySelectorAll("input:checked"), input => input.value);
  payload.zones = {};
  if (field(profile, "zone_mode").value === "known") {
    for (let zone = 1; zone <= 5; zone += 1) {
      const low = field(profile, "zone_" + zone + "_low").value;
      const high = field(profile, "zone_" + zone + "_high").value;
      if (low || high) payload.zones[zone] = [low ? Number(low) : null, high ? Number(high) : null];
    }
    if (!payload.zones[2]) throw new Error("Enter Zone 2 or choose unknown zones.");
  }
  return payload;
});
formAction(schedule, "schedule", () => ({
  enabled: field(schedule, "enabled").checked,
  time: field(schedule, "time").value,
  timezone: field(schedule, "timezone").value,
}));

async function run(action) {
  if (working) return;
  working = true;
  byId("new-plan").disabled = true;
  byId("run-now").disabled = true;
  try {
    const result = await api("action", {action});
    working = false;
    draw(result);
  } catch (error) {
    byId("run-detail").textContent = error.message;
    byId("run-detail").classList.add("error");
  } finally {
    working = false;
  }
}
byId("run-now").addEventListener("click", () => run("run_now"));
const confirm = byId("new-plan-confirm");
byId("new-plan").addEventListener("click", () => { confirm.returnValue = ""; confirm.showModal(); });
confirm.addEventListener("close", () => { if (confirm.returnValue === "confirm") run("new_plan"); });

function updateDashboard() {
  if (byId("dashboard-panel").hidden || !state.dashboard_ready || displayedRevision === dashboardRevision) return;
  byId("dashboard").src = "./dashboard?v=" + encodeURIComponent(dashboardRevision);
  displayedRevision = dashboardRevision;
}

function selectView(dashboard) {
  byId("setup-hero").hidden = dashboard;
  byId("settings").hidden = dashboard;
  byId("dashboard-panel").hidden = !dashboard;
  byId("dashboard-tab").setAttribute("aria-pressed", String(dashboard));
  byId("settings-tab").setAttribute("aria-pressed", String(!dashboard));
  updateDashboard();
}
byId("dashboard-tab").addEventListener("click", () => selectView(true));
byId("settings-tab").addEventListener("click", () => selectView(false));
refresh();
setInterval(refresh, 3500);
