"""Read-only dashboard panels. Interpretations come from the saved AI response."""
from datetime import date, timedelta
import html
import math
import re
from sports import activity_sport, session_sport, sport_label


def session_order(item):
    brick = item.get('brick') or {}
    return (item['date'], brick.get('id', item['id']), brick.get('order', 0), item['id'])


def activity_measure(item):
    """Keep recorded units and time bases explicit; never infer missing duration."""
    sport = activity_sport(item.get('type'))
    distance = item.get('distance_m')
    moving = item.get('moving_time_s')
    timer = item.get('timer_duration_s')
    amount = (f'{distance:g} m' if sport == 'swimming' else f'{distance / 1000:.2f} km') if number(distance) else 'Distance unavailable'
    rate = ''
    if number(distance) and distance > 0:
        if sport == 'running':
            rate = pace(distance, moving)
        elif sport == 'cycling' and number(moving) and moving > 0:
            rate = f'{distance / moving * 3.6:.1f} km/h moving'
        elif sport == 'swimming' and number(timer) and timer > 0:
            seconds = round(timer / (distance / 100))
            rate = f'{seconds // 60}:{seconds % 60:02d} /100 m timer (may include rests)'
    return amount, rate


def weekly_sport_data(activities, weeks, today):
    """Separate distances by sport; the combined series contains duration only."""
    result = {}
    for sport in ('all', 'running', 'cycling', 'swimming', 'other'):
        entries = []
        for week in weeks:
            end = min(week + timedelta(days=6), today)
            rows = [item for day, items in activities.items() if week.isoformat() <= day <= end.isoformat()
                    for item in items if sport == 'all' or (activity_sport(item.get('type')) == sport if sport != 'other'
                    else activity_sport(item.get('type')) not in ('running', 'cycling', 'swimming'))]
            timers = [r['timer_duration_s'] / 60 for r in rows if number(r.get('timer_duration_s'))]
            distances = [r['distance_m'] / (1 if sport == 'swimming' else 1000) for r in rows if number(r.get('distance_m'))]
            loads = [r['training_load'] for r in rows if number(r.get('training_load'))]
            entries.append({'count': len(rows), 'minutes': round(sum(timers), 1) if timers else (None if rows else 0),
                'duration_coverage': len(timers), 'distance': round(sum(distances), 2) if distances else (None if rows else 0),
                'distance_coverage': len(distances), 'load': round(sum(loads), 1) if loads else None})
        if sport in ('all', 'other'):
            for entry in entries:
                entry.pop('distance'); entry.pop('distance_coverage'); entry.pop('load')
        result[sport] = entries
    return result

def esc(value):
    return html.escape('' if value is None else str(value),quote=True)

def number(value):
    return isinstance(value,(int,float)) and not isinstance(value,bool) and math.isfinite(value) and value>=0

def duration(value):
    if not number(value):return 'Unavailable'
    value=round(value);hours,remainder=divmod(value,3600);minutes,seconds=divmod(remainder,60)
    return (str(hours)+'h ' if hours else '')+str(minutes)+'m '+str(seconds)+'s'

def pace(distance,moving):
    if not number(distance) or distance<=0 or not number(moving) or moving<=0:return 'Unavailable'
    seconds=round(moving/(distance/1000));minutes,seconds=divmod(seconds,60)
    return str(minutes)+':'+str(seconds).zfill(2)+' /km moving'

def coach_payload(conn):
    tables={row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if 'coach_review_jobs' not in tables:return {}
    import json
    row=conn.execute("SELECT response_json FROM coach_review_jobs WHERE status='complete' AND prompt_version IN ('autonomous-coach-v2','autonomous-coach-v3','autonomous-coach-v4-starter-week','autonomous-coach-v5-availability','autonomous-coach-v5-availability-starter-week','autonomous-coach-v6-schedule','autonomous-coach-v6-schedule-starter-week','autonomous-coach-v7-new-plan','autonomous-coach-v7-new-plan-starter-week','autonomous-coach-v8-multisport','autonomous-coach-v8-multisport-starter-week') ORDER BY updated_at DESC LIMIT 1").fetchone()
    try:
        payload=json.loads(row[0]) if row and row[0] else {}
        return payload if isinstance(payload,dict) else {}
    except (ValueError,TypeError):return {}

def build_panels(plan,activities,coach,today):
    today=date.fromisoformat(today) if isinstance(today,str) else today
    scheduled=sorted(plan.get('sessions',[]),key=session_order)
    today_sessions=[s for s in scheduled if s['date']==today.isoformat()]
    upcoming=[s for s in scheduled if s['date']>=today.isoformat() and session_sport(s) != 'rest'][:3]
    agenda='<section id="todayAgenda" class="bg-slate-800/60 border border-sky-500/30 rounded-xl p-4"><div class="flex justify-between gap-3 items-center"><h2 class="font-bold text-sky-300"><i class="fa-solid fa-person-running"></i> Today &amp; next workouts</h2><a href="#calendarView" onclick="showCalendarToday()" class="text-xs text-sky-300 underline">This week</a></div>'
    today_records=len(activities.get(today.isoformat(),[]))
    agenda+='<p class="text-xs text-slate-400 mt-2">Today: '+esc(', '.join(s['workout_type'] for s in today_sessions) or 'No session scheduled')+' &middot; '+str(today_records)+' recorded activities. A recording does not confirm a scheduled session was completed.</p><div class="grid grid-cols-1 md:grid-cols-3 gap-3 mt-3">'
    for session in upcoming:
        agenda+='<article data-workout-session="'+esc(session['id'])+'" class="rounded-lg bg-slate-900/60 border border-slate-700 p-3 text-sm"><time class="text-xs text-slate-400">'+esc(session['date'])+' &middot; '+esc(sport_label(session))+(' &middot; Brick leg '+esc(session['brick']['order']) if session.get('brick') else '')+(' &middot; Today' if session['date']==today.isoformat() else '')+'</time><h3 class="font-semibold mt-1">'+esc(session['workout_type'])+'</h3><p class="text-sky-300 text-xs mt-1">'+esc(session['amount'])+'</p><details class="text-xs text-slate-300 mt-2"><summary class="cursor-pointer">Effort &amp; preparation</summary><p class="mt-2">'+esc(session['target'])+'</p><p class="text-slate-500 mt-2">Planned ascent is not specified unless included in the coach guidance.</p></details></article>'
    if not upcoming:agenda+='<p class="text-slate-400">No upcoming workout saved. The next successful coach review can extend the plan.</p>'
    monday=today-timedelta(days=today.weekday());sunday=monday+timedelta(days=6)
    recorded=[item for day,items in activities.items() if monday.isoformat()<=day<=today.isoformat() for item in items]
    remaining=[item for item in scheduled if today.isoformat()<item['date']<=sunday.isoformat() and session_sport(item) != 'rest']
    agenda+='</div><div class="grid grid-cols-1 sm:grid-cols-3 gap-3 border-t border-slate-700 pt-3 mt-3">'
    for sport, label in [('running','Running'),('cycling','Cycling'),('swimming','Swimming')]:
        rows=[item for item in recorded if activity_sport(item.get('type'))==sport]
        values=[item['distance_m'] for item in rows if number(item.get('distance_m'))]
        shown=(str(round(sum(values),1))+' m' if sport=='swimming' else str(round(sum(values)/1000,2))+' km') if values else 'Distance unavailable'
        agenda+='<p class="text-xs text-slate-400"><strong class="text-sky-300">'+label+' this week</strong><br>'+str(len(rows))+' recorded activities · '+shown+'<br>'+str(len(values))+'/'+str(len(rows))+' distances known</p>'
    timers=[item['timer_duration_s'] for item in recorded if number(item.get('timer_duration_s'))]
    agenda+='</div><p class="text-xs text-slate-400 mt-3">All sports: '+(esc(duration(sum(timers)))+' known timer duration' if timers else 'timer duration unavailable')+' ('+str(len(timers))+'/'+str(len(recorded))+' known; may include rests). Still scheduled after today: '+str(len(remaining))+' non-rest sessions. No completion percentage is inferred.</p></section>'
    priorities=coach.get('weekly_priorities') or []
    focus='<ul class="text-xs text-slate-300 space-y-3">'
    for item in priorities[:3]:
        focus+='<li><strong class="text-emerald-300">'+esc(item.get('priority'))+'</strong><p class="mt-1">'+esc(item.get('reason'))+'</p>'
        if item.get('evidence'):focus+='<details class="mt-1 text-slate-400"><summary class="cursor-pointer">Why this priority</summary><p>'+esc('; '.join(item['evidence']))+'</p></details>'
        focus+='</li>'
    if not priorities:focus+='<li class="text-slate-400">Specific weekly priorities have not been supplied in the saved review yet.</li>'
    focus+='</ul>'
    assessment=coach.get('assessment') or {}
    explanation=''
    if assessment:
        explanation='<details class="mt-3 text-xs text-slate-300"><summary class="cursor-pointer text-sky-300">Assessment evidence &amp; uncertainty</summary><p class="mt-2">'+esc(assessment.get('reason'))+'</p>'
        for label,key in [('Evidence','evidence'),('Uncertainty','uncertainties')]:
            if assessment.get(key):explanation+='<p class="mt-2"><strong>'+label+':</strong> '+esc('; '.join(assessment[key]))+'</p>'
        explanation+='</details>'
    if coach.get('next_review_trigger'):explanation+='<p class="text-xs text-slate-400 mt-3"><strong>Next review:</strong> '+esc(coach['next_review_trigger'])+'</p>'
    from planning_engine import is_run
    runs=[item for day,items in activities.items() if (today-timedelta(days=55)).isoformat()<=day<=today.isoformat() for item in items if is_run(item.get('type')) and number(item.get('distance_m'))]
    longest=sorted(sorted(runs,key=lambda item:item['distance_m'],reverse=True)[:6],key=lambda item:str(item.get('start_date')),reverse=True)
    history='<details id="longRunHistory" class="bg-slate-800/60 border border-slate-700 rounded-xl p-4"><summary class="font-semibold text-sky-300 cursor-pointer">Recent longer-run history</summary><p class="text-xs text-slate-400 mt-2">Six longest recorded runs in the past 56 days, displayed by date. Terrain and stops affect pace; moving and elapsed time are separate.</p><div class="overflow-x-auto mt-3"><table class="text-xs w-full text-left"><thead class="text-slate-400"><tr><th class="p-2">Date / run</th><th class="p-2">Distance</th><th class="p-2">Ascent</th><th class="p-2">Moving time / pace</th><th class="p-2">Elapsed time</th></tr></thead><tbody>'
    for run in longest:
        history+='<tr class="border-t border-slate-700"><td class="p-2">'+esc(str(run.get('start_date',''))[:10])+'<br>'+esc(run.get('name'))+'</td><td class="p-2">'+esc(round(run['distance_m']/1000,2))+' km</td><td class="p-2">'+(esc(round(run['elevation_gain_m']))+' m' if number(run.get('elevation_gain_m')) else 'Unavailable')+'</td><td class="p-2">'+esc(duration(run.get('moving_time_s')))+'<br>'+esc(pace(run['distance_m'],run.get('moving_time_s')))+'</td><td class="p-2">'+esc(duration(run.get('elapsed_time_s')))+'</td></tr>'
    if not longest:history+='<tr><td colspan="5" class="p-2 text-slate-400">No distance-bearing running records in this window.</td></tr>'
    history+='</tbody></table></div></details>'
    endurance=[item for day,items in activities.items() if (today-timedelta(days=55)).isoformat()<=day<=today.isoformat() for item in items if activity_sport(item.get('type')) in ('cycling','swimming')]
    if endurance:
        history+='<details id="otherSportHistory" class="bg-slate-800/60 border border-slate-700 rounded-xl p-4"><summary class="font-semibold text-sky-300 cursor-pointer">Recent cycling &amp; swimming</summary><p class="text-xs text-slate-400 mt-2">Latest 12 records. Swimming timer pace may include rests; no running pace or heart-rate zones are applied to another sport.</p><div class="overflow-x-auto"><table class="text-xs w-full"><thead><tr><th class="p-2">Date / activity</th><th class="p-2">Sport / distance</th><th class="p-2">Recorded rate</th><th class="p-2">Timer duration</th></tr></thead><tbody>'
        for item in sorted(endurance,key=lambda row:str(row.get('start_date')),reverse=True)[:12]:
            amount,rate=activity_measure(item)
            history+='<tr class="border-t border-slate-700"><td class="p-2">'+esc(str(item.get('start_date',''))[:10])+'<br>'+esc(item.get('name'))+'</td><td class="p-2">'+esc(item.get('type'))+'<br>'+esc(amount)+'</td><td class="p-2">'+esc(rate or 'Unavailable')+'</td><td class="p-2">'+esc(duration(item.get('timer_duration_s')))+'</td></tr>'
        history+='</tbody></table></div></details>'
    feedback='<details id="feedbackHelp" class="bg-slate-800/60 border border-slate-700 rounded-xl p-4"><summary class="font-semibold text-sky-300 cursor-pointer">Tell the coach how a session felt</summary><p class="text-sm text-slate-300 mt-3">Add a Garmin activity note/description and sync, or save a dated check-in using coach_checkin.py on the VM. The coach receives only notes returned by the Garmin API or saved locally. Browser checkboxes are reminders only.</p><p class="text-xs text-slate-400 mt-2">Use only fields you actually know; every field is optional. RPE uses 1-10; fatigue uses 0-10. Do not invent intake amounts.</p><pre class="text-xs whitespace-pre-wrap break-words bg-slate-900 p-3 rounded mt-3">[coach: rpe=4; fatigue=2; pain=none; recovery=normal; fueling=2 gels; fueling_tolerance=good]</pre><p class="text-xs text-slate-400 mt-2">Rest-day feedback can be saved through the provided coach_checkin.py command on the VM. This static page does not submit a form.</p></details>'
    return {'agenda':agenda,'focus':focus,'assessment':explanation,'history':history,'feedback':feedback}
