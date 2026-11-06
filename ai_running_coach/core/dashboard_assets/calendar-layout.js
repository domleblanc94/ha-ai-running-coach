/* Calendar presentation only: dates determine columns; no sessions are invented. */
(function (root) {
    'use strict';
    const dayNames = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'];
    function parsedDate(value) {
        if (typeof value !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return null;
        const day = new Date(value + 'T00:00:00Z');
        return Number.isFinite(day.getTime()) && day.toISOString().slice(0, 10) === value ? day : null;
    }
    function weekStart(value) {
        const day = parsedDate(value);
        if (!day) return null;
        day.setUTCDate(day.getUTCDate() - (day.getUTCDay() + 6) % 7);
        return day.toISOString().slice(0, 10);
    }
    function normalizeWeeks(weeks) {
        const dated = new Map(), unplaced = [];
        for (const week of weeks) {
            const starts = new Set(week.rows.map(row => weekStart(row.dateStr)).filter(Boolean));
            const unknown = [];
            for (const row of week.rows) {
                const start = weekStart(row.dateStr);
                if (!start) { unknown.push(row); continue; }
                if (!dated.has(start)) dated.set(start, {
                    title: starts.size === 1 ? week.title : 'Week of ' + start,
                    weekStart: start, rows: []
                });
                dated.get(start).rows.push(Object.assign({}, row, {
                    dayName: dayNames[(parsedDate(row.dateStr).getUTCDay() + 6) % 7]
                }));
            }
            if (unknown.length) unplaced.push({
                title: String(week.title).startsWith('Undated sessions') ? week.title : 'Undated sessions · ' + week.title,
                weekStart: null, rows: unknown
            });
        }
        return [...dated.values()].sort((a, b) => a.weekStart.localeCompare(b.weekStart))
            .concat(unplaced).map((week, index) => Object.assign({}, week, {
                index, rows: week.rows.slice().sort((a, b) => String(a.dateStr).localeCompare(String(b.dateStr)))
            }));
    }
    function configure(container) {
        if (!container) return;
        const document = container.ownerDocument;
        if (!document.getElementById('coach-calendar-style')) {
            const style = document.createElement('style');
            style.id = 'coach-calendar-style';
            style.textContent = `
                #calendarView { container-type: inline-size; }
                #calendarView .coach-calendar-grid { display:grid; grid-template-columns:minmax(0,1fr); gap:12px; }
                #calendarView .coach-calendar-weekdays, #calendarView .coach-calendar-empty { display:none; }
                #calendarView .coach-calendar-weekdays { padding-left:0; padding-right:0; }
                #calendarView .coach-calendar-day { min-width:0; display:flex; flex-direction:column; gap:12px; }
                #calendarView .coach-calendar-day > * { min-width:0; overflow-wrap:anywhere; }
                #calendarView .coach-calendar-day [data-session-id] .flex.justify-between.items-start { flex-wrap:wrap; gap:8px; }
                @container (min-width:640px) {
                    #calendarView .coach-calendar-grid { grid-template-columns:repeat(2,minmax(0,1fr)); }
                }
                @container (min-width:1120px) {
                    #calendarView .coach-calendar-grid:not(.coach-calendar-unplaced) { grid-template-columns:repeat(7,minmax(0,1fr)); }
                    #calendarView[data-calendar-dated="true"] .coach-calendar-weekdays { display:grid; grid-template-columns:repeat(7,minmax(0,1fr)); gap:12px; }
                    #calendarView .coach-calendar-empty { display:flex; }
                }`;
            document.head.appendChild(style);
        }
        const header = document.getElementById('calendarWeekdayHeader');
        if (header) header.classList.add('coach-calendar-weekdays');
    }
    function renderWeek(grid, week, renderDayCard) {
        const document = grid.ownerDocument;
        const calendar = document.getElementById('calendarView');
        configure(calendar);
        if (calendar) calendar.dataset.calendarDated = week.weekStart ? 'true' : 'false';
        grid.classList.add('coach-calendar-grid');
        const render = (parent, row) => renderDayCard(parent, row.cardId, row.dayName,
            row.dateStr, row.workoutType, row.distOrDuration, row.target);
        if (!week.weekStart) {
            grid.classList.add('coach-calendar-unplaced');
            week.rows.forEach(row => render(grid, row));
            return;
        }
        const start = parsedDate(week.weekStart);
        for (let offset = 0; offset < 7; offset++) {
            const day = new Date(start.getTime());
            day.setUTCDate(day.getUTCDate() + offset);
            const dateStr = day.toISOString().slice(0, 10);
            const rows = week.rows.filter(row => row.dateStr === dateStr);
            const column = document.createElement('div');
            column.dataset.calendarDate = dateStr;
            column.className = 'coach-calendar-day';
            if (rows.length) rows.forEach(row => render(column, row));
            else {
                column.className = 'coach-calendar-empty p-4 rounded-xl border border-dashed border-slate-700/50 flex-col gap-2 text-slate-500';
                column.dataset.calendarEmpty = 'true';
                for (const [text, className] of [[dayNames[offset], 'text-xs font-bold uppercase'],
                    [dateStr, 'text-[10px] font-mono'], ['No session planned', 'text-xs']]) {
                    const label = document.createElement('span');
                    label.textContent = text;
                    label.className = className;
                    column.appendChild(label);
                }
            }
            grid.appendChild(column);
        }
    }
    root.CoachCalendar = { version: 1, weekStart, normalizeWeeks, configure, renderWeek };
}(typeof window !== 'undefined' ? window : globalThis));
