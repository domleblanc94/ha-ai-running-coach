"use strict";

(() => {
  let selected = null, busy = false, latest = {};
  const text = (tag, value, className = "") => {
    const node = document.createElement(tag); node.textContent = value ?? "";
    if (className) node.className = className;
    return node;
  };
  const pretty = value => typeof value === "string" ? value : JSON.stringify(value, null, 2);
  const clearToken = () => {
    byId("chat-token").value = ""; byId("chat-token").type = "password";
    byId("chat-token-box").hidden = true; byId("chat-token-reveal").textContent = "Show token";
  };
  function updateStatus(next) {
    latest = next;
    const saved = next.chat_bridge || {};
    byId("chat-connection-status").textContent = saved.enabled
      ? "Connection enabled. The dedicated token and a configured private LAN port are required."
      : "Connection disabled. No chat client can access your data through this bridge.";
    byId("chat-enable").hidden = !!saved.enabled;
    byId("chat-rotate").hidden = byId("chat-disable").hidden = !saved.enabled;
    for (const id of ["chat-enable", "chat-rotate", "chat-disable", "chat-refresh"]) byId(id).disabled = busy;
    byId("activity-details-import").disabled = busy || !!next.running || !!next.queued;
    const unavailable = busy || !!next.running || !!next.queued || selected?.status !== "pending";
    byId("chat-apply").disabled = byId("chat-reject").disabled = unavailable;
  }
  async function configure(action) {
    if (busy) return;
    busy = true; updateStatus(latest); clearToken();
    const output = byId("chat-connection-result"); output.textContent = "Saving connection…";
    try {
      const result = await api("chat/connection", {action});
      latest = {...latest, chat_bridge: result.chat_bridge};
      if (result.token) {
        byId("chat-token").value = result.token; byId("chat-token-box").hidden = false;
        output.textContent = "New token created. Any previous token has been revoked.";
      } else output.textContent = "Connection disabled and token revoked.";
    } catch (error) { output.textContent = error.message; }
    finally { busy = false; updateStatus(latest); await refresh(); }
  }
  for (const action of ["enable", "rotate", "disable"]) byId("chat-" + action).addEventListener("click", () => configure(action));
  byId("chat-token-clear").addEventListener("click", clearToken);
  byId("chat-token-reveal").addEventListener("click", () => {
    const input = byId("chat-token"); input.type = input.type === "password" ? "text" : "password";
    byId("chat-token-reveal").textContent = input.type === "password" ? "Show token" : "Mask token";
  });
  byId("chat-token-copy").addEventListener("click", async () => {
    try { await navigator.clipboard.writeText(byId("chat-token").value); byId("chat-connection-result").textContent = "Token copied. Store it in the companion’s private token file."; }
    catch { byId("chat-connection-result").textContent = "Clipboard unavailable. Show the token and copy it manually."; }
  });
  async function show() {
    if (busy) return;
    busy = true; updateStatus(latest);
    const output = byId("chat-proposal-result"); output.textContent = "Loading proposals…";
    try {
      const result = await api("chat/proposals", undefined, 25000);
      const target = byId("chat-proposals"); target.replaceChildren();
      for (const item of result.proposals || []) {
        const row = document.createElement("article"); row.className = "chat-proposal-row";
        row.append(text("h3", item.summary), text("p", item.status + " · " + item.created_at, "hint"));
        const button = text("button", "Review proposal", "secondary"); button.type = "button";
        button.addEventListener("click", () => inspect(item.id)); row.append(button); target.append(row);
      }
      output.textContent = (result.proposals || []).length
        ? result.total_pending + " pending proposal(s). Nothing is applied automatically."
        : "No proposals yet. Ask your connected chat coach to suggest a plan adjustment.";
    } catch (error) { output.textContent = error.message; }
    finally { busy = false; updateStatus(latest); }
  }
  function drawProposal(value) {
    selected = value;
    byId("chat-proposal-title").textContent = value.summary;
    byId("chat-proposal-meta").textContent = value.status + " · Based on plan revision " + value.base_revision + " · " + value.created_at;
    const target = byId("chat-proposal-diff"); target.replaceChildren();
    for (const change of value.changes || []) {
      const row = document.createElement("article"); row.className = "chat-proposal-row";
      row.append(text("h3", (change.op || "Change") + " · " + (change.session_id || change.after?.date || "Session")), text("p", change.reason));
      const pair = document.createElement("div"); pair.className = "grid";
      for (const [label, item] of [["Before", change.before], ["After", change.after]]) {
        const part = document.createElement("section"); part.append(text("h4", label));
        if (!item) part.append(text("p", label === "Before" ? "New session" : "Session removed"));
        else {
          const facts = document.createElement("dl"); facts.className = "chat-session-facts";
          for (const [key, content] of Object.entries(item)) {
            facts.append(text("dt", key.replaceAll("_", " ")), text("dd", pretty(content)));
          }
          part.append(facts);
        }
        pair.append(part);
      }
      row.append(pair); target.append(row);
    }
    if (JSON.stringify(value.phases_before) !== JSON.stringify(value.phases_after)) {
      const phases = document.createElement("details"); phases.open = true;
      phases.append(text("summary", "Training phase changes"), text("h4", "Before"), text("pre", pretty(value.phases_before), "profile-source"), text("h4", "After"), text("pre", pretty(value.phases_after), "profile-source")); target.append(phases);
    }
    if (JSON.stringify(value.planning_overview_before) !== JSON.stringify(value.planning_overview_after)) {
      const overview = document.createElement("details"); overview.open = true;
      overview.append(text("summary", "Planning focus and review point changes"), text("h4", "Before"), text("pre", pretty(value.planning_overview_before), "profile-source"), text("h4", "After"), text("pre", pretty(value.planning_overview_after), "profile-source")); target.append(overview);
    }
    byId("chat-proposal-source").textContent = pretty(value);
    byId("chat-proposal-detail").hidden = false;
  }
  async function inspect(id) {
    if (busy) return;
    busy = true; updateStatus(latest);
    try { drawProposal(await api("chat/proposal", {action: "get_proposal", payload: {proposal_id: id}}, 25000)); byId("chat-proposal-detail").scrollIntoView({behavior: "smooth"}); }
    catch (error) { byId("chat-proposal-result").textContent = error.message; }
    finally { busy = false; updateStatus(latest); }
  }
  const confirm = byId("chat-apply-confirm");
  byId("chat-apply").addEventListener("click", () => {
    if (!selected || busy) return;
    byId("chat-confirm-summary").textContent = selected.summary;
    confirm.returnValue = ""; confirm.showModal();
  });
  confirm.addEventListener("close", () => { if (confirm.returnValue === "confirm") resolve("apply_proposal"); });
  byId("chat-reject").addEventListener("click", () => resolve("reject_proposal"));
  async function resolve(action) {
    if (!selected || busy) return;
    busy = true; updateStatus(latest);
    try {
      const result = await api("chat/proposal", {action, payload: {proposal_id: selected.id, proposal_hash: selected.proposal_hash}}, 50000);
      selected.status = result.status;
      byId("chat-proposal-meta").textContent = result.status + " · Plan revision " + (result.revision ?? selected.base_revision);
      byId("chat-proposal-result").textContent = result.status === "applied"
        ? result.dashboard_published ? "Changes applied. Your saved dashboard is updated; no AI request was made."
          : "Changes applied to the saved plan. The old dashboard is retained because local rendering or publication failed. Your plan exports reflect the saved changes."
        : "Proposal rejected. Your plan is unchanged.";
      await refresh();
    } catch (error) { byId("chat-proposal-result").textContent = error.message; }
    finally { busy = false; updateStatus(latest); }
  }
  byId("chat-refresh").addEventListener("click", show);
  byId("activity-details-backfill").addEventListener("submit", async event => {
    event.preventDefault();
    if (busy || latest.running || latest.queued) return;
    const raw = byId("activity-details-backfill").elements.namedItem("activity_id").value.trim();
    const id = Number(raw);
    const output = byId("activity-details-result");
    if (!/^[1-9][0-9]{0,15}$/.test(raw) || !Number.isSafeInteger(id)) {
      output.textContent = "Enter one valid Garmin activity ID."; return;
    }
    busy = true; updateStatus(latest); byId("activity-details-import").disabled = true;
    output.textContent = "Importing this activity’s details…";
    try {
      const result = await api("activities/details-backfill", {activity_id: id}, 165000);
      output.textContent = "Details imported: " + result.stream_rows + " sensor records and " + result.laps + " laps. Your chat coach can now read the saved activity.";
    } catch (error) { output.textContent = error.message; }
    finally { busy = false; byId("activity-details-import").disabled = false; updateStatus(latest); await refresh(); }
  });
  window.CoachChat = {show, updateStatus, clearToken};
})();
