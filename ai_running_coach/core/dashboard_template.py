"""Original dashboard layout, preserved from the pre-upgrade project."""
def render_layout(*, assets, weeks_json, km_json, load_json, raw_markdown_json, activities_json, macro_review_json, last_updated_str, today_json, freshness_html, other_activities_html, session_ids_json, planned_json, agenda_html, focus_html, assessment_html, history_html, feedback_html, workout_sessions_json):
    return f"""<!DOCTYPE html>
<html lang="en" class="dark">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>AI Endurance Coach - Calendar Dashboard</title>
    <script>{assets['tailwind.js']}</script>
    <script>{assets['chart.js']}</script>
    <script>{assets['marked.js']}</script>
    <script>{assets['purify.js']}</script>
    <script>{assets['workout-fit.js']}</script>
    <script>{assets['workout-ui.js']}</script>
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
                <i class="fa-solid fa-user-ninja"></i> AI Coach Daily Review & Focus
            </div>
            {assessment_html}
            <div id="macroCoachContent" class="text-xs md:text-sm text-slate-300 space-y-2 prose prose-invert prose-sm max-w-none leading-relaxed"></div>
        </div>

        <!-- Analytics Overview -->
        <div class="grid grid-cols-1 md:grid-cols-3 gap-6">
            <div class="bg-slate-800/60 border border-slate-800 rounded-xl p-5 shadow-lg md:col-span-2">
                <div class="flex justify-between items-center mb-4">
                    <h2 class="text-base font-semibold text-slate-300 flex items-center gap-2">
                        <i class="fa-solid fa-chart-simple text-sky-400"></i> Weekly Volume & Load
                    </h2>
                    <div class="flex gap-2">
                        <button id="btnKm" onclick="switchChartMetric('km')" class="px-2.5 py-1 rounded text-xs font-semibold bg-sky-500/20 text-sky-400 border border-sky-500/40">Distance (km)</button>
                        <button id="btnLoad" onclick="switchChartMetric('load')" class="px-2.5 py-1 rounded text-xs font-semibold bg-slate-800 text-slate-400 border border-slate-700 hover:text-slate-200">Garmin Load</button>
                    </div>
                </div>
                <div class="h-48">
                    <canvas id="volumeChart"></canvas>
                </div>
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
                    <span class="text-sky-400">Long Run</span> |
                    <span class="text-slate-500">Rest</span>
                </div>
            </div>
        </div>

        <!-- WEEKLY CALENDAR VIEW -->
        <div id="calendarView" class="space-y-6">
            <div class="flex items-center gap-2 overflow-x-auto pb-3 border-b border-slate-800/80" id="weekTabsContainer"></div>

            <div class="grid grid-cols-7 gap-2 text-center text-xs font-bold text-slate-400 uppercase tracking-wider bg-slate-800/80 p-3 rounded-lg border border-slate-800">
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
        setInterval(refreshWorkflowHealth,60000);
        document.addEventListener('visibilitychange',()=>{{if(!document.hidden)refreshWorkflowHealth();}});

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
            const tempDiv = document.createElement('div');
            tempDiv.innerHTML = safeMarkdown(md);

            const tables = tempDiv.querySelectorAll('table');
            allParsedWeeks = [];
            for (const key of Object.keys(usedIds)) delete usedIds[key];

            if (tables.length > 0) {{
                tables.forEach((table, weekIdx) => {{
                    const rows = Array.from(table.querySelectorAll('tbody tr'));
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

                        let dayName = cols[0];
                        let dateStr = '';
                        let workoutType = cols[1];
                        let distOrDuration = cols[2] || '—';
                        let target = cols[3] || '';

                        if (cols.length >= 5) {{
                            dateStr = cols[1];
                            workoutType = cols[2];
                            distOrDuration = cols[3] || '—';
                            target = cols[4] || '';
                        }}

                        const identityKey = dateStr + '|' + workoutType;
                        const occurrence = usedIds[identityKey] || 0;
                        usedIds[identityKey] = occurrence + 1;
                        weekObj.rows.push({{
                            cardId: (sessionIds[identityKey] || [])[occurrence] || `run_${{dateStr}}_${{rowIdx}}_${{encodeURIComponent(workoutType)}}`,
                            dayName,
                            dateStr,
                            workoutType,
                            distOrDuration,
                            target
                        }});
                    }});

                    allParsedWeeks.push(weekObj);
                }});

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
            const todayStr = {today_json};

            for (let i = 0; i < allParsedWeeks.length; i++) {{
                if (allParsedWeeks[i].rows.some(r => r.dateStr === todayStr)) {{
                    return i;
                }}
            }}

            const d = new Date();
            const day = d.getDay();
            const diff = d.getDate() - day + (day === 0 ? -6 : 1);
            d.setDate(diff);
            const mondayStr = `${{d.getFullYear()}}-${{String(d.getMonth()+1).padStart(2,'0')}}-${{String(d.getDate()).padStart(2,'0')}}`;

            for (let i = 0; i < allParsedWeeks.length; i++) {{
                if (allParsedWeeks[i].title.includes(mondayStr) || allParsedWeeks[i].rows.some(r => r.dateStr === mondayStr)) {{
                    return i;
                }}
            }}

            return 0;
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
            grid.className = 'grid grid-cols-1 sm:grid-cols-2 md:grid-cols-7 gap-3';

            selectedWeek.rows.forEach(row => {{
                renderDayCard(grid, row.cardId, row.dayName, row.dateStr, row.workoutType, row.distOrDuration, row.target);
            }});

            container.appendChild(grid);
        }}

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

            const dbActivities = completedActivities[dateStr] || [];
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
                return `<div class="mt-3 text-[11px] bg-slate-900/80 p-2 rounded border border-emerald-500/30 space-y-1"><div class="text-emerald-400 font-bold"><i class="fa-solid fa-circle-check"></i> Recorded: ${{escapeHtml(activity.name)}} (${{escapeHtml(activity.actual_km)}} km)</div><div>${{escapeHtml(activity.type)}}</div>${{feedback}}</div>`;
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
                    <div class="font-semibold text-sm leading-tight text-slate-100 ${{isCompleted ? 'line-through opacity-80' : ''}}">${{escapeHtml(displayWorkout)}}</div>
                    <div class="text-xs font-bold mt-1 text-sky-400">${{escapeHtml(displayAmount)}}</div>
                </div>
                <div>
                    ${{isPast ? activityReviewHtml + `<details class="text-[11px] mt-3 text-slate-400"><summary class="cursor-pointer">Original planned session</summary><p class="mt-2">${{escapeHtml(workoutType)}} &middot; ${{escapeHtml(distOrDuration)}}</p><p class="mt-1">${{escapeHtml(target)}}</p></details>` : (target ? `<div class="mt-2 text-[11px] opacity-75 border-t border-slate-700/40 pt-1.5 break-words whitespace-normal leading-snug">${{escapeHtml(target)}}</div>` : '') + activityReviewHtml}}
                </div>
            `;
            dayCard.dataset.sessionId = cardId;
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
        switchChartMetric("km");

        function setDataWindow(days) {{
            dataWindow=days;
            document.querySelectorAll('[data-running-window]').forEach(el=>el.classList.toggle('hidden',Number(el.dataset.runningWindow)!==days));
            switchChartMetric(chartMetric);
        }}
        function switchChartMetric(type) {{
            chartMetric=type;
            const count=dataWindow===28?4:8;
            volumeChart.data.labels=weeksData.slice(-count);
            const btnKm = document.getElementById('btnKm');
            const btnLoad = document.getElementById('btnLoad');

            if (type === 'km') {{
                volumeChart.data.datasets[0].data = kmData.slice(-count);
                volumeChart.data.datasets[0].label = 'Actual Kilometers Completed';
                volumeChart.data.datasets[0].backgroundColor = '#38bdf8';
                btnKm.className = 'px-2.5 py-1 rounded text-xs font-semibold bg-sky-500/20 text-sky-400 border border-sky-500/40';
                btnLoad.className = 'px-2.5 py-1 rounded text-xs font-semibold bg-slate-800 text-slate-400 border border-slate-700 hover:text-slate-200';
            }} else {{
                volumeChart.data.datasets[0].data = loadData.slice(-count);
                volumeChart.data.datasets[0].label = 'Garmin training load';
                volumeChart.data.datasets[0].backgroundColor = '#f59e0b';
                btnLoad.className = 'px-2.5 py-1 rounded text-xs font-semibold bg-amber-500/20 text-amber-400 border border-amber-500/40';
                btnKm.className = 'px-2.5 py-1 rounded text-xs font-semibold bg-slate-800 text-slate-400 border border-slate-700 hover:text-slate-200';
            }}
            if (type==='km') {{
                volumeChart.data.datasets[1]={{label:'Known planned running km (time-only sessions excluded)',data:plannedData.slice(-count),backgroundColor:'#64748b',borderRadius:6}};
            }} else {{ volumeChart.data.datasets=volumeChart.data.datasets.slice(0,1); }}
            volumeChart.update();
        }}
    </script>
</body>
</html>
"""
