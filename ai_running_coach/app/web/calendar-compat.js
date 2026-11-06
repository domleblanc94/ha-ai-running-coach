/* Apply the bundled layout to an older saved dashboard without a coach run. */
(function () {
  "use strict";
  if (!window.CoachCalendar || typeof renderSelectedWeek !== "function"
      || renderSelectedWeek.calendarAligned || typeof renderDayCard !== "function"
      || typeof renderTabs !== "function" || typeof findCurrentWeekIndex !== "function"
      || typeof allParsedWeeks === "undefined" || !Array.isArray(allParsedWeeks)
      || typeof activeWeekIndex === "undefined") return;
  const calendar = document.getElementById("calendarView");
  const container = document.getElementById("calendarGridContainer");
  if (!calendar || !container) return;
  const header = calendar.querySelector("#calendarWeekdayHeader, :scope > .grid-cols-7");
  if (header) header.id = "calendarWeekdayHeader";
  window.CoachCalendar.configure(calendar);
  const previous = allParsedWeeks[activeWeekIndex];
  const firstDate = previous?.rows?.find(row => window.CoachCalendar.weekStart(row.dateStr))?.dateStr;
  const selectedStart = previous?.weekStart || window.CoachCalendar.weekStart(firstDate);
  const weeks = window.CoachCalendar.normalizeWeeks(allParsedWeeks);
  if (!weeks.length) return;
  allParsedWeeks = weeks;

  const originalCurrentWeek = findCurrentWeekIndex;
  findCurrentWeekIndex = function () {
    const start = window.CoachCalendar.weekStart(typeof todayIso === "string" ? todayIso : "");
    const index = start ? allParsedWeeks.findIndex(week => week.weekStart === start) : -1;
    return index >= 0 ? index : originalCurrentWeek();
  };
  renderSelectedWeek = function (weekIndex) {
    container.replaceChildren();
    const week = allParsedWeeks[weekIndex];
    if (!week) return;
    const grid = document.createElement("div");
    grid.className = "coach-calendar-grid";
    window.CoachCalendar.renderWeek(grid, week, renderDayCard);
    container.appendChild(grid);
  };
  renderSelectedWeek.calendarAligned = true;
  const selected = selectedStart ? weeks.findIndex(week => week.weekStart === selectedStart) : -1;
  activeWeekIndex = selected >= 0 ? selected : findCurrentWeekIndex();
  renderTabs();
  renderSelectedWeek(activeWeekIndex);
}());
