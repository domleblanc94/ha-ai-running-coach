"""Original dashboard layout, preserved from the pre-upgrade project."""
def render_layout(*, assets, weeks_json, km_json, load_json, raw_markdown_json, activities_json, macro_review_json, last_updated_str, today_json, freshness_html, other_activities_html, session_ids_json, planned_json, agenda_html, focus_html, assessment_html, history_html, feedback_html, workout_sessions_json, sport_weeks_json="{}", training_mode_json='"running"', demo_mode=False):
    return f"""<!DOCTYPE html>
<html lang="en" class="dark">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{'Synthetic demo | ' if demo_mode else ''}AI Multisport Coach - Calendar Dashboard</title>
    <script>{assets['tailwind.js']}</script>
    <script>{assets['chart.js']}</script>
    <script>{assets['marked.js']}</script>
    <script>{assets['purify.js']}</script>
    <script>{assets['workout-fit.js']}</script>
    <script>{assets['workout-ui.js']}</script>
    <script id="coach-calendar-layout">{assets['calendar-layout.js']}</script>
    <style>{assets['fontawesome.css']}</style>
    <script>
        tailwind.config = {{
            darkMode: 'class',
            theme: {{
                extend: {{ colors: {{ slate: {{ 850: '#151f32' }} }} }}
            }}
        }}
    </script>
    <style>
        /* Dark themed scrollbars matching dashboard palette */
        ::-webkit-scrollbar {{
            height: 8px;
            width: 8px;
        }}
        ::-webkit-scrollbar-track {{
            background: #0f172a;
            border-radius: 4px;
        }}
        ::-webkit-scrollbar-thumb {{
            background: #334155;
            border-radius: 4px;
        }}
        ::-webkit-scrollbar-thumb:hover {{
            background: #475569;
        }}
        * {{
            scrollbar-color: #334155 #0f172a;
            scrollbar-width: thin;
        }}
    </style>
</head>
<body class="bg-slate-900 text-slate-100 font-sans min-h-screen p-4 md:p-8">
    <div class="max-w-7xl mx-auto space-y-8">
        {'''<section id="syntheticDemoNotice" role="note" class="border border-amber-500/60 bg-amber-950 text-amber-100 rounded-xl p-5 space-y-3">
            <h2 class="text-xl font-bold">SYNTHETIC DEMO · AI Multisport Coach</h2>
            <p>All athletes, activities, plans and reviews here are fictional examples, not personalized training advice. No live AI or Garmin account is connected. “Today” is the fixed sample date shown below.</p>
            <p class="text-sm">Try the calendar filters, charts and local FIT previews. Checkboxes stay in this browser; nothing is uploaded. Sample FIT files need manual watch transfer.</p>
            <nav aria-label="Install AI Multisport Coach" class="flex flex-wrap gap-3 text-sm font-semibold">
                <a class="underline underline-offset-4" href="https://github.com/domleblanc94/ha-ai-running-coach#readme">Home Assistant app</a>
                <a class="underline underline-offset-4" href="https://github.com/domleblanc94/ai-running-coach/blob/main/docs/STANDALONE.md">Standalone setup guide</a>
                <a class="underline underline-offset-4" href="https://github.com/domleblanc94/ai-running-coach">Source project</a>
                <a class="underline underline-offset-4" href="#calendarView">Explore the sample week</a>
            </nav>
        </section>''' if demo_mode else ''}

        <!-- Header -->
        <header class="flex flex-col md:flex-row justify-between items-start md:items-center gap-4 border-b border-slate-800 pb-6">
            <div>
                <h1 class="text-3xl font-extrabold text-sky-400 flex items-center gap-3">
                    <i class="fa-solid fa-calendar-days"></i> Training Plan Calendar
                </h1>
                <div class="flex flex-wrap items-center gap-3 mt-1 text-slate-400 text-sm">
                    <span>Interactive workout schedule, local run check-offs, and AI activity reviews</span>
                    <span class="inline-flex items-center gap-1.5 bg-slate-800 border border-slate-700/80 text-emerald-400 text-xs px-2.5 py-0.5 rounded-full font-mono">
                        <i class="fa-solid fa-clock text-[10px]"></i> Updated: {last_updated_str}
                    </span>
                </div>
            </div>

        </header>

        <section id="liveHealthBanner" role="status" class="border border-slate-700 bg-slate-800/60 rounded-xl p-3 text-xs text-slate-300">Live workflow status is loading. Saved sync and review times are shown below.</section>
        {agenda_html}
        {freshness_html}
        <!-- MACRO COACHING FEEDBACK BANNER -->
        <div id="macroCoachBanner" class="hidden bg-slate-850 border border-amber-500/40 rounded-xl p-5 shadow-lg relative overflow-hidden">
            <div class="flex items-center gap-3 mb-3 text-amber-400 font-bold text-lg border-b border-slate-700/60 pb-2">
                <i class="fa-solid fa-user-ninja"></i> AI Multisport Coach Daily Review & Focus
            </div>
            {assessment_html}
            <div id="macroCoachContent" class="text-xs md:text-sm text-slate-300 space-y-2 prose prose-invert prose-sm max-w-none leading-relaxed"></div>
        </div>

        <!-- Analytics Overview -->
        <div class="grid grid-cols-1 md:grid-cols-3 gap-6">
            <div class="bg-slate-800/60 border border-slate-800 rounded-xl p-5 shadow-lg md:col-span-2">
                <div class="flex flex-wrap justify-between items-center gap-3 mb-4">
                    <h2 class="text-base font-semibold text-slate-300 flex items-center gap-2">
                        <i class="fa-solid fa-chart-simple text-sky-400"></i> Weekly Volume & Load
                    </h2>
                    <div class="flex flex-wrap gap-2">
                        <label class="text-xs text-slate-400">Sport <select id="chartSport" class="bg-slate-900 border border-slate-600 rounded p-1"><option value="running">Running</option><option value="cycling">Cycling</option><option value="swimming">Swimming</option><option value="other">Other activities</option><option value="all">All sports · duration</option></select></label>
                        <button id="btnMinutes" onclick="switchChartMetric('minutes')" class="px-2.5 py-1 rounded text-xs border border-slate-700">Timer minutes</button>
                        <button id="btnKm" onclick="switchChartMetric('km')" class="px-2.5 py-1 rounded text-xs font-semibold bg-sky-500/20 text-sky-400 border border-sky-500/40">Distance (km)</button>
                        <button id="btnLoad" onclick="switchChartMetric('load')" class="px-2.5 py-1 rounded text-xs font-semibold bg-slate-800 text-slate-400 border border-slate-700 hover:text-slate-200">Garmin Load</button>
                    </div>
                </div>
                <div class="h-48">
                    <canvas id="volumeChart"></canvas>
                </div>
                <p id="chartCoverage" class="text-xs text-slate-400 mt-3"></p>
            </div>

            <div class="bg-slate-800/60 border border-slate-800 rounded-xl p-5 shadow-lg flex flex-col justify-between">
                <div>
                    <h2 class="text-base font-semibold text-slate-300 mb-3 flex items-center gap-2">
                        <i class="fa-solid fa-bullseye text-emerald-400"></i> This Week's Coaching Priorities
                    </h2>
                    <div id="weeklyPriorities">{focus_html}</div>
                    <ul class="hidden text-xs text-slate-400 space-y-2">
                        <li class="flex items-start gap-2"><span class="text-emerald-400 font-bold">•</span> Use week tabs to navigate training schedule blocks.</li>
                        <li class="flex items-start gap-2"><span class="text-sky-400 font-bold">•</span> Checkboxes are local reminders; recorded activities are listed separately.</li>
                        <li class="flex items-start gap-2"><span class="text-amber-400 font-bold">•</span> Follow the coach's session guidance and your practised fueling strategy.</li>
                    </ul>
                </div>
                <div class="mt-4 pt-3 border-t border-slate-700/50 flex justify-between text-xs text-slate-400">
                    <span>Legend:</span>
                    <span class="text-emerald-400">Easy</span> |
                    <span class="text-amber-400">Tempo</span> |
                    <span class="text-sky-400">Long session</span> |
                    <span class="text-slate-500">Rest</span>
                </div>
            </div>
        </div>

        <!-- WEEKLY CALENDAR VIEW -->
        <div id="calendarView" class="space-y-6">
            <div class="flex flex-wrap items-center gap-3"><label class="text-sm text-slate-300">Show <select id="calendarSport" class="bg-slate-900 border border-slate-600 rounded px-2 py-1"><option value="all">All sessions</option><option value="running">Running</option><option value="cycling">Cycling</option><option value="swimming">Swimming</option><option value="multisport">Triathlon</option><option value="other">Strength, mobility &amp; other</option></select></label><p id="calendarSportSummary" class="text-xs text-slate-400"></p></div>
            <div class="flex items-center gap-2 overflow-x-auto pb-3 border-b border-slate-800/80" id="weekTabsContainer"></div>

            <div id="calendarWeekdayHeader" class="coach-calendar-weekdays text-center text-xs font-bold text-slate-400 uppercase tracking-wider bg-slate-800/80 p-3 rounded-lg border border-slate-800">
                <div>Mon</div>
                <div>Tue</div>
                <div>Wed</div>
                <div>Thu</div>
                <div>Fri</div>
                <div>Sat</div>
                <div>Sun</div>
            </div>

            <div id="calendarGridContainer"></div>
        </div>

        {history_html}
        {feedback_html}
        {other_activities_html}

    </div>

    <script>
        const rawMarkdown = {raw_markdown_json};
        const weeksData = {weeks_json};
        const kmData = {km_json};
        const loadData = {load_json};
        const plannedData = {planned_json};
        let dataWindow = 56;
        let chartMetric = "km";
        const completedActivities = {activities_json};
        const macroReview = {macro_review_json};
        const todayIso = {today_json};
        const sessionIds = {session_ids_json};
        const workoutSessions = {workout_sessions_json};
        const sportWeeks = {sport_weeks_json};
        const trainingMode = {training_mode_json};
        const sportLabels = {{running:'Running',cycling:'Cycling',swimming:'Swimming',multisport:'Triathlon',strength:'Strength',mobility:'Mobility',rest:'Rest',other:'Other'}};
        let calendarSport = 'all';
        let chartSport = trainingMode === 'triathlon' ? 'all' : trainingMode;
        function rowSport(row) {{return (workoutSessions[row.cardId] || {{}}).display_sport || (workoutSessions[row.cardId] || {{}}).sport || 'other';}}
        function setCalendarSport(value) {{calendarSport=value;renderSelectedWeek(activeWeekIndex);}}
        document.getElementById('calendarSport').addEventListener('change',event=>setCalendarSport(event.target.value));
        document.getElementById('chartSport').addEventListener('change',event=>switchChartSport(event.target.value));
        function brickOrder(a,b) {{
            const left=(workoutSessions[a.cardId] || {{}}).brick, right=(workoutSessions[b.cardId] || {{}}).brick;
            if (left && right && left.id===right.id) return left.order-right.order;
            return 0;
        }}
        document.addEventListener('DOMContentLoaded', () => {{
            document.querySelectorAll('[data-workout-session]').forEach(node => window.WorkoutDownload.attach(node, workoutSessions[node.dataset.workoutSession]));
        }});
        const usedIds = {{}};

        function escapeHtml(value) {{
            const span = document.createElement('span');
            span.textContent = value == null ? '' : String(value);
            return span.innerHTML;
        }}
        function safeMarkdown(value) {{
            return DOMPurify.sanitize(marked.parse(value || ''), {{USE_PROFILES: {{html: true}}, FORBID_TAGS: ['style','form','input']}});
        }}

        function healthDisplay(state, now=Date.now()) {{
            if (!state || state.schema_version!==1 || !Array.isArray(state.issues)) return {{level:'unknown',label:'Live workflow status unavailable',details:'The saved dashboard remains usable.'}};
            const updated=Date.parse(state.updated_at || '');
            const pipeline=Date.parse(state.last_pipeline_success_at || '');
            const published=Date.parse(state.last_publish_success_at || '');
            if (!Number.isFinite(updated) || updated>now+300000) return {{level:'unknown',label:'Workflow status timestamp is unavailable',details:'Check the saved sync and review times.'}};
            if ((Number.isFinite(pipeline) && pipeline>now+300000) || (Number.isFinite(published) && published>now+300000)) return {{level:'unknown',label:'Workflow success timestamps are inconsistent',details:'Check the server clock and saved status.'}};
            const auth=state.garmin_auth;
            if (auth && auth.schema_version===1 && auth.state==='action_required') return {{level:'error',label:(now-updated>30*3600000?'Last reported input required: Garmin':'Input required: Garmin'),details:[auth.message,auth.action,'Last confirmed activity sync: '+(state.last_sync_success_at || 'not yet confirmed'),now-updated>30*3600000?'This status is stale; check the VM before acting.':''].filter(Boolean).join(' ')}};
            if (now-updated>30*3600000) return {{level:'warning',label:'Workflow status is stale',details:'No fresh status received in over 30 hours. This may include a failed connection or a stopped daily job.'}};
            if (auth && auth.schema_version===1 && auth.state==='temporary_error') return {{level:'warning',label:'Garmin sync temporarily unavailable',details:[auth.message,auth.action,'Last confirmed activity sync: '+(state.last_sync_success_at || 'not yet confirmed')].join(' ')}};
            if (state.phase==='migration_pending') return {{level:'info',label:'Official Garmin data ready; first coach review pending',details:state.issues.join(' | ')}};
            if (Array.isArray(state.issues) && state.issues.length) return {{level:'error',label:'The workflow needs attention',details:state.issues.join(' | ')}};
            if (['syncing','processing','coaching','rendering','publishing'].includes(state.phase)) return {{level:now-updated>2*3600000?'warning':'info',label:now-updated>2*3600000?'Workflow may have stopped before completion':'Daily workflow is running',details:'Stage: '+state.phase+'. Last completed run and upload timestamps remain below.'}};
            if (Number.isFinite(pipeline) && now-pipeline>30*3600000) return {{level:'warning',label:'The daily workflow is overdue',details:'Last successful daily run: '+state.last_pipeline_success_at}};
            if (state.phase==='awaiting_publish') return {{level:'info',label:'Daily review ready; awaiting scheduled upload',details:'The saved HTML will be uploaded by the existing publication schedule.'}};
            if (!Number.isFinite(pipeline)) return {{level:'unknown',label:'A successful daily run has not been confirmed yet',details:'Check the saved plan and review times.'}};
            if (now-pipeline>30*3600000) return {{level:'warning',label:'The daily workflow is overdue',details:'Last successful daily run: '+state.last_pipeline_success_at}};
            if (!Number.isFinite(published) || now-published>30*3600000) return {{level:'warning',label:'A recent dashboard upload has not been confirmed',details:'Last successful upload: '+(state.last_publish_success_at || 'Unavailable')}};
            return {{level:'ok',label:'Last completed workflow and dashboard upload succeeded',details:'Daily run: '+state.last_pipeline_success_at+' | Upload: '+state.last_publish_success_at}};
        }}
        function displayHealth(result) {{
            const banner=document.getElementById('liveHealthBanner');
            const colors={{ok:'border-emerald-500/30 text-emerald-300',info:'border-sky-500/30 text-sky-300',unknown:'border-slate-600 text-slate-300',warning:'border-amber-500/50 text-amber-300',error:'border-red-500/50 text-red-300'}};
            banner.className='border bg-slate-800/60 rounded-xl p-3 text-xs '+(colors[result.level] || colors.unknown);
            banner.replaceChildren();
            const title=document.createElement('strong');title.textContent=result.label;
            const details=document.createElement('p');details.className='mt-1 text-slate-400';details.textContent=result.details;
            banner.append(title,details);
        }}
        async function refreshWorkflowHealth() {{
            if ({str(bool(demo_mode)).lower()}) {{
                displayHealth({{level:'info',label:'Static fictional example',details:'Sample date: '+todayIso+'. No coaching job, Garmin sync or status polling runs on this demo.'}});return;
            }}
            if (!['http:','https:'].includes(location.protocol)) {{
                displayHealth({{level:'unknown',label:'Live status is available on the Home Assistant page',details:'This offline preview shows the saved plan and timestamps.'}});return;
            }}
            const controller=new AbortController();const timer=setTimeout(()=>controller.abort(),5000);
            try {{
                const url=new URL('coach-status.json',location.href);url.searchParams.set('t',String(Date.now()));
                const response=await fetch(url,{{cache:'no-store',credentials:'same-origin',signal:controller.signal}});
                if(!response.ok)throw Error('Status unavailable');
                displayHealth(healthDisplay(await response.json()));
            }} catch {{
                displayHealth({{level:'unknown',label:'Live workflow status could not be loaded',details:'The saved dashboard remains usable; its current workflow health is unknown.'}});
            }} finally {{clearTimeout(timer);}}
        }}
        refreshWorkflowHealth();
        if (!{str(bool(demo_mode)).lower()}) {{
            setInterval(refreshWorkflowHealth,60000);
            document.addEventListener('visibilitychange',()=>{{if(!document.hidden)refreshWorkflowHealth();}});
        }}

        let allParsedWeeks = [];
        let activeWeekIndex = 0;
        let volumeChart = null;

        // Render Daily Macro Review Banner if present
        if (macroReview && macroReview.trim().length > 0) {{
            const macroBanner = document.getElementById('macroCoachBanner');
            const macroContent = document.getElementById('macroCoachContent');
            if (macroReview.startsWith('Applied plan changes') || macroReview.startsWith('Saved coaching changes')) {{
                const parts = macroReview.split('\\n\\n');
                macroContent.innerHTML = safeMarkdown(parts.slice(2).join('\\n\\n')) + '<p class="text-sky-300 mt-2">' + escapeHtml((parts[1] || '').replace('on_track','On track').replace('insufficient_evidence','Insufficient evidence').replace('ahead','Ahead').replace('behind','Behind')) + '</p><details class="mt-3"><summary class="cursor-pointer text-amber-400">Applied changes and coaching rationale</summary><div class="whitespace-pre-wrap mt-2">' + escapeHtml(parts[0]) + '</div></details>';
            }} else {{
                macroContent.innerHTML = macroReview.startsWith('Historical review') ? '<details><summary>Historical coach review (previous policy)</summary>' + safeMarkdown(macroReview) + '</details>' : safeMarkdown(macroReview);
            }}
            macroBanner.classList.remove('hidden');
        }}


        function parseAndBuildCalendar(md) {{
            window.CoachCalendar.configure(document.getElementById('calendarView'));
            const tempDiv = document.createElement('div');
            tempDiv.innerHTML = safeMarkdown(md);

            const tables = tempDiv.querySelectorAll('table');
            allParsedWeeks = [];
            for (const key of Object.keys(usedIds)) delete usedIds[key];

            if (tables.length > 0) {{
                tables.forEach((table, weekIdx) => {{
                    const rows = Array.from(table.querySelectorAll('tbody tr'));
                    const headers = Array.from(table.querySelectorAll('thead th')).map(cell => cell.innerText.trim().toLowerCase());
                    if (rows.length === 0) return;

                    let weekTitle = `Week ${{weekIdx + 1}}`;

                    let prev = table.previousElementSibling;
                    while (prev) {{
                        if (['H1', 'H2', 'H3', 'H4'].includes(prev.tagName)) {{
                            weekTitle = prev.innerText.trim();
                            break;
                        }}
                        prev = prev.previousElementSibling;
                    }}

                    const weekObj = {{
                        index: weekIdx,
                        title: weekTitle,
                        rows: []
                    }};

                    rows.forEach((row, rowIdx) => {{
                        const cols = Array.from(row.querySelectorAll('td')).map(td => td.innerText.trim());
                        if (cols.length < 2) return;

                        const cell = (name, fallback) => cols[headers.includes(name) ? headers.indexOf(name) : fallback] || '';
                        const dayName = cell('day',0);
                        const dateStr = cols.length >= 5 ? cell('date',1) : '';
                        const workoutType = cell('workout type',cols.length >= 5 ? 2 : 1);
                        const distOrDuration = cell('distance / duration',cols.length >= 5 ? 3 : 2) || '—';
                        const target = cell('target',cols.length >= 5 ? 4 : 3);
                        const sportText = headers.includes('sport') ? cell('sport') : '';

                        const identityKey = dateStr + '|' + workoutType;
                        const occurrence = usedIds[identityKey] || 0;
                        usedIds[identityKey] = occurrence + 1;
                        const cardId = (sessionIds[identityKey] || [])[occurrence] || `run_${{dateStr}}_${{rowIdx}}_${{encodeURIComponent(workoutType)}}`;
                        if (!workoutSessions[cardId]) {{
                            const named = {{'running':'running','cycling':'cycling','swimming':'swimming','pool swimming':'swimming','open-water swimming':'swimming','triathlon':'multisport','strength':'strength','mobility':'mobility','rest':'rest'}};
                            const lower = workoutType.replace(/([a-z])([A-Z])/g,'$1 $2').toLowerCase().replace(/[^a-z]+/g,' ');
                            const inferred = /\\b(swim|swimming)\\b/.test(lower) ? 'swimming' : /\\b(cycling|cycle|bike|biking|ride)\\b/.test(lower) ? 'cycling' : /\\bstrength\\b/.test(lower) ? 'strength' : /\\b(mobility|yoga)\\b/.test(lower) ? 'mobility' : /\\b(rest|off)\\b/.test(lower) ? 'rest' : /\\b(run|running|easy|long|tempo|intervals?|race|hill|trail|strides?)\\b/.test(lower) ? 'running' : 'other';
                            const sport = named[sportText.toLowerCase()] || inferred;
                            workoutSessions[cardId] = {{id:cardId,date:dateStr,workout_type:workoutType,amount:distOrDuration,target,display_sport:sport,sport_label:sportText || sportLabels[sport]}};
                            if (sportText) workoutSessions[cardId].sport = sport;
                            if (sportText==='Pool swimming') workoutSessions[cardId].sub_sport='lap_swimming';
                            if (sportText==='Open-water swimming') workoutSessions[cardId].sub_sport='open_water';
                        }}
                        weekObj.rows.push({{
                            cardId,
                            dayName,
                            dateStr,
                            workoutType,
                            distOrDuration,
                            target
                        }});
                    }});

                    allParsedWeeks.push(weekObj);
                }});

                allParsedWeeks = window.CoachCalendar.normalizeWeeks(allParsedWeeks);
                activeWeekIndex = findCurrentWeekIndex();
                renderTabs();
                renderSelectedWeek(activeWeekIndex);
                return;
            }}

            document.getElementById('calendarGridContainer').innerHTML = `
                <div class="p-8 text-center bg-slate-800/40 border border-slate-800 rounded-xl">
                    <p class="text-slate-400">No workout tables detected in <code>training_plan.md</code>.</p>
                </div>`;
        }}

        function findCurrentWeekIndex() {{
            if (allParsedWeeks.length === 0) return 0;
            const mondayStr = window.CoachCalendar.weekStart(todayIso);
            const current = allParsedWeeks.findIndex(week => week.weekStart === mondayStr);
            return current < 0 ? 0 : current;
        }}

        function renderTabs() {{
            const tabsContainer = document.getElementById('weekTabsContainer');
            tabsContainer.innerHTML = '';

            allParsedWeeks.forEach((week, idx) => {{
                const isActive = idx === activeWeekIndex;
                const btn = document.createElement('button');
                btn.type = 'button';
                btn.className = `px-4 py-2 rounded-lg text-xs font-semibold whitespace-nowrap transition border shrink-0 ${{
                    isActive
                        ? 'bg-sky-500/20 text-sky-400 border-sky-500/50 shadow-sm ring-1 ring-sky-500/30'
                        : 'bg-slate-800/80 text-slate-400 border-slate-700/60 hover:bg-slate-700 hover:text-slate-200'
                }}`;
                btn.innerHTML = `${{isActive ? '<i class="fa-solid fa-calendar-day mr-1.5"></i>' : ''}}${{escapeHtml(week.title)}}`;
                btn.onclick = () => {{
                    activeWeekIndex = idx;
                    renderTabs();
                    renderSelectedWeek(idx);
                }};
                tabsContainer.appendChild(btn);
            }});
        }}

        function renderSelectedWeek(weekIdx) {{
            const container = document.getElementById('calendarGridContainer');
            container.innerHTML = '';

            const selectedWeek = allParsedWeeks[weekIdx];
            if (!selectedWeek) return;

            const grid = document.createElement('div');
            grid.className = 'coach-calendar-grid';
            const counts = {{}};
            selectedWeek.rows.forEach(row => {{const sport=rowSport(row); counts[sport]=(counts[sport] || 0)+1;}});
            document.getElementById('calendarSportSummary').textContent='Whole week: '+Object.entries(counts).map(([sport,count])=>(sportLabels[sport] || 'Other')+' '+count).join(' · ');
            const header=document.getElementById('calendarWeekdayHeader');
            if (calendarSport === 'all') {{
                header.style.removeProperty('display');
                window.CoachCalendar.renderWeek(grid, {{...selectedWeek,rows:[...selectedWeek.rows].sort(brickOrder)}}, renderDayCard);
            }} else {{
                header.style.display='none';
                grid.className='grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3';
                const rows=selectedWeek.rows.filter(row=>calendarSport==='other' ? ['strength','mobility','rest','other'].includes(rowSport(row)) : rowSport(row)===calendarSport).sort(brickOrder);
                rows.forEach(row=>renderDayCard(grid,row.cardId,row.dayName,row.dateStr,row.workoutType,row.distOrDuration,row.target));
                if(!rows.length) {{const empty=document.createElement('p');empty.className='text-slate-400 p-4';empty.textContent='No matching sessions this week. Other planned sessions remain in All sessions.';grid.appendChild(empty);}}
            }}

            container.appendChild(grid);
        }}
        renderSelectedWeek.calendarAligned = true;

        function renderDayCard(gridContainer, cardId, dayName, dateStr, workoutType, distOrDuration, target) {{
            let badgeClass = 'bg-slate-800/80 border-slate-700/60 text-slate-300';
            let icon = 'fa-bed';

            const typeLower = workoutType.toLowerCase();
            if (typeLower.includes('tempo') || typeLower.includes('threshold') || typeLower.includes('speed') || typeLower.includes('interval')) {{
                badgeClass = 'bg-amber-950/40 border-amber-500/30 text-amber-300';
                icon = 'fa-bolt';
            }} else if (typeLower.includes('long')) {{
                badgeClass = 'bg-sky-950/40 border-sky-500/30 text-sky-300';
                icon = 'fa-person-running';
            }} else if (typeLower.includes('easy') || typeLower.includes('aerobic') || typeLower.includes('recovery run')) {{
                badgeClass = 'bg-emerald-950/40 border-emerald-500/30 text-emerald-300';
                icon = 'fa-heart-pulse';
            }} else if (typeLower.includes('rest') || typeLower.includes('off')) {{
                badgeClass = 'bg-slate-800/30 border-slate-800 text-slate-500';
                icon = 'fa-mug-hot';
            }}

            const session = workoutSessions[cardId] || {{}};
            const sport = session.display_sport || session.sport || 'other';
            icon = {{running:'fa-person-running',cycling:'fa-bicycle',swimming:'fa-person-swimming',multisport:'fa-flag-checkered',strength:'fa-dumbbell',mobility:'fa-child-reaching',rest:'fa-mug-hot',other:'fa-calendar'}}[sport] || 'fa-calendar';
            const brick = session.brick;
            const sportText = (session.sport_label || sportLabels[sport] || 'Other') + (brick ? ' · Brick leg '+brick.order : '');
            const dbActivities = (completedActivities[dateStr] || []).filter(activity=>activity.sport===sport);
            const isPast = dateStr < todayIso;
            const displayWorkout = isPast ? (dbActivities.length ? 'Recorded activities' : 'No recorded activity') : workoutType;
            const displayAmount = isPast ? (dbActivities.length + ' recorded on ' + dateStr) : distOrDuration;
            let isManualChecked = false;
            try {{ isManualChecked = localStorage.getItem(cardId) === 'true'; }} catch {{}}
            const isCompleted = isManualChecked;
            const dayCard = document.createElement('div');
            dayCard.className = `p-4 rounded-xl border flex flex-col justify-between shadow-sm hover:border-slate-600 transition min-h-[160px] relative ${{badgeClass}} ${{isCompleted ? 'ring-2 ring-emerald-500/50' : ''}}`;
            const activityReviewHtml = dbActivities.map(activity => {{
                const feedback = activity.comment ? (activity.feedback_current ? `<div class="text-slate-300 italic whitespace-normal break-words leading-tight">${{escapeHtml(activity.comment)}}</div>` : `<details><summary class="cursor-pointer">Historical feedback (previous policy)</summary><div>${{escapeHtml(activity.comment)}}</div></details>`) : '';
                return `<div class="mt-3 text-[11px] bg-slate-900/80 p-2 rounded border border-emerald-500/30 space-y-1"><div class="text-emerald-400 font-bold"><i class="fa-solid fa-circle-check"></i> Recorded: ${{escapeHtml(activity.name)}} (${{escapeHtml(activity.amount_label)}})</div><div>${{escapeHtml(activity.type)}} · ${{escapeHtml(activity.rate_label || '')}}</div>${{feedback}}</div>`;
            }}).join('');

            dayCard.innerHTML = `
                <div>
                    <div class="flex justify-between items-start mb-2">
                        <div>
                            <span class="font-bold text-xs uppercase tracking-wider opacity-80">${{escapeHtml(dayName)}}</span>
                            ${{dateStr ? `<span class="block text-[10px] opacity-60 font-mono">${{escapeHtml(dateStr)}}</span>` : ''}}
                        </div>
                        <div class="flex items-center gap-2">
                            <i class="fa-solid ${{icon}} text-xs opacity-70"></i>
                            <input type="checkbox" title="Local reminder only" ${{isCompleted ? 'checked' : ''}}  class="w-4 h-4 accent-emerald-500 rounded cursor-pointer">
                        </div>
                    </div>
                    <div class="text-[11px] opacity-80 mb-1">${{escapeHtml(sportText)}}</div>
                    <div class="font-semibold text-sm leading-tight text-slate-100 ${{isCompleted ? 'line-through opacity-80' : ''}}">${{escapeHtml(displayWorkout)}}</div>
                    <div class="text-xs font-bold mt-1 text-sky-400">${{escapeHtml(displayAmount)}}</div>
                </div>
                <div>
                    ${{isPast ? activityReviewHtml + `<details class="text-[11px] mt-3 text-slate-400"><summary class="cursor-pointer">Original planned session</summary><p class="mt-2">${{escapeHtml(workoutType)}} &middot; ${{escapeHtml(distOrDuration)}}</p><p class="mt-1">${{escapeHtml(target)}}</p></details>` : (target ? `<div class="mt-2 text-[11px] opacity-75 border-t border-slate-700/40 pt-1.5 break-words whitespace-normal leading-snug">${{escapeHtml(target)}}</div>` : '') + activityReviewHtml}}
                </div>
            `;
            dayCard.dataset.sessionId = cardId;
            dayCard.dataset.sport = sport;
            if(brick) {{dayCard.dataset.brickId=brick.id;dayCard.dataset.brickOrder=brick.order;}}
            dayCard.querySelector('input[type=checkbox]').id = cardId;
            dayCard.querySelector('input[type=checkbox]').addEventListener('change', () => toggleRunComplete(cardId));
            if (!isPast) window.WorkoutDownload.attach(dayCard, workoutSessions[cardId] || {{id:cardId,date:dateStr,workout_type:workoutType,amount:distOrDuration,target}});
            gridContainer.appendChild(dayCard);
        }}

        function toggleRunComplete(cardId) {{
            const checkbox = document.getElementById(cardId);
            try {{ localStorage.setItem(cardId, checkbox.checked); }} catch {{}}
            renderSelectedWeek(activeWeekIndex);
        }}

        parseAndBuildCalendar(rawMarkdown);

        function showCalendarToday() {{
            document.getElementById('calendarView').classList.remove('hidden');
            activeWeekIndex=findCurrentWeekIndex();renderTabs();renderSelectedWeek(activeWeekIndex);
        }}

        const ctx = document.getElementById('volumeChart').getContext('2d');
        volumeChart = new Chart(ctx, {{
            type: 'bar',
            data: {{
                labels: weeksData,
                datasets: [{{
                    label: 'Actual Kilometers Completed',
                    data: kmData,
                    backgroundColor: '#38bdf8',
                    borderRadius: 6
                }}]
            }},
            options: {{
                responsive: true,
                maintainAspectRatio: false,
                plugins: {{ legend: {{ display: false }} }},
                scales: {{
                    y: {{ beginAtZero: true, grid: {{ color: '#1e293b' }} }},
                    x: {{ grid: {{ display: false }} }}
                }}
            }}
        }});
        document.getElementById('chartSport').value=chartSport;
        switchChartMetric(chartSport==='all' ? 'minutes' : 'km');

        function setDataWindow(days) {{
            dataWindow=days;
            document.querySelectorAll('[data-running-window]').forEach(el=>el.classList.toggle('hidden',Number(el.dataset.runningWindow)!==days));
            switchChartMetric(chartMetric);
        }}
        function switchChartSport(sport) {{
            chartSport=sport;
            switchChartMetric(['all','other'].includes(sport) ? 'minutes' : chartMetric);
        }}
        function switchChartMetric(type) {{
            if (['all','other'].includes(chartSport)) type='minutes';
            chartMetric=type;
            const count=dataWindow===28?4:8;
            const entries=(sportWeeks[chartSport] || []).slice(-count);
            const unit=chartSport==='swimming' ? 'm' : 'km';
            const field=type==='km' ? 'distance' : type;
            const label=type==='minutes' ? 'Recorded timer minutes (may include rests)' : type==='load' ? 'Recorded Garmin training load' : 'Recorded '+(sportLabels[chartSport] || 'other activities')+' distance ('+unit+')';
            volumeChart.data.labels=weeksData.slice(-count);
            volumeChart.data.datasets=[{{label,data:entries.map(entry=>entry[field]),backgroundColor:type==='load' ? '#f59e0b' : '#38bdf8',borderRadius:6}}];
            if(type==='km' && chartSport==='running') volumeChart.data.datasets.push({{label:'Known planned running km (time-only sessions excluded)',data:plannedData.slice(-count),backgroundColor:'#64748b',borderRadius:6}});
            for(const [id,key] of [['btnKm','km'],['btnLoad','load'],['btnMinutes','minutes']]) {{
                const button=document.getElementById(id);
                button.hidden=['all','other'].includes(chartSport) && key!=='minutes';
                button.className='px-2.5 py-1 rounded text-xs font-semibold border '+(type===key ? 'bg-sky-500/20 text-sky-400 border-sky-500/40' : 'bg-slate-800 text-slate-400 border-slate-700');
            }}
            document.getElementById('btnKm').textContent='Distance ('+unit+')';
            const records=entries.reduce((sum,entry)=>sum+entry.count,0);
            const coverage=entries.reduce((sum,entry)=>sum+(entry[type==='km' ? 'distance_coverage' : 'duration_coverage'] || 0),0);
            document.getElementById('chartCoverage').textContent=(chartSport==='all' ? 'All sports' : sportLabels[chartSport] || 'Other activities')+' · '+records+' recorded activities. '+(type==='load' ? 'Only recorded load is shown; no missing values estimated.' : coverage+'/'+records+' '+(type==='km' ? 'distances' : 'timer durations')+' known. Missing values are not estimated; partial totals contain only recorded values.');
            volumeChart.update();
        }}
    </script>
</body>
</html>
"""
