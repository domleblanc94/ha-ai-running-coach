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
const sportNames = {running: "Running", cycling: "Cycling", swimming: "Swimming", multisport: "Triathlon", triathlon: "Triathlon"};
const sportSettingEdits = new Set();
const goalSportEdits = new Set();
let savedGoals = {};
let runningZonesEdited = false;
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
let setupWasReady = false;
let profileRequest = 0;
let downloading = false;
let longRunPreferenceEdited = false;
let savedLongRunPreference = "";

function savedLongRunNote() {
  byId("long-run-saved-note").hidden = !savedLongRunPreference;
  byId("long-run-saved-note").textContent = "Saved preference: " + savedLongRunPreference
    + ". This stays unchanged until you edit the preferred days or choose No preference and save.";
}

for (const day of weekdays) {
  const label = document.createElement("label");
  const input = document.createElement("input");
  input.type = "checkbox";
  input.name = "days";
  input.value = day;
  label.append(input, day.slice(0, 3));
  byId("days").append(label);
  const preference = document.createElement("label");
  const choice = document.createElement("input");
  choice.type = "checkbox";
  choice.name = "long_run_days";
  choice.value = day;
  preference.append(choice, day.slice(0, 3));
  byId("long-run-days").append(preference);
}
byId("clear-long-run-days").addEventListener("click", () => {
  for (const input of byId("long-run-days").querySelectorAll("input")) input.checked = false;
  longRunPreferenceEdited = true;
});
byId("long-run-days").addEventListener("change", () => { longRunPreferenceEdited = true; });
byId("days").addEventListener("change", () => {
  const selected = Array.from(byId("days").querySelectorAll("input:checked"), input => input.value);
  byId("long-run-hint").textContent = "Leave all blank for no preference. Multiple days are alternatives for your long run, not extra long runs."
    + (selected.length ? " Choose from your available days: " + selected.join(", ") + "." : " With availability unset, any day can be a preference.");
});
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
  const sportLabel = document.createElement("label");
  const sport = document.createElement("select");
  sport.name = "goal_" + priority + "_sport";
  for (const key of ["running", "cycling", "swimming", "multisport"]) sport.add(new Option(sportNames[key], key));
  sport.addEventListener("change", () => goalSportEdits.add(priority));
  sportLabel.append("Goal sport", sport);
  group.append(sportLabel);
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
    sport.value = field(profile, "training_mode").value === "triathlon" ? "multisport" : field(profile, "training_mode").value;
    goalSportEdits.add(priority);
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
    input.max = "255";
    input.placeholder = edge === "low" ? "Min" : "Max";
    input.setAttribute("aria-label", "Zone " + zone + " " + edge + " bpm");
    row.append(input);
  }
  byId("zone-fields").append(row);
}

function settingInput(sport, key, labelText, type = "text", options = null) {
  const label = document.createElement("label");
  const input = document.createElement(options ? "select" : "input");
  input.name = sport + "_" + key;
  if (options) for (const [value, title] of options) input.add(new Option(title, value));
  else { input.type = type; if (type === "number") { input.min = "0"; input.step = "any"; } }
  input.addEventListener("change", () => sportSettingEdits.add(sport + "." + (key.startsWith("pool_") ? "pool" : key)));
  label.append(labelText, input);
  return label;
}
function settingDays(sport, key, title) {
  const group = document.createElement("fieldset");
  group.className = "full";
  const legend = document.createElement("legend"); legend.textContent = title; group.append(legend);
  const choices = document.createElement("div"); choices.className = "days";
  for (const day of weekdays) {
    const label = document.createElement("label"), input = document.createElement("input");
    input.type = "checkbox"; input.name = sport + "_" + key; input.value = day;
    input.addEventListener("change", () => sportSettingEdits.add(sport + "." + key));
    label.append(input, day.slice(0, 3)); choices.append(label);
  }
  group.append(choices);
  return group;
}
for (const sport of ["running", "cycling", "swimming"]) {
  const group = document.createElement("fieldset");
  group.className = "full"; group.dataset.sportSettings = sport;
  const legend = document.createElement("legend"); legend.textContent = sportNames[sport]; group.append(legend);
  const grid = document.createElement("div"); grid.className = "grid";
  grid.append(settingDays(sport, "available_days", "Available days for " + sport));
  if (sport === "cycling") {
    grid.append(settingDays(sport, "preferred_long_days", "Preferred long-ride days · alternatives"));
    grid.append(settingInput(sport, "ftp_watts", "FTP (watts)", "number"));
  }
  if (sport === "swimming") {
    grid.append(settingInput(sport, "css_seconds_per_100m", "Critical swim speed pace (m:ss per 100 m)"));
    grid.append(settingInput(sport, "sub_sport", "Usual swimming setting", "text", [["", "Unknown"], ["lap_swimming", "Pool"], ["open_water", "Open water"]]));
    grid.append(settingInput(sport, "pool_length", "Pool length (optional)", "number"));
    grid.append(settingInput(sport, "pool_unit", "Pool length unit", "text", [["m", "Metres"], ["yd", "Yards"]]));
    const hint = document.createElement("p"); hint.className = "hint full";
    hint.textContent = "Pool workout downloads need your actual pool length. Open-water sessions stay in your plan and do not offer FIT downloads."; grid.append(hint);
  }
  if (sport !== "running") {
    const zoneGroup = document.createElement("fieldset"); zoneGroup.className = "full";
    const zoneLegend = document.createElement("legend"); zoneLegend.textContent = sportNames[sport] + " heart-rate zones · optional bpm"; zoneGroup.append(zoneLegend);
    const ranges = document.createElement("div"); ranges.className = "zone-grid";
    for (let zone = 1; zone <= 5; zone++) {
      const row = document.createElement("div"); row.className = "zone-row";
      const title = document.createElement("span"); title.className = "zone-name"; title.textContent = "Zone " + zone; row.append(title);
      for (const side of ["low", "high"]) {
        const input = document.createElement("input"); input.type = "number"; input.min = "1"; input.max = "255";
        input.name = sport + "_zone_" + zone + "_" + side; input.placeholder = side === "low" ? "Min" : "Max";
        input.setAttribute("aria-label", sportNames[sport] + " Zone " + zone + " " + side + " bpm");
        input.addEventListener("change", () => sportSettingEdits.add(sport + ".zones")); row.append(input);
      }
      ranges.append(row);
    }
    zoneGroup.append(ranges); grid.append(zoneGroup);
  }
  group.append(grid); byId("sport-settings").append(group);
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

function factsList(entries, target) {
  target.replaceChildren();
  for (const [label, value] of entries) {
    const term = document.createElement("dt");
    const detail = document.createElement("dd");
    term.textContent = label;
    detail.textContent = value || "Not supplied";
    target.append(term, detail);
  }
}

function savedSetup() {
  const config = state.config || {};
  factsList([
    ["AI provider", {gemini: "Google Gemini", openai: "OpenAI", anthropic: "Anthropic"}[config.provider] || config.provider],
    ["Model", config.model], ["Activity source", config.source === "garmin" ? "Garmin Personal API" : "Offline"],
    ["Daily review", state.schedule?.enabled ? state.schedule.time + " (" + state.schedule.timezone + ")" : "Paused"],
    ["Time zone", state.schedule?.timezone],
    ["Daily AI request limit", config.daily_request_limit == null ? "Needs a valid setting" : config.daily_request_limit + " requests (UTC day)"],
  ], byId("setup-summary"));
}

async function showAthlete() {
  const request = ++profileRequest;
  byId("athlete-result").textContent = "Loading saved profile…";
  try {
    const saved = await api("profile-summary");
    if (request !== profileRequest) return;
    const data = saved.profile;
    byId("athlete-name").textContent = data.name || "Athlete profile";
    const container = byId("athlete-summary");
    container.replaceChildren();
    const card = (title, rows) => {
      const section = document.createElement("section");
      section.className = "card";
      const heading = document.createElement("h2");
      heading.textContent = title;
      const list = document.createElement("dl");
      list.className = "profile-facts";
      factsList(rows, list);
      section.append(heading, list);
      container.append(section);
    };
    const availability = saved.markdown.match(/^\*\*Weekly Availability:\*\*\s*(.*)$/m)?.[1];
    card("Your training", [["Training focus", sportNames[data.training_mode || "running"]], ["Training goal", data.goal], ["Experience", data.experience],
      ["Training days", data.days.length ? data.days.join(", ") : availability || "Not specified - coach may choose days"],
      ["Preferred long-run days", data.long_run_preference_text || (data.long_run_days?.length ? data.long_run_days.join(", ") + " (alternative days)" : "No preference")],
      ["Injuries and constraints", data.injuries]]);
    for (const [priority, label] of goals) {
      const goal = data.goals?.[priority];
      if (!goal?.event) continue;
      const rows = [["Goal", goal.event], ["Sport", sportNames[goal.sport || "running"]], ["Type", goal.kind === "race" ? "Race" : "Training milestone"]];
      if (goal.race_date) rows.push(["Date", goal.race_date]);
      if (goal.target_time) rows.push(["Target", goal.target_time]);
      for (const [key, title] of outcomeFields) if (goal.outcomes?.[key]) rows.push([title, goal.outcomes[key]]);
      if (goal.kind === "race" && !goal.target_time && !Object.keys(goal.outcomes || {}).length) rows.push(["Target", "Just finish"]);
      card(priority + " goal · " + label, rows);
    }
    const zoneRows = [["Mode", {learn: "Learning from baseline runs", known: "Configured ranges", unknown: "Unknown / use effort"}[data.zone_mode]]];
    for (const match of saved.markdown.matchAll(/^\*\*(Zone [1-5] \([^\n]+\)):\*\*[ \t]*(.*)$/gm)) zoneRows.push([match[1], match[2]]);
    if (data.training_mode && !["running", "triathlon"].includes(data.training_mode)) zoneRows.push(["Use", "Saved for running; not applied to this training focus"]);
    card("Running heart rate", zoneRows);
    for (const sport of ["running", "cycling", "swimming"]) {
      const settings = data.sport_settings?.[sport] || {};
      const rows = [["Sport days", settings.available_days?.join(", ") || "Use general availability"]];
      if (sport === "cycling") {
        rows.push(["Long-ride days", settings.preferred_long_days?.join(", ") || "No preference"]);
        rows.push(["FTP", settings.ftp_watts == null ? "Unknown" : settings.ftp_watts + " W"]);
      }
      if (sport === "swimming") {
        const css = settings.css_seconds_per_100m;
        rows.push(["CSS pace", css == null ? "Unknown" : Math.floor(css / 60) + ":" + String(css % 60).padStart(2, "0") + " /100 m"]);
        rows.push(["Setting", {lap_swimming: "Pool", open_water: "Open water"}[settings.sub_sport] || "Unknown"]);
        const length = settings.pool_length_metres;
        rows.push(["Pool length", length == null ? "Unknown" : Number((length / (settings.pool_length_unit === "yd" ? .9144 : 1)).toFixed(4)) + " " + (settings.pool_length_unit || "m")]);
      }
      for (const [number, bounds] of Object.entries(settings.zones || {})) rows.push(["HR Zone " + number, bounds.join("–") + " bpm"]);
      if (sport !== "running" || settings.available_days?.length) card(sportNames[sport] + " settings", rows);
    }
    byId("athlete-source").textContent = saved.markdown;
    savedSetup();
    byId("athlete-result").textContent = "";
  } catch {
    byId("athlete-result").textContent = "The saved profile could not be loaded. Select Athlete profile to try again.";
  }
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
  } else if (next.running_session_conflicts?.length || next.training_day_conflicts?.length) {
    title = "Saved plan needs review";
    detail = "Check the plan warnings and your saved training preferences before generating a replacement.";
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
  const budget = next.request_budget;
  const used = Number.isSafeInteger(budget?.used) ? String(budget.used) : "Unknown";
  const reset = budget?.resets_at ? budget.resets_at.slice(0, 10) + " at 00:00 UTC" : "00:00 UTC";
  byId("request-budget").textContent = "AI requests today: " + used + " / " + (budget?.limit ?? "unknown")
    + " normal allowance · Resets " + reset + ". Includes failed requests and any extra request.";
  const extraMessages = {
    available: "One extra request is available with your confirmation. Provider quotas still apply.",
    used: "Today's extra request has been used or reserved. It cannot be retried automatically.",
    allowance_remaining: "Your normal allowance is still available. One confirmed extra request is available after it is used.",
    busy: "Wait for the current action before confirming an extra request.",
    ledger_unavailable: "Request usage could not be read. Extra requests are disabled until it can be checked.",
    invalid_limit: "Set a valid daily AI request limit in Settings before running the coach.",
  };
  byId("extra-availability").textContent = extraMessages[budget?.extra_status] || "Checking request allowance…";
  byId("run-extra").hidden = !budget?.extra_available;
  byId("run-extra").disabled = byId("new-plan").disabled || !budget?.extra_available;
  const ready = Boolean(next.setup_ready && next.profile_ready);
  byId("dashboard-tab").disabled = !ready && !next.dashboard_ready;
  byId("athlete-tab").disabled = !next.profile_ready;
  byId("dashboard-empty").hidden = Boolean(next.dashboard_ready);
  byId("dashboard").hidden = !next.dashboard_ready;
  byId("plan-downloads").hidden = !next.plan_ready;
  byId("first-dashboard-plan").disabled = byId("new-plan").disabled;
  byId("first-dashboard-plan").textContent = byId("new-plan").textContent;
  const conflicts = next.training_day_conflicts || [];
  const multipleRuns = next.running_session_conflicts || [];
  byId("running-conflict-warning").hidden = multipleRuns.length === 0;
  byId("running-conflict-detail").textContent = multipleRuns.length
    ? "The saved plan has multiple running sessions on " + multipleRuns.length + (multipleRuns.length === 1 ? " day: " : " days: ")
      + multipleRuns.slice(0, 5).map(item => item.date + " (" + item.session_ids.length + " runs)").join(", ")
      + (multipleRuns.length > 5 ? ", and more." : ".") : "";
  byId("availability-warning").hidden = conflicts.length === 0;
  byId("availability-detail").textContent = conflicts.length ? "The saved plan includes " + conflicts.length
    + (conflicts.length === 1 ? " session" : " sessions") + " outside your selected training days: "
    + conflicts.slice(0, 5).map(item => item.weekday + " " + item.date).join(", ")
    + (conflicts.length > 5 ? ", and more." : ".") : "";
  field(schedule, "enabled").disabled = !next.plan_ready;
  connectionHints();
  if (!byId("athlete-panel").hidden) savedSetup();
  drawBaseline(next.hr_learning, busy);
  if (initialized && ready && !setupWasReady) selectView("dashboard");
  setupWasReady = ready;
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
        if (["provider", "model", "source", "garmin_region", "daily_request_limit"].includes(key)) field(connection, key).value = value;
      }
      field(schedule, "enabled").checked = Boolean(next.schedule?.enabled);
      field(schedule, "time").value = next.schedule?.time || "03:00";
      field(schedule, "timezone").value = next.schedule?.timezone || "UTC";
      for (const key of ["name", "goal", "experience", "injuries"]) {
        field(profile, key).value = saved[key] || "";
      }
      field(profile, "training_mode").value = saved.training_mode || "running";
      savedGoals = saved.goals || {};
      for (const [priority] of goals) {
        const savedGoal = saved.goals?.[priority] || {};
        const kind = field(profile, "goal_" + priority + "_kind");
        kind.value = savedGoal.kind || "training";
        kind.dispatchEvent(new Event("change"));
        field(profile, "goal_" + priority + "_sport").value = savedGoal.sport
          || (savedGoal.event ? "running" : field(profile, "training_mode").value === "triathlon" ? "multisport" : field(profile, "training_mode").value);
        for (const [key] of goalFields) {
          field(profile, "goal_" + priority + "_" + key).value = savedGoal[key] || "";
        }
        for (const [key] of outcomeFields) {
          field(profile, "goal_" + priority + "_outcome_" + key).value = savedGoal.outcomes?.[key] || "";
        }
      }
      for (const input of byId("days").querySelectorAll("input")) input.checked = (saved.days || []).includes(input.value);
      for (const input of byId("long-run-days").querySelectorAll("input")) input.checked = (saved.long_run_days || []).includes(input.value);
      savedLongRunPreference = saved.long_run_preference_text || "";
      longRunPreferenceEdited = false;
      savedLongRunNote();
      byId("days").dispatchEvent(new Event("change"));
      for (const [zone, bounds] of Object.entries(saved.zones || {})) {
        field(profile, "zone_" + zone + "_low").value = bounds[0];
        field(profile, "zone_" + zone + "_high").value = bounds[1];
      }
      field(profile, "zone_mode").value = saved.zone_mode || "unknown";
      for (const sport of ["running", "cycling", "swimming"]) {
        const settings = saved.sport_settings?.[sport] || {};
        for (const key of ["available_days", "preferred_long_days"]) {
          for (const input of profile.querySelectorAll('input[name="' + sport + '_' + key + '"]')) input.checked = (settings[key] || []).includes(input.value);
        }
        if (sport === "cycling") field(profile, "cycling_ftp_watts").value = settings.ftp_watts ?? "";
        if (sport === "swimming") {
          const css = settings.css_seconds_per_100m;
          field(profile, "swimming_css_seconds_per_100m").value = css == null ? "" : Math.floor(css / 60) + ":" + String(css % 60).padStart(2, "0");
          field(profile, "swimming_sub_sport").value = settings.sub_sport || "";
          field(profile, "swimming_pool_unit").value = settings.pool_length_unit || "m";
          field(profile, "swimming_pool_length").value = settings.pool_length_metres == null ? "" : Number((settings.pool_length_metres / (settings.pool_length_unit === "yd" ? .9144 : 1)).toFixed(4));
        }
        for (const [zone, bounds] of Object.entries(settings.zones || {})) {
          if (sport === "running") continue;
          field(profile, sport + "_zone_" + zone + "_low").value = bounds[0];
          field(profile, sport + "_zone_" + zone + "_high").value = bounds[1];
        }
      }
      sportSettingEdits.clear(); goalSportEdits.clear(); runningZonesEdited = false;
      updateTrainingMode();
      initialized = true;
      connectionHints();
      if (next.setup_ready && next.profile_ready) selectView("dashboard");
    }
  } catch {
    byId("run-title").textContent = "Coach connection unavailable";
    byId("run-detail").textContent = "Check Home Assistant and refresh the page. This page will retry.";
    byId("run-detail").classList.add("error");
    byId("run-diagnostic").hidden = true;
    byId("first-plan-hint").hidden = true;
    byId("new-plan").disabled = true;
    byId("run-now").disabled = true;
    byId("run-extra").disabled = true;
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
  const running = ["running", "triathlon"].includes(field(profile, "training_mode").value);
  byId("zones").hidden = !running || mode !== "known";
  byId("zone-learn-hint").hidden = !running || mode !== "learn";
}
function updateTrainingMode() {
  const mode = field(profile, "training_mode").value;
  const running = ["running", "triathlon"].includes(mode);
  byId("running-preferences").hidden = !running;
  byId("running-zone-mode-label").hidden = !running;
  for (const section of document.querySelectorAll("[data-sport-settings]")) section.hidden = mode !== "triathlon" && section.dataset.sportSettings !== mode;
  for (const [priority] of goals) {
    if (!savedGoals[priority]?.event && !field(profile, "goal_" + priority + "_event").value && !goalSportEdits.has(priority)) {
      field(profile, "goal_" + priority + "_sport").value = mode === "triathlon" ? "multisport" : mode;
    }
  }
  updateZoneMode();
}
field(profile, "training_mode").addEventListener("change", updateTrainingMode);
field(profile, "zone_mode").addEventListener("change", () => { runningZonesEdited = true; updateZoneMode(); });
byId("zone-fields").addEventListener("change", () => { runningZonesEdited = true; });

function sportSettingsPayload() {
  const result = {};
  for (const edit of sportSettingEdits) {
    const [sport, key] = edit.split(".");
    const settings = result[sport] ||= {};
    if (key === "available_days" || key === "preferred_long_days") {
      settings[key] = Array.from(profile.querySelectorAll('input[name="' + sport + '_' + key + '"]:checked'), input => input.value);
    } else if (key === "zones") {
      settings.zones = {};
      for (let zone = 1; zone <= 5; zone++) {
        const low = field(profile, sport + "_zone_" + zone + "_low").value;
        const high = field(profile, sport + "_zone_" + zone + "_high").value;
        if (low || high) settings.zones[zone] = [low ? Number(low) : null, high ? Number(high) : null];
      }
    } else if (key === "pool") {
      const value = field(profile, "swimming_pool_length").value;
      const unit = field(profile, "swimming_pool_unit").value;
      settings.pool_length_metres = value ? Number(value) * (unit === "yd" ? .9144 : 1) : null;
      settings.pool_length_unit = value ? unit : null;
    } else if (key === "css_seconds_per_100m") {
      const value = field(profile, "swimming_css_seconds_per_100m").value.trim();
      const match = value.match(/^(\d+):([0-5]\d)$/);
      if (value && !match) throw new Error("Use m:ss for swimming CSS pace, for example 2:00 per 100 m, or leave it blank.");
      settings[key] = match ? Number(match[1]) * 60 + Number(match[2]) : null;
    } else {
      const value = field(profile, sport + "_" + key).value;
      settings[key] = value ? (key === "ftp_watts" ? Number(value) : value) : null;
    }
  }
  return result;
}

formAction(connection, "config", () => {
  const get = key => field(connection, key).value;
  return {provider: get("provider"), model: get("model"), source: get("source"),
    daily_request_limit: get("daily_request_limit"),
    garmin_region: get("garmin_region"), garmin_api_key: get("garmin_api_key"),
    [get("provider") + "_api_key"]: get("provider_key")};
}, () => {
  field(connection, "provider_key").value = "";
  field(connection, "garmin_api_key").value = "";
});
formAction(profile, "profile", () => {
  const payload = {};
  for (const key of ["name", "goal", "experience", "injuries"]) payload[key] = field(profile, key).value;
  payload.training_mode = field(profile, "training_mode").value;
  const sportSettings = sportSettingsPayload();
  if (Object.keys(sportSettings).length) payload.sport_settings = sportSettings;
  payload.goals = {};
  for (const [priority] of goals) {
    const kind = field(profile, "goal_" + priority + "_kind").value;
    payload.goals[priority] = {kind, outcomes: {}};
    for (const [key] of goalFields) {
      payload.goals[priority][key] = field(profile, "goal_" + priority + "_" + key).value;
    }
    if (goalSportEdits.has(priority) || savedGoals[priority]?.sport || (!savedGoals[priority]?.event && payload.goals[priority].event)) {
      payload.goals[priority].sport = field(profile, "goal_" + priority + "_sport").value;
    }
    if (kind === "race") {
      for (const [key] of outcomeFields) {
        payload.goals[priority].outcomes[key] = field(profile, "goal_" + priority + "_outcome_" + key).value;
      }
    }
  }
  payload.days = Array.from(byId("days").querySelectorAll("input:checked"), input => input.value);
  const preferredDays = Array.from(byId("long-run-days").querySelectorAll("input:checked"), input => input.value);
  if ((["running", "triathlon"].includes(payload.training_mode) || longRunPreferenceEdited)
      && payload.days.length && preferredDays.some(day => !payload.days.includes(day))) {
    throw new Error("Preferred long-run days must also be selected as available training days. Update your availability or clear those preferences.");
  }
  if (longRunPreferenceEdited) payload.long_run_days = preferredDays;
  if (runningZonesEdited) {
    payload.zone_mode = field(profile, "zone_mode").value;
    payload.zones = {};
  }
  if (runningZonesEdited && field(profile, "zone_mode").value === "known") {
    for (let zone = 1; zone <= 5; zone += 1) {
      const low = field(profile, "zone_" + zone + "_low").value;
      const high = field(profile, "zone_" + zone + "_high").value;
      if (low || high) payload.zones[zone] = [low ? Number(low) : null, high ? Number(high) : null];
    }
    if (!payload.zones[2]) throw new Error("Enter Zone 2 or choose unknown zones.");
  }
  return payload;
}, () => {
  if (longRunPreferenceEdited) {
    savedLongRunPreference = Array.from(byId("long-run-days").querySelectorAll("input:checked"), input => input.value).join(", ");
    savedLongRunNote();
  }
  longRunPreferenceEdited = false;
  for (const [priority] of goals) {
    const event = field(profile, "goal_" + priority + "_event").value;
    const sport = field(profile, "goal_" + priority + "_sport").value;
    const explicit = goalSportEdits.has(priority) || savedGoals[priority]?.sport || (!savedGoals[priority]?.event && event);
    savedGoals[priority] = {event, ...(explicit ? {sport} : {})};
  }
  runningZonesEdited = false; sportSettingEdits.clear(); goalSportEdits.clear();
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

async function run(action, manualExtra) {
  if (working) return;
  working = true;
  byId("new-plan").disabled = true;
  byId("run-now").disabled = true;
  byId("run-extra").disabled = true;
  try {
    const payload = {action};
    if (manualExtra) payload.manual_extra = manualExtra;
    const result = await api("action", payload);
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
const extraConfirm = byId("extra-confirm");
let extraDay = "";
function extraActionDetail() {
  byId("extra-action-detail").textContent = byId("extra-action").value === "run_now"
    ? "Review and adapt your saved plan using the current profile and activity data."
    : "Generate a new upcoming block using your saved profile and training days. A valid plan preserves past sessions.";
  if (state.hr_learning?.enabled) byId("extra-action-detail").textContent += " The coach will include your HR baseline learning request.";
}
byId("extra-action").addEventListener("change", extraActionDetail);
byId("run-extra").addEventListener("click", () => {
  if (working || !state.request_budget?.extra_available) return;
  extraDay = state.request_budget.utc_date;
  byId("extra-action").options[0].disabled = !state.plan_ready;
  byId("extra-action").value = state.plan_ready ? "run_now" : "new_plan";
  extraActionDetail();
  byId("extra-date").textContent = "Approval applies only to " + extraDay + " (UTC). Once the AI request is reserved, it counts even if it fails.";
  extraConfirm.returnValue = "";
  extraConfirm.showModal();
});
extraConfirm.addEventListener("close", () => {
  if (extraConfirm.returnValue === "confirm") run(byId("extra-action").value, {date: extraDay, confirmed: true});
});

function updateDashboard() {
  if (byId("dashboard-panel").hidden || !state.dashboard_ready || displayedRevision === dashboardRevision) return;
  byId("dashboard").src = "./dashboard?v=" + encodeURIComponent(dashboardRevision);
  displayedRevision = dashboardRevision;
}

function selectView(view) {
  byId("setup-hero").hidden = view !== "settings";
  byId("settings").hidden = view !== "settings";
  byId("dashboard-panel").hidden = view !== "dashboard";
  byId("athlete-panel").hidden = view !== "athlete";
  for (const name of ["dashboard", "settings", "athlete"]) byId(name + "-tab").setAttribute("aria-pressed", String(view === name));
  if (view === "athlete") showAthlete();
  updateDashboard();
}
byId("dashboard-tab").addEventListener("click", () => selectView("dashboard"));
byId("settings-tab").addEventListener("click", () => selectView("settings"));
byId("athlete-tab").addEventListener("click", () => selectView("athlete"));
byId("edit-athlete").addEventListener("click", () => { selectView("settings"); profile.scrollIntoView({behavior: "smooth"}); });
byId("first-dashboard-plan").addEventListener("click", () => byId("new-plan").click());
for (const link of document.querySelectorAll("a.download-link[download]")) {
  link.addEventListener("click", async event => {
    event.preventDefault();
    if (downloading) return;
    downloading = true;
    const result = byId("download-result");
    result.hidden = false;
    result.textContent = "Preparing your download…";
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 30000);
    try {
      const response = await fetch(link.href, {credentials: "same-origin", cache: "no-store", signal: controller.signal});
      if (!response.ok) throw new Error((await response.json()).error || "The download is unavailable.");
      const url = URL.createObjectURL(await response.blob());
      const download = document.createElement("a");
      download.href = url;
      download.download = link.download;
      document.body.append(download);
      download.click();
      download.remove();
      setTimeout(() => URL.revokeObjectURL(url), 60000);
      result.textContent = "Download prepared: " + link.download;
    } catch (error) {
      result.textContent = error.message || "The download could not be prepared. Try again when the coach is idle.";
    } finally {
      clearTimeout(timer);
      downloading = false;
    }
  });
}
refresh();
setInterval(refresh, 3500);
