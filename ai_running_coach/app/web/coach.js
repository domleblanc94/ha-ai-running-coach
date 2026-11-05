"use strict";

const byId = id => document.getElementById(id);
const connection = byId("connection-form");
const profile = byId("profile-form");
const schedule = byId("schedule-form");
const baseline = byId("baseline-form");
const field = (form, name) => form.elements.namedItem(name);
const weekdays = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];
const goals = [["A", "Primary"], ["B", "Secondary"], ["C", "Supporting"]];
const goalFields = [["event", "Goal name", "text", 150], ["race_date", "Date", "date", 0], ["target_time", "Existing or general target", "text", 500]];
const outcomeFields = [["A", "A outcome · Ideal"], ["B", "B outcome · Main"], ["C", "C outcome · Fallback"]];
const runStages = {
  sync: "Garmin sync", activities: "Activity processing", coaching: "Coaching",
  render: "Dashboard rendering", publication: "Dashboard publication", startup: "Startup",
  unknown: "Stage not identified",
};
let state = {};
let csrf = "";
let working = false;
let initialized = false;
let dashboardRevision = "";
let displayedRevision = null;
let candidateSignature = "";

for (const day of weekdays) {
  const label = document.createElement("label");
  const input = document.createElement("input");
  input.type = "checkbox";
  input.name = "days";
  input.value = day;
  label.append(input, day.slice(0, 3));
  byId("days").append(label);
}
for (const [priority, description] of goals) {
  const group = document.createElement("fieldset");
  const legend = document.createElement("legend");
  legend.textContent = priority + " goal · " + description;
  group.append(legend);
  const kindLabel = document.createElement("label");
  const kind = document.createElement("select");
  kind.name = "goal_" + priority + "_kind";
  kind.add(new Option("Training goal", "training"));
  kind.add(new Option("Race", "race"));
  kindLabel.append("Goal type", kind);
  group.append(kindLabel);
  for (const [key, title, type, length] of goalFields) {
    const label = document.createElement("label");
    const input = document.createElement("input");
    input.name = "goal_" + priority + "_" + key;
    input.type = type;
    if (length) input.maxLength = length;
    label.append(title, input);
    group.append(label);
  }
  const outcomes = document.createElement("details");
  const summary = document.createElement("summary");
  summary.textContent = "Race outcomes (optional)";
  outcomes.append(summary);
  for (const [key, title] of outcomeFields) {
    const label = document.createElement("label");
    const input = document.createElement("input");
    input.name = "goal_" + priority + "_outcome_" + key;
    input.maxLength = 250;
    label.append(title, input);
    outcomes.append(label);
  }
  const defaultTarget = document.createElement("small");
  defaultTarget.textContent = "Leave the outcomes and target blank to use Just finish.";
  outcomes.append(defaultTarget);
  group.append(outcomes);
  const showOutcomes = () => { outcomes.hidden = kind.value !== "race"; };
  kind.addEventListener("change", showOutcomes);
  showOutcomes();
  const clear = document.createElement("button");
  clear.type = "button";
  clear.className = "secondary";
  clear.textContent = "Clear " + priority + " goal";
  clear.addEventListener("click", () => {
    for (const [key] of goalFields) field(profile, "goal_" + priority + "_" + key).value = "";
    for (const [key] of outcomeFields) field(profile, "goal_" + priority + "_outcome_" + key).value = "";
    kind.value = "training";
    showOutcomes();
  });
  group.append(clear);
  byId("goal-fields").append(group);
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
    title = ["provider_auth", "provider_config", "garmin_auth"].includes(last.code) || lastStatus === "input_required"
      ? "Input required" : next.plan_ready ? "Last run failed" : "First plan could not be generated";
    detail = last.message || "Check the app logs for the run status before trying again.";
  } else if (next.plan_ready) {
    title = "Your plan is ready";
    detail = next.schedule?.enabled
      ? "Daily review at " + next.schedule.time + " (" + next.schedule.timezone + ")."
      : "Daily reviews are paused.";
  }
  byId("run-title").textContent = title;
  byId("run-detail").textContent = detail;
  const failed = !busy && ["failed", "error", "input_required", "interrupted"].includes(lastStatus);
  byId("run-detail").classList.toggle("error", failed || next.worker_ok === false);
  const diagnostic = byId("run-diagnostic");
  const timestamp = new Date(last.finished_at || last.started_at || "");
  const parts = [];
  if (runStages[last.stage]) parts.push(runStages[last.stage]);
  if (typeof last.code === "string" && /^[a-z0-9_]+$/.test(last.code)) parts.push(last.code);
  if (!Number.isNaN(timestamp.valueOf())) parts.push(timestamp.toLocaleString());
  if (Number.isSafeInteger(last.request_attempts) && last.request_attempts >= 0) {
    parts.push("AI request attempts this run: " + last.request_attempts);
  } else {
    parts.push("AI request attempts this run: unknown");
  }
  diagnostic.hidden = !failed || parts.length === 0;
  diagnostic.textContent = parts.join(" · ");
  const firstPlan = byId("first-plan-hint");
  firstPlan.hidden = busy || !next.setup_ready || !next.profile_ready || next.plan_ready;
  firstPlan.textContent = next.hr_learning?.enabled
    ? "Plan baseline runs to create your first plan and enable Run now."
    : "Generate a new plan to create your first schedule and enable Run now.";
  const issues = byId("issues");
  issues.replaceChildren();
  for (const issue of next.setup_issues || []) {
    const row = document.createElement("li");
    row.textContent = issue;
    issues.append(row);
  }
  issues.hidden = issues.children.length === 0;
  byId("new-plan").disabled = busy || working || next.worker_ok === false || !next.setup_ready || !next.profile_ready;
  byId("new-plan").textContent = next.hr_learning?.enabled ? "Plan baseline runs" : "Generate new plan";
  byId("run-now").disabled = byId("new-plan").disabled || !next.plan_ready;
  byId("dashboard-tab").disabled = !next.dashboard_ready;
  field(schedule, "enabled").disabled = !next.plan_ready;
  connectionHints();
  drawBaseline(next.hr_learning, busy);
  updateDashboard();
}

function drawBaseline(learning, busy) {
  baseline.hidden = !learning?.enabled;
  if (!learning?.enabled) return;
  const candidates = learning.candidates || [];
  byId("baseline-inputs").hidden = candidates.length === 0;
  let detail = "Plan your baseline runs, then sync a completed run from Garmin.";
  if (candidates.length) {
    const count = learning.feedback_count;
    detail = count
      ? count + (count === 1 ? " baseline observation recorded. " : " baseline observations recorded. ")
        + "Run a review to share your observations with the coach."
      : "Choose a completed run and record how it felt.";
  } else if (state.config?.source === "offline") {
    detail = "The coach can plan effort-based baseline runs. Select Garmin sync to attach observations to recorded runs here.";
  }
  byId("baseline-status").textContent = detail;
  byId("baseline-review").hidden = !learning.review?.summary;
  byId("baseline-review-summary").textContent = learning.review?.summary
    ? (learning.review.completed_at || "").slice(0, 10) + " · " + learning.review.summary
    : "";
  baseline.querySelector("button[type=submit]").disabled = busy;
  const select = field(baseline, "activity_id");
  const signature = JSON.stringify(candidates);
  if (signature === candidateSignature) return;
  candidateSignature = signature;
  const selected = select.value;
  select.replaceChildren(new Option("Select a run", ""));
  for (const run of candidates) {
    const metrics = [];
    if (run.moving_minutes != null) metrics.push(run.moving_minutes + " min");
    if (run.average_hr_bpm != null) metrics.push(run.average_hr_bpm + " bpm avg");
    if (run.feedback) metrics.push("observation saved");
    const label = run.date + (metrics.length ? " · " + metrics.join(" · ") : "");
    select.add(new Option(label, String(run.activity_id)));
  }
  if (candidates.some(run => String(run.activity_id) === selected)) select.value = selected;
}

field(baseline, "activity_id").addEventListener("change", () => {
  const run = state.hr_learning?.candidates?.find(item => String(item.activity_id) === field(baseline, "activity_id").value);
  const saved = run?.feedback;
  field(baseline, "rpe").value = saved?.rpe ?? "";
  field(baseline, "talk_test").value = saved?.talk_test || "unknown";
  field(baseline, "sensor").value = saved?.sensor || "unknown";
});

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
      for (const key of ["name", "goal", "experience", "injuries"]) {
        field(profile, key).value = saved[key] || "";
      }
      for (const [priority] of goals) {
        const savedGoal = saved.goals?.[priority] || {};
        const kind = field(profile, "goal_" + priority + "_kind");
        kind.value = savedGoal.kind || "training";
        kind.dispatchEvent(new Event("change"));
        for (const [key] of goalFields) {
          field(profile, "goal_" + priority + "_" + key).value = savedGoal[key] || "";
        }
        for (const [key] of outcomeFields) {
          field(profile, "goal_" + priority + "_outcome_" + key).value = savedGoal.outcomes?.[key] || "";
        }
      }
      for (const input of byId("days").querySelectorAll("input")) input.checked = (saved.days || []).includes(input.value);
      for (const [zone, bounds] of Object.entries(saved.zones || {})) {
        field(profile, "zone_" + zone + "_low").value = bounds[0];
        field(profile, "zone_" + zone + "_high").value = bounds[1];
      }
      field(profile, "zone_mode").value = saved.zone_mode || "unknown";
      updateZoneMode();
      initialized = true;
      connectionHints();
      if (next.setup_ready && next.profile_ready && next.dashboard_ready) selectView(true);
    }
  } catch {
    byId("run-title").textContent = "Coach connection unavailable";
    byId("run-detail").textContent = "Check Home Assistant and refresh the page. This page will retry.";
    byId("run-detail").classList.add("error");
    byId("run-diagnostic").hidden = true;
    byId("first-plan-hint").hidden = true;
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
function updateZoneMode() {
  const mode = field(profile, "zone_mode").value;
  byId("zones").hidden = mode !== "known";
  byId("zone-learn-hint").hidden = mode !== "learn";
}
field(profile, "zone_mode").addEventListener("change", updateZoneMode);

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
  for (const key of ["name", "goal", "experience", "injuries"]) payload[key] = field(profile, key).value;
  payload.goals = {};
  for (const [priority] of goals) {
    const kind = field(profile, "goal_" + priority + "_kind").value;
    payload.goals[priority] = {kind, outcomes: {}};
    for (const [key] of goalFields) {
      payload.goals[priority][key] = field(profile, "goal_" + priority + "_" + key).value;
    }
    if (kind === "race") {
      for (const [key] of outcomeFields) {
        payload.goals[priority].outcomes[key] = field(profile, "goal_" + priority + "_outcome_" + key).value;
      }
    }
  }
  payload.days = Array.from(byId("days").querySelectorAll("input:checked"), input => input.value);
  payload.zone_mode = field(profile, "zone_mode").value;
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
formAction(baseline, "baseline-feedback", () => ({
  activity_id: Number(field(baseline, "activity_id").value),
  rpe: Number(field(baseline, "rpe").value),
  talk_test: field(baseline, "talk_test").value,
  sensor: field(baseline, "sensor").value,
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
byId("new-plan").addEventListener("click", () => {
  const learning = state.hr_learning?.enabled;
  byId("plan-confirm-title").textContent = learning ? "Plan baseline runs?" : "Generate a new training plan?";
  byId("plan-confirm-detail").textContent = learning
    ? "This uses your selected model API to plan easy baseline runs guided by effort. A valid plan replaces the upcoming block and preserves your past sessions and saved ranges."
    : "This uses your selected model API. A valid response replaces the upcoming block and preserves your past sessions.";
  confirm.returnValue = "";
  confirm.showModal();
});
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
