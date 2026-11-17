"use strict";

(() => {
  const finite = value => typeof value === "number" && Number.isFinite(value);
  const svgNS = "http://www.w3.org/2000/svg";
  const charts = new WeakMap();
  const label = (value, step) => {
    const precision = Math.max(0, Math.min(6, 1 - Math.floor(Math.log10(step))));
    return Number(value.toFixed(precision)).toLocaleString("en-GB", {maximumFractionDigits: precision});
  };
  const niceStep = value => {
    const power = 10 ** Math.floor(Math.log10(value)), fraction = value / power;
    return (fraction <= 1 ? 1 : fraction <= 2 ? 2 : fraction <= 5 ? 5 : 10) * power;
  };

  function model(points, courseDistance, width = 520) {
    width = Math.max(260, Math.round(width));
    const height = 270, box = {left: 64, right: width - 18, top: 36, bottom: height - 52};
    const distances = points.filter(p => finite(p.distance_m) && p.distance_m >= 0).map(p => p.distance_m);
    const end = Math.max(0, finite(courseDistance) ? courseDistance : 0, ...distances);
    const known = points.filter(p => finite(p.distance_m) && p.distance_m >= 0 && finite(p.elevation_m));
    const minHeight = known.length ? Math.min(...known.map(p => p.elevation_m)) : null;
    const maxHeight = known.length ? Math.max(...known.map(p => p.elevation_m)) : null;
    const x = value => box.left + (end > 0 ? value / end : 0) * (box.right - box.left);
    const count = Math.max(2, Math.min(6, Math.floor((box.right - box.left) / 120)));
    const xValues = [0];
    if (end > 0) {
      const step = niceStep(end / count);
      // Keep intermediate ticks on round distances and leave room for the
      // actual endpoint without moving it to a rounded tick position.
      for (let value = step; value < end - step * .6; value += step) xValues.push(value);
      xValues.push(end);
    }
    const distancePrecision = end < 1000 ? 3 : 2;
    const xTicks = xValues.map(value => ({value, position: x(value),
      label: Number((value / 1000).toFixed(distancePrecision)).toLocaleString("en-GB", {maximumFractionDigits: distancePrecision})}));
    let yTicks = [], y = () => box.bottom;
    if (known.length) {
      const padding = Math.max(1, (maxHeight - minHeight) * .05);
      const step = niceStep((maxHeight - minHeight + padding * 2) / 4);
      const low = Math.floor((minHeight - padding) / step) * step;
      const high = Math.ceil((maxHeight + padding) / step) * step;
      y = value => box.bottom - (value - low) / (high - low) * (box.bottom - box.top);
      yTicks = Array.from({length: Math.round((high - low) / step) + 1}, (_, i) => {
        const value = low + i * step;
        return {value, position: y(value), label: label(value, step)};
      });
    }
    const runs = []; let current = null, previous = null;
    for (const point of points) {
      if (!finite(point.distance_m) || point.distance_m < 0 || !finite(point.elevation_m)) {
        current = null; previous = null; continue;
      }
      if (!previous || point.segment !== previous.segment || point.distance_m <= previous.distance_m) {
        current = []; runs.push(current);
      }
      current.push({x: x(point.distance_m), y: y(point.elevation_m)});
      previous = point;
    }
    return {width, height, box, end, minHeight, maxHeight, knownCount: known.length,
      hasProfile: runs.some(run => run.length > 1), xTicks, yTicks, runs};
  }

  function draw(target, points, courseDistance) {
    const chart = model(points, courseDistance, target.clientWidth || 520);
    const {box} = chart;
    target.replaceChildren();
    target.setAttribute("viewBox", `0 0 ${chart.width} ${chart.height}`);
    target.setAttribute("aria-label", "Course elevation. Distance in kilometres, elevation in metres. "
      + (chart.knownCount ? `${chart.knownCount} known preview heights; missing heights and disconnected sections are gaps.` : "Elevation is unknown."));
    const node = (tag, attrs, text = null) => {
      const item = document.createElementNS(svgNS, tag);
      for (const [name, value] of Object.entries(attrs)) item.setAttribute(name, String(value));
      if (text !== null) item.textContent = text;
      target.append(item); return item;
    };
    node("text", {x: box.left, y: 21, class: "elevation-axis-title"}, "Elevation (m)");
    node("text", {x: (box.left + box.right) / 2, y: chart.height - 10, "text-anchor": "middle", class: "elevation-axis-title"}, "Distance (km)");
    for (const tick of chart.yTicks) {
      node("line", {x1: box.left, x2: box.right, y1: tick.position, y2: tick.position, class: "elevation-grid"});
      node("text", {x: box.left - 9, y: tick.position + 4, "text-anchor": "end", class: "elevation-tick elevation-y-tick"}, tick.label);
    }
    for (const [index, tick] of chart.xTicks.entries()) {
      node("line", {x1: tick.position, x2: tick.position, y1: box.bottom, y2: box.bottom + 5, class: "elevation-axis"});
      node("text", {x: tick.position, y: box.bottom + 21, "text-anchor": index === 0 ? "start" : index === chart.xTicks.length - 1 ? "end" : "middle", class: "elevation-tick elevation-x-tick"}, tick.label);
    }
    node("path", {d: `M ${box.left} ${box.top} V ${box.bottom} H ${box.right}`, class: "elevation-axis", fill: "none"});
    for (const run of chart.runs) {
      if (run.length === 1) node("circle", {cx: run[0].x, cy: run[0].y, r: 3, class: "elevation-sample"});
      else node("path", {d: run.map((p, i) => `${i ? "L" : "M"} ${p.x.toFixed(2)} ${p.y.toFixed(2)}`).join(" "),
        class: "elevation-line", fill: "none"});
    }
    if (!chart.knownCount) node("text", {x: (box.left + box.right) / 2, y: (box.top + box.bottom) / 2, "text-anchor": "middle", class: "elevation-tick"}, "Elevation unknown");
    return chart;
  }

  function render(target, points, courseDistance) {
    let record = charts.get(target);
    if (!record) {
      record = {points, courseDistance, width: target.clientWidth}; charts.set(target, record);
      if (typeof ResizeObserver !== "undefined") {
        record.observer = new ResizeObserver(() => {
          const width = target.clientWidth;
          if (width > 0 && width !== record.width) {
            record.width = width; draw(target, record.points, record.courseDistance);
          }
        });
        record.observer.observe(target);
      }
    }
    record.points = points; record.courseDistance = courseDistance; record.width = target.clientWidth;
    return draw(target, points, courseDistance);
  }
  window.CoachElevationChart = {render, model};
})();
