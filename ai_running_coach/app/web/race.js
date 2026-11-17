"use strict";

(() => {
  const upload = byId("race-upload-form"), review = byId("race-review-form");
  let saved = null, latestStatus = {}, loading = false, uploading = false, requesting = false;
  let dirty = false, signature = "", loadedHash = "";
  const number = value => typeof value === "number" && Number.isFinite(value);
  const distance = metres => number(metres) ? (metres / 1000).toFixed(2) + " km" : "Unknown";
  const elevation = metres => number(metres) ? Math.round(metres) + " m" : "Unknown";
  const duration = seconds => {
    if (!number(seconds)) return "Unknown";
    const rounded = Math.round(seconds), h = Math.floor(rounded / 3600), m = Math.floor(rounded % 3600 / 60), s = rounded % 60;
    return (h ? h + "h " : "") + m + "m" + (!h && s ? " " + s + "s" : "");
  };
  const pace = (seconds, metres) => {
    if (!number(seconds) || !number(metres) || metres <= 0) return "Effort guidance";
    const value = Math.round(seconds / (metres / 1000));
    return Math.floor(value / 60) + ":" + String(value % 60).padStart(2, "0");
  };
  const text = (tag, value, className = "") => {
    const node = document.createElement(tag); node.textContent = value || "";
    if (className) node.className = className;
    return node;
  };
  const list = (target, rows) => {
    target.replaceChildren();
    for (const value of rows || []) target.append(text("li", value));
    target.hidden = !target.children.length;
  };

  function plot(target, points, x, y) {
    target.replaceChildren();
    const values = points.filter(p => number(x(p)) && number(y(p)));
    if (values.length < 2) return false;
    let minX = Math.min(...values.map(x)), maxX = Math.max(...values.map(x));
    let minY = Math.min(...values.map(y)), maxY = Math.max(...values.map(y));
    if (minX === maxX) { minX -= 1; maxX += 1; }
    if (minY === maxY) { minY -= 1; maxY += 1; }
    const project = p => [(24 + (x(p) - minX) / (maxX - minX) * 472).toFixed(2), (188 - (y(p) - minY) / (maxY - minY) * 160).toFixed(2)];
    let drawing = "", previous = null;
    for (const point of points) {
      if (!number(x(point)) || !number(y(point))) { previous = null; continue; }
      drawing += (previous && point.segment === previous.segment ? " L " : " M ") + project(point).join(" ");
      previous = point;
    }
    const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
    path.setAttribute("d", drawing); path.setAttribute("fill", "none"); path.setAttribute("stroke", "currentColor"); path.setAttribute("stroke-width", "2.5");
    target.append(path);
    return true;
  }

  function controls() {
    const busy = Boolean(latestStatus.running || latestStatus.queued);
    upload.querySelector("button").disabled = busy || uploading;
    field(upload, "gpx").disabled = busy || uploading;
    const discontinuous = Boolean(saved?.course?.summary?.discontinuities);
    byId("race-generate").disabled = busy || requesting || !saved?.ready || !saved?.course || discontinuous;
    byId("race-generate").textContent = saved?.plan ? "Refine strategies · 1 AI request" : "Generate race strategies · 1 AI request";
    byId("race-readiness").textContent = !saved?.ready
      ? "Save your athlete profile and AI provider connection in Setup & settings before generating strategies."
      : discontinuous ? "The course has disconnected sections. Upload a continuous course before asking for pacing."
      : busy ? "Wait for the current coach action to finish." : "The coach will use your saved profile and current imported activity evidence.";
    const budget = latestStatus.request_budget;
    byId("race-budget").textContent = "Shared AI requests today: " + (budget?.used ?? "unknown") + " / " + (budget?.limit ?? "unknown")
      + ". Failed requests count. Uploading and downloading do not use an AI request.";
  }

  function renderStrategy(strategy, course, plan) {
    const card = text("article", "", "card race-strategy");
    card.append(text("p", strategy.id === "ambitious" ? "CONDITIONAL AMBITIOUS OPTION" : "CONSERVATIVE OPTION", "eyebrow"),
      text("h2", strategy.title), text("p", strategy.rationale, "race-copy"));
    const range = strategy.finish_seconds;
    card.append(text("p", range ? "Estimated finish: " + duration(range.low) + " – " + duration(range.high) : "No reliable finish-time estimate: follow the effort guidance.", "race-finish"));
    if (number(strategy.pickup_after_m)) card.append(text("p", "Consider a pickup after " + distance(strategy.pickup_after_m) + " only if the conditions below are met."));
    const conditions = document.createElement("ul"); list(conditions, strategy.conditions); card.append(conditions);
    const wrapper = text("div", "", "race-splits");
    const table = document.createElement("table"), caption = text("caption", "Course sections · estimates, not guaranteed splits");
    const head = document.createElement("thead"), hr = document.createElement("tr");
    for (const label of ["Section", "Terrain", "Pace / time", "Coaching guidance"]) hr.append(text("th", label));
    head.append(hr); table.append(caption, head);
    const body = document.createElement("tbody");
    for (const section of strategy.sections || []) {
      const terrain = course.summary.sections.find(item => item.id === section.section_id);
      if (!terrain) continue;
      const row = document.createElement("tr");
      const timing = number(section.seconds_low) && number(section.seconds_high)
        ? pace(section.seconds_low, terrain.distance_m) + "–" + pace(section.seconds_high, terrain.distance_m) + " /km\n" + duration(section.seconds_low) + "–" + duration(section.seconds_high)
        : "Use effort guidance";
      row.append(text("td", distance(terrain.start_m) + "–" + distance(terrain.end_m)),
        text("td", "↑ " + elevation(terrain.ascent_m) + "\n↓ " + elevation(terrain.descent_m)),
        text("td", timing), text("td", section.effort + "\n" + section.guidance));
      Array.from(row.children).forEach((cell, index) => { cell.dataset.label = ["Section", "Terrain", "Pace / time", "Coaching guidance"][index]; });
      body.append(row);
    }
    table.append(body); wrapper.append(table); card.append(wrapper);
    if (range && strategy.sections?.every(section => number(section.seconds_low) && number(section.seconds_high))) {
      const link = text("a", "Download timed course FIT", "download-link");
      link.href = "./exports/race-course-" + strategy.id + ".fit?route_hash=" + encodeURIComponent(course.route_hash) + "&request_id=" + encodeURIComponent(plan.request_id);
      link.download = "race-" + strategy.id + "-course.fit";
      link.addEventListener("click", download);
      card.append(link, text("p", "Uses the midpoint of each estimated section range as a fixed course timing reference, not a native PacePro plan. It cannot detect how you feel or switch strategies. Garmin may calculate different splits when you create PacePro from the course.", "hint"));
    }
    return card;
  }

  function render() {
    const course = saved?.course, plan = saved?.plan;
    byId("race-course-card").hidden = !course;
    review.hidden = !course;
    byId("race-saved-plan").hidden = !plan;
    if (course) {
      byId("race-course-name").textContent = course.name;
      factsList([["Approximate distance", distance(course.summary.distance_m)], ["Ascent / descent", elevation(course.summary.ascent_m) + " / " + elevation(course.summary.descent_m)],
        ["Elevation coverage", Math.round((course.summary.elevation_coverage || 0) * 100) + "% of route distance"], ["Track sections", String(course.summary.segment_count)]], byId("race-course-facts"));
      list(byId("race-course-warnings"), course.summary.warnings);
      byId("race-quality-notes").hidden = !course.summary.warnings?.length;
      if (course.summary.discontinuities) byId("race-quality-notes").open = true;
      const reference = course.preview[0]?.lon || 0;
      plot(byId("race-route-plot"), course.preview, p => ((p.lon - reference + 540) % 360 - 180) * Math.cos((course.preview[0]?.lat || 0) * Math.PI / 180), p => p.lat);
      const hasElevation = plot(byId("race-elevation-plot"), course.preview, p => p.distance_m, p => p.elevation_m);
      const elevationPoints = course.preview.filter(p => number(p.distance_m) && number(p.elevation_m));
      byId("race-elevation-scale").textContent = hasElevation
        ? "Distance shown: " + distance(Math.min(...elevationPoints.map(p => p.distance_m))) + "–" + distance(Math.max(...elevationPoints.map(p => p.distance_m)))
          + ". Known preview height: " + elevation(Math.min(...elevationPoints.map(p => p.elevation_m))) + "–" + elevation(Math.max(...elevationPoints.map(p => p.elevation_m))) + "."
        : "Course distance: 0.00 km–" + distance(course.summary.distance_m) + ". Elevation scale: unknown.";
      byId("race-elevation-note").textContent = hasElevation ? "GPX elevations are approximate. Missing points leave gaps; noisy elevations can affect ascent and pacing." : "This file has too little elevation data to plot. Elevation is unknown, not zero.";
      byId("race-fit-download").hidden = Boolean(course.summary.discontinuities);
      byId("race-fit-download").href = "./exports/race-course.fit?route_hash=" + encodeURIComponent(course.route_hash);
      byId("race-gpx-download").href = "./exports/race-course.gpx?route_hash=" + encodeURIComponent(course.route_hash);
      if (loadedHash !== course.route_hash) {
        loadedHash = course.route_hash; dirty = false;
        field(review, "race_name").value = plan?.race_name || course.name;
        field(review, "race_date").value = plan?.race_date || "";
        field(review, "question").value = plan?.question || "";
      } else if (plan && !dirty) {
        field(review, "race_name").value = plan.race_name;
        field(review, "race_date").value = plan.race_date || "";
      }
    }
    if (plan) {
      byId("race-plan-name").textContent = plan.race_name;
      const stamp = new Date(plan.generated_at);
      const savedTime = Number.isNaN(stamp.valueOf()) ? "Unknown" : stamp.toLocaleString(undefined, {dateStyle: "medium", timeStyle: "short"});
      byId("race-plan-date").textContent = (plan.race_date ? "Race: " + plan.race_date + " · " : "") + "Saved: " + savedTime
        + " · Evidence through " + (plan.context?.evidence_date || "unknown date") + " · Latest activity: " + (plan.context?.latest_activity_date || "No imported activity");
      byId("race-plan-summary").textContent = plan.summary;
      byId("race-fitness-confidence").textContent = "Fitness estimate confidence: " + plan.fitness.confidence + ". Approximation from available training, not a measured race prediction.";
      list(byId("race-fitness-evidence"), plan.fitness.evidence); list(byId("race-fitness-limits"), plan.fitness.limitations);
      byId("race-strategies").replaceChildren(...plan.strategies.map(strategy => renderStrategy(strategy, course, plan)));
    }
    const last = saved?.last_run;
    const running = latestStatus.running?.action === "race_review" || latestStatus.queued?.action === "race_review";
    byId("race-action-result").textContent = running ? "The race coach is working. This page checks for the result." : last?.message || "";
    byId("race-action-result").classList.toggle("error", !running && ["failed", "interrupted"].includes(last?.status));
    controls();
  }

  async function load() {
    if (loading || uploading) return;
    loading = true;
    try {
      saved = await api("race"); render(); byId("race-load-result").textContent = "";
    } catch (error) {
      byId("race-load-result").textContent = error.message;
    } finally { loading = false; }
  }

  async function download(event) {
    event.preventDefault();
    const link = event.currentTarget;
    try {
      const response = await fetch(link.href, {credentials: "same-origin", cache: "no-store"});
      if (!response.ok) throw new Error((await response.json()).error || "Course download is unavailable.");
      const url = URL.createObjectURL(await response.blob()), anchor = document.createElement("a");
      anchor.href = url; anchor.download = link.download; document.body.append(anchor); anchor.click(); anchor.remove();
      setTimeout(() => URL.revokeObjectURL(url), 60000);
      byId("race-load-result").textContent = "Course downloaded. Transfer it separately using your Garmin workflow.";
    } catch (error) { byId("race-load-result").textContent = error.message; }
  }
  // Existing static download links already have the shared app handler.

  upload.addEventListener("submit", async event => {
    event.preventDefault();
    const file = field(upload, "gpx").files[0];
    const result = byId("race-upload-result");
    if (!file || file.size > 5 * 1024 * 1024) { result.textContent = "Choose a GPX file no larger than 5 MB."; return; }
    uploading = true; controls(); result.textContent = "Inspecting the GPX locally…"; result.classList.remove("error");
    try {
      saved = await api("race/course", {filename: file.name, gpx: await file.text()});
      loadedHash = ""; render(); result.textContent = "Course saved. No AI request was used.";
    } catch (error) { result.textContent = error.message; result.classList.add("error"); }
    finally { uploading = false; controls(); }
  });
  review.addEventListener("input", () => { dirty = true; });
  review.addEventListener("submit", async event => {
    event.preventDefault();
    if (requesting || !saved?.course) return;
    requesting = true; controls();
    try {
      const payload = {route_hash: saved.course.route_hash};
      for (const key of ["question", "race_name", "race_date"]) payload[key] = field(review, key).value.trim();
      const next = await api("race/action", payload); draw(next); dirty = false; await load();
    } catch (error) { byId("race-action-result").textContent = error.message; byId("race-action-result").classList.add("error"); }
    finally { requesting = false; controls(); }
  });
  window.CoachRace = {
    show: load,
    updateStatus(next) {
      latestStatus = next; controls();
      const current = JSON.stringify([next.queued, next.running, next.race_last_run]);
      if (!byId("race-panel").hidden && current !== signature) { signature = current; load(); }
    },
  };
})();
