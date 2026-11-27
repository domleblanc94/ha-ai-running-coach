/* Keep graph choices outside the opaque saved-dashboard sandbox. */
(function () {
  "use strict";
  const frame = document.getElementById("dashboard");
  if (!frame) return;
  const storageKey = "coach.chart-preferences.v1";
  const fields = ["view", "metric", "sport", "week", "open"];
  function exactKeys(value, keys) {
    return value && typeof value === "object" && !Array.isArray(value)
      && Object.keys(value).length === keys.length
      && keys.every(key => Object.prototype.hasOwnProperty.call(value, key));
  }
  function preferences(value) {
    if (!exactKeys(value, fields)
        || !["week", "plan"].includes(value.view)
        || !["km", "load", "minutes"].includes(value.metric)
        || !["running", "cycling", "swimming", "other", "all"].includes(value.sport)
        || typeof value.week !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(value.week)
        || typeof value.open !== "boolean") return null;
    const date = new Date(value.week + "T00:00:00Z");
    if (!Number.isFinite(date.getTime()) || date.toISOString().slice(0, 10) !== value.week
        || date.getUTCDay() !== 1) return null;
    return Object.fromEntries(fields.map(key => [key, value[key]]));
  }
  let saved = null;
  try {
    const raw = localStorage.getItem(storageKey);
    if (raw && raw.length <= 256) saved = preferences(JSON.parse(raw));
  } catch (_) { /* Blocked storage still allows choices for this app session. */ }
  function send() {
    if (saved && frame.contentWindow) frame.contentWindow.postMessage({
      type: "coach:chart-preferences", version: 1, preferences: saved,
    }, "*"); // The sandbox has an opaque origin; address only this exact frame.
  }
  frame.addEventListener("load", send);
  window.addEventListener("message", event => {
    if (event.source !== frame.contentWindow) return;
    const message = event.data;
    if (!message || message.version !== 1) return;
    if (message.type === "coach:chart-preferences-ready"
        && exactKeys(message, ["type", "version"])) { send(); return; }
    if (message.type !== "coach:chart-preferences-change"
        || !exactKeys(message, ["type", "version", "preferences"])) return;
    const next = preferences(message.preferences);
    if (!next) return;
    saved = next;
    try { localStorage.setItem(storageKey, JSON.stringify(saved)); } catch (_) {}
  });
})();
