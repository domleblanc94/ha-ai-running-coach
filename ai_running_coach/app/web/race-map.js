"use strict";

(() => {
  const holder = byId("race-map"), toggle = byId("race-map-toggle"), fit = byId("race-map-fit"), status = byId("race-map-status");
  let course = null, map = null, bounds = null, resizeObserver = null, generation = 0, renderedHash = "", failed = false, hidden = false;
  try { hidden = localStorage.getItem("coach-hide-course-map") === "true"; } catch (_) { /* Storage can be unavailable in private browsing. */ }

  function segments(points) {
    if (!Array.isArray(points) || points.length < 2 || points.length > 50000) throw new Error("The course geometry is unavailable. The offline outline remains available.");
    const output = [], all = []; let previous = null, line = null;
    const reference = points[0][1];
    for (const point of points) {
      if (!Array.isArray(point) || point.length !== 3 || !point.every(Number.isFinite) || Math.abs(point[0]) > 90 || Math.abs(point[1]) > 180 || !Number.isInteger(point[2]) || point[2] < 0) throw new Error("The course geometry is invalid. The offline outline remains available.");
      if (Math.abs(point[0]) > 85.05112878) throw new Error("This course reaches beyond the map imagery's polar coverage. Use the offline outline and course download.");
      if (!previous || previous[2] !== point[2]) { line = []; output.push(line); previous = null; }
      const anchor = previous ? previous[1] : reference;
      const longitude = anchor + ((point[1] - anchor + 540) % 360 - 180);
      const next = [point[0], longitude, point[2]];
      line.push(next.slice(0, 2)); all.push(next.slice(0, 2)); previous = next;
    }
    const longitudes = all.map(point => point[1]);
    if (output.length > 256 || Math.max(...longitudes) - Math.min(...longitudes) > 180) throw new Error("This course covers too wide an area or too many separate sections for this map. Use the offline outline and course download.");
    return {lines: output, points: all};
  }

  function clear() {
    generation += 1;
    resizeObserver?.disconnect(); resizeObserver = null;
    if (map) map.remove();
    map = null; bounds = null; renderedHash = "";
    holder.hidden = true; fit.hidden = true;
    byId("race-outline").hidden = false;
  }

  function buttons() { toggle.textContent = hidden ? "Show map" : failed ? "Retry map" : "Hide map"; }

  async function display(next) {
    const changed = course?.route_hash !== next?.route_hash;
    course = next;
    if (byId("race-panel").hidden) { clear(); return; }
    if (changed) { clear(); failed = false; }
    buttons();
    if (!course) { clear(); return; }
    if (hidden) { status.textContent = "Map hidden. The offline outline and elevation profile remain available."; return; }
    if (failed) return;
    if (renderedHash === course.route_hash) { if (map) map.invalidateSize(); return; }
    clear(); failed = false; buttons();
    const token = generation, hash = course.route_hash;
    renderedHash = hash;
    status.textContent = "Loading the course map from OpenStreetMap…";
    try {
      const geometry = await api("race/geometry?route_hash=" + encodeURIComponent(hash));
      if (token !== generation || hidden || byId("race-panel").hidden) return;
      if (geometry.route_hash !== hash) throw new Error("The course changed. Refresh Race planning to view its map.");
      const route = segments(geometry.points);
      if (!window.L) throw new Error("The map library is unavailable. The offline outline remains available.");
      holder.hidden = false;
      map = L.map(holder, {preferCanvas: true, scrollWheelZoom: false, zoomAnimation: false, fadeAnimation: false, minZoom: 1, maxZoom: 19});
      if (window.ResizeObserver) {
        resizeObserver = new ResizeObserver(() => { if (map) map.invalidateSize({pan: false}); });
        resizeObserver.observe(holder);
      }
      bounds = L.latLngBounds(route.points);
      for (const line of route.lines) {
        if (line.length > 1) L.polyline(line, {color: "#006f88", weight: 4, opacity: 0.95}).addTo(map);
        else L.circleMarker(line[0], {radius: 4, color: "#006f88"}).addTo(map);
      }
      L.circleMarker(route.points[0], {radius: 6, color: "#ffffff", fillColor: "#187c3b", fillOpacity: 1, weight: 2}).bindTooltip("Course start").addTo(map);
      L.circleMarker(route.points.at(-1), {radius: 6, color: "#ffffff", fillColor: "#b64242", fillOpacity: 1, weight: 2}).bindTooltip("Course finish").addTo(map);
      L.control.scale({imperial: false}).addTo(map);
      map.fitBounds(bounds, {padding: [24, 24], maxZoom: 16});
      const tiles = L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
        maxZoom: 19, detectRetina: false, keepBuffer: 0, updateWhenIdle: true, updateWhenZooming: false,
        referrerPolicy: "origin", attribution: '&copy; <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener noreferrer">OpenStreetMap contributors</a>'
      });
      let loaded = 0, errors = 0;
      tiles.on("tileload", () => {
        if (token !== generation) return;
        loaded += 1; byId("race-outline").hidden = true;
        status.textContent = errors ? "Some map tiles are unavailable. The course line and offline outline are still available." : "Drag to pan; use +/− or pinch to zoom. Green marks the start; red marks the finish.";
        if (errors) byId("race-outline").hidden = false;
      });
      tiles.on("tileerror", () => {
        if (token !== generation) return;
        errors += 1; byId("race-outline").hidden = false;
        status.textContent = "Map tiles are unavailable or incomplete. The offline outline and elevation profile remain available.";
      });
      tiles.on("load", () => {
        if (token !== generation || loaded || !errors) return;
        clear(); failed = true; buttons();
      });
      tiles.addTo(map); fit.hidden = false;
    } catch (error) {
      if (token !== generation) return;
      clear(); failed = true; status.textContent = error.message || "Map unavailable. The offline outline remains available."; buttons();
    }
  }

  toggle.addEventListener("click", () => {
    hidden = failed ? false : !hidden; failed = false;
    try { localStorage.setItem("coach-hide-course-map", String(hidden)); } catch (_) { /* No persistence is needed. */ }
    clear(); display(course);
  });
  fit.addEventListener("click", () => { if (map && bounds) map.fitBounds(bounds, {padding: [24, 24], maxZoom: 16}); });
  window.CoachRouteMap = {display, segments, deactivate: clear};
})();
