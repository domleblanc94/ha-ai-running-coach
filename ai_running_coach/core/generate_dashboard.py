"""Offline mobile dashboard. All imported/model text is escaped, never executed."""
import argparse
import base64
from collections import defaultdict
from contextlib import closing
from datetime import date, datetime, timedelta, timezone
import html
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import tempfile

SCRIPT_DIR = Path(__file__).resolve().parent
DB_PATH = SCRIPT_DIR / "ai_coach_master.sqlite"
PLAN_FILE = SCRIPT_DIR / "training_plan.md"
OUTPUT_HTML = SCRIPT_DIR / "dashboard.html"

def text(value):
    if value is None or isinstance(value, float) and not math.isfinite(value):
        return ""
    return str(value)

def esc(value):
    return html.escape(text(value), quote=True)

def parse_sessions(markdown):
    sessions = []
    counts = defaultdict(int)
    for line in markdown.splitlines():
        if not line.lstrip().startswith("|"):
            continue
        cols = [cell.strip().replace("**", "") for cell in line.strip().strip("|").split("|")]
        if len(cols) < 5 or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", cols[1]):
            continue
        try:
            date.fromisoformat(cols[1])
        except ValueError:
            continue
        # Date + type + occurrence survives insertion/reordering of other weeks.
        identity = cols[1] + ":" + cols[2]
        counts[identity] += 1
        sessions.append(dict(day=cols[0], date=cols[1], kind=cols[2],
            amount=cols[3], target=cols[4], id=identity + ":" + str(counts[identity])))
    return sessions

def read_database(path, start, end):
    activities = defaultdict(list)
    review, reviewed_at = "", ""
    current_review = False
    if not Path(path).exists():
        return activities, review, reviewed_at
    # Rendering never migrates or mutates the database.
    uri = Path(path).resolve().as_uri() + "?mode=ro"
    with closing(sqlite3.connect(uri, uri=True)) as conn:
        conn.row_factory = sqlite3.Row
        columns = {row[1] for row in conn.execute("PRAGMA table_info(activities)")}
        if columns:
            wanted = [c for c in ("id", "name", "type", "start_date", "distance_m", "ai_feedback", "description", "moving_time_s", "elapsed_time_s", "elevation_gain_m") if c in columns]
            for row in conn.execute("SELECT " + ",".join(wanted) + " FROM activities WHERE date(start_date) BETWEEN ? AND ? ORDER BY start_date", (start, end)):
                item = dict(row)
                activities[text(item.get("start_date"))[:10]].append(item)
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "coach_review_jobs" in tables:
            job_columns = {row[1] for row in conn.execute("PRAGMA table_info(coach_review_jobs)")}
            policy_filter = " AND prompt_version IN ('autonomous-coach-v2','autonomous-coach-v3','autonomous-coach-v4-starter-week','autonomous-coach-v5-availability','autonomous-coach-v5-availability-starter-week')" if "prompt_version" in job_columns else ""
            reviewed_ids = set()
            for saved in conn.execute("SELECT response_json FROM coach_review_jobs WHERE status='complete' AND response_json IS NOT NULL" + policy_filter):
                try:
                    for item in json.loads(saved[0]).get("reviews", []):
                        reviewed_ids.add(str(item["activity_id"]))
                except (ValueError, KeyError, TypeError, AttributeError):
                    continue
            for records in activities.values():
                for item in records:
                    item["feedback_current"] = str(item.get("id")) in reviewed_ids
            row = conn.execute("SELECT response_json FROM coach_review_jobs WHERE status='complete'" + policy_filter + " ORDER BY updated_at DESC LIMIT 1").fetchone()
            if row and row[0]:
                current_review = True
                try:
                    payload = json.loads(row[0])
                    changes = payload.get("plan_changes", [])
                    if changes:
                        revision = payload.get("applied_revision")
                        label = "Applied plan changes" if revision is not None else "Saved coaching changes"
                        review += label + (" (revision " + str(revision) + ")" if revision is not None else "") + ":\n"
                        review += "\n".join(text(p.get("op")) + " " + text(p.get("session_id")) + ": " + text(p.get("reason")) for p in changes) + "\n\n"
                    if payload.get("trajectory"):
                        review += "Race progress assessment: " + text(payload["trajectory"]) + "\n\n"
                except (ValueError, AttributeError, TypeError):
                    pass
        if "daily_coach_reviews" in tables:
            row = conn.execute("SELECT summary_text,created_at FROM daily_coach_reviews ORDER BY id DESC LIMIT 1").fetchone()
            if row:
                review, reviewed_at = review + text(row[0]), text(row[1])
    if review and not current_review:
        review = "Historical review from the previous coaching policy; retained for reference, not current guidance.\n\n" + review
    return activities, review, reviewed_at

def build_overview(conn, plan, today, profile=None):
    """Actual running windows and explicit planned quantities; no fitness scoring."""
    today=date.fromisoformat(today) if isinstance(today,str) else today
    from planning_engine import is_run
    columns={row[1] for row in conn.execute("PRAGMA table_info(activities)")}
    fields=[name if name in columns else "NULL AS "+name for name in ("start_date","type","distance_m","elevation_gain_m")]
    records=[]
    if {"start_date","type"}.issubset(columns):
        for started,kind,distance,ascent in conn.execute("SELECT "+",".join(fields)+" FROM activities WHERE date(start_date) BETWEEN ? AND ?",((today-timedelta(days=55)).isoformat(),today.isoformat())):
            if is_run(kind):records.append((str(started)[:10],distance,ascent))
    windows={}
    for days in (28,56):
        rows=[row for row in records if row[0]>=(today-timedelta(days=days-1)).isoformat()]
        distances=[r[1]/1000 for r in rows if isinstance(r[1],(int,float)) and math.isfinite(r[1]) and r[1]>=0]
        ascents=[r[2] for r in rows if isinstance(r[2],(int,float)) and math.isfinite(r[2]) and r[2]>=0]
        windows[str(days)]={"runs":len(rows),"distance_km":round(sum(distances),2) if distances else None,"distance_coverage":len(distances),"ascent_m":round(sum(ascents)) if ascents else None,"ascent_coverage":len(ascents),"longest_km":round(max(distances),2) if distances else None,"start":(today-timedelta(days=days-1)).isoformat(),"end":today.isoformat()}
    planned={}
    for session in plan.get("sessions",[]):
        day=date.fromisoformat(session['date']);week=(day-timedelta(days=day.weekday())).isoformat()
        item=planned.setdefault(week,{"known_km":0.0,"known_sessions":0,"unknown_sessions":0})
        kind=session['workout_type']
        if re.search(r'cross.?train|strength|mobility|walk|cycl|swim|yoga|row|\brest\b|\boff\b',kind,re.I) and not re.search(r'run|race',kind,re.I):continue
        if not re.search(r'run|race|tempo|interval|hill|trail|threshold|speed|fartlek|progression|strides|jog',kind,re.I):
            item['unknown_sessions']+=1;continue
        quantities=re.findall(r'(\d+(?:\.\d+)?)\s*km\b',session['amount'],re.I)
        ambiguous=re.search(r'\d\s*[-\u2013x\u00d7]\s*\d+(?:\.\d+)?\s*km',session['amount'],re.I)
        ambiguous=ambiguous or re.search(r'\bor\b',session['amount'],re.I)
        if len(quantities)==1 and not ambiguous:
            item['known_km']+=float(quantities[0]);item['known_sessions']+=1
        else:item['unknown_sessions']+=1
    for item in planned.values():item['known_km']=round(item['known_km'],2)
    goals=(profile or {}).get('goals',plan.get('goals',[]));countdown=None
    for goal in goals:
        if goal.get('priority')!='A-Race':continue
        raw=goal.get('date','');event_date=None
        for fmt in ('%Y-%m-%d','%B %d, %Y'):
            try:event_date=datetime.strptime(raw,fmt).date();break
            except ValueError:continue
        if event_date:countdown={'event':goal.get('event','A-Race'),'date':event_date.isoformat(),'days':(event_date-today).days}
    upcoming=sorted([session for session in plan.get('sessions',[]) if session['date']>=today.isoformat() and not re.search(r'\brest\b|\boff\b',session['workout_type'],re.I)],key=lambda item:(item['date'],item['id']))
    return {'windows':windows,'planned_weeks':planned,'countdown':countdown,'next_session':upcoming[0] if upcoming else None}


def build_dashboard(db_path=None, plan_file=None, output_html=None, today=None, status_file=None):
    db_path, plan_file = Path(db_path or DB_PATH), Path(plan_file or PLAN_FILE)
    output_html = Path(output_html or OUTPUT_HTML)
    today = today or date.today()
    markdown = plan_file.read_text(encoding="utf-8") if plan_file.exists() else "No training plan available."
    canonical = None
    canonical_path = plan_file.parent / "training_plan.json"
    profile_path = plan_file.parent / "athlete.md"
    if (plan_file.parent / ".plan-commit.json").exists():
        raise RuntimeError("Plan update recovery is pending; previous dashboard retained. Run the daily coach to recover.")
    if canonical_path.exists():
        from planning_engine import parse_profile, validate_plan, render_plan
        profile = parse_profile(profile_path.read_text(encoding="utf-8-sig"))
        canonical = json.loads(canonical_path.read_text(encoding="utf-8-sig"))
        validate_plan(canonical, profile)
        markdown = render_plan(canonical)
    sessions = parse_sessions(markdown)
    dates = [s["date"] for s in sessions]
    start = min(min(dates), (today - timedelta(days=55)).isoformat()) if dates else (today - timedelta(days=55)).isoformat()
    end = max(max(dates),today.isoformat()) if dates else today.isoformat()
    activities, review, reviewed_at = read_database(db_path, start, end)
    status_path = Path(status_file) if status_file else db_path.parent / "pipeline_status.json"
    status = {}
    if status_path.exists():
        try:
            status = json.loads(status_path.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            status = {"errors": ["Pipeline status unavailable"]}
    from dashboard_template import render_layout
    def safe_json(value):
        return json.dumps(value,ensure_ascii=True,allow_nan=False).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    assets_dir=SCRIPT_DIR/"dashboard_assets"
    assets={name:(assets_dir/name).read_text(encoding="utf-8") for name in ("tailwind.js","chart.js","marked.js","purify.js","fontawesome.css","workout-fit.js","workout-ui.js")}
    for name,value in list(assets.items()):
        assets[name]=value.replace("</script", "<\\/script")
    css=assets["fontawesome.css"]
    for font in ("fa-solid-900","fa-regular-400","fa-brands-400"):
        data="data:font/woff2;base64,"+base64.b64encode((assets_dir/(font+".woff2")).read_bytes()).decode("ascii")
        css=css.replace("../webfonts/"+font+".woff2",data)
        # WOFF2 is bundled; remove unused legacy font-format alternatives.
        css=__import__('re').sub(r',url\(\.\./webfonts/'+font+r'\.ttf\) format\("truetype"\)',"",css)
    assets["fontawesome.css"]=css
    exported={}
    for d,records in activities.items():
        exported[d]=[]
        for item in records:
            distance=item.get("distance_m")
            exported[d].append({"id":item.get("id"),"name":text(item.get("name")),"type":text(item.get("type")),"actual_km":round(distance/1000,2) if isinstance(distance,(int,float)) and math.isfinite(distance) else "unavailable","comment":text(item.get("ai_feedback")),"feedback_current":bool(item.get("feedback_current"))})
    monday=today-timedelta(days=today.weekday())
    weeks=[monday-timedelta(weeks=i) for i in reversed(range(8))]
    totals={w.isoformat():[0.0,0.0,0] for w in weeks}
    if db_path.exists():
        with closing(sqlite3.connect(db_path.resolve().as_uri()+"?mode=ro",uri=True)) as conn:
            columns={row[1] for row in conn.execute("PRAGMA table_info(activities)")}
            if {"start_date","distance_m","type"}.issubset(columns):
                load="training_load" if "training_load" in columns else "NULL"
                rows=conn.execute("SELECT date(start_date),distance_m,"+load+",type FROM activities WHERE date(start_date) BETWEEN ? AND ?",(weeks[0].isoformat(),today.isoformat()))
                from planning_engine import is_run
                for d,distance,score,kind in rows:
                    if not is_run(kind):continue
                    day=date.fromisoformat(d);key=(day-timedelta(days=day.weekday())).isoformat()
                    if key in totals:
                        if isinstance(distance,(int,float)) and math.isfinite(distance):totals[key][0]+=distance/1000
                        if isinstance(score,(int,float)) and math.isfinite(score):
                            totals[key][1]+=score;totals[key][2]+=1
    overview=None
    if db_path.exists():
        with closing(sqlite3.connect(db_path.resolve().as_uri()+"?mode=ro",uri=True)) as conn:
            overview=build_overview(conn,canonical or {"sessions":[],"goals":[]},today)
    from dashboard_panels import build_panels,coach_payload,duration,pace
    coach={}
    if db_path.exists():
        with closing(sqlite3.connect(db_path.resolve().as_uri()+"?mode=ro",uri=True)) as conn:coach=coach_payload(conn)
    panels=build_panels(canonical or {'sessions':[]},activities,coach,today)
    planned_chart=[]
    for week in weeks:
        entry=(overview or {}).get("planned_weeks",{}).get(week.isoformat())
        planned_chart.append(entry['known_km'] if entry and (entry['known_sessions'] or not entry['unknown_sessions']) else None)
    generated_at=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    freshness='<div class="bg-slate-800/60 border border-slate-700 rounded-xl p-4 text-xs text-slate-400">Latest saved review (UTC): '+esc(reviewed_at or "Unavailable")+' &middot; Latest successful sync (UTC): '+esc(status.get("sync_success_at") or "Unavailable")
    if canonical:
        freshness+=' &middot; Plan revision '+esc(canonical.get("revision"))
        if canonical.get("requires_coach_reconciliation"):freshness+='<p class="text-amber-300 mt-2">Imported schedule awaiting AI coach reconciliation.</p>'
        for goal in canonical.get("goals",[]):
            freshness+='<p class="mt-2 text-sky-300">'+esc(goal.get("priority"))+': '+esc(goal.get("event"))+' &middot; '+esc(goal.get("date"))+' &middot; '+esc(goal.get("target_time"))+'</p>'
    if overview:
        countdown=overview['countdown'];upcoming=overview['next_session']
        freshness+='<div class="grid grid-cols-1 md:grid-cols-2 gap-3 mt-3">'
        if countdown:freshness+='<p><strong class="text-sky-300">'+esc(countdown['days'])+' days to A-race</strong> &middot; '+esc(countdown['date'])+'</p>'
        if upcoming:freshness+='<p><strong class="text-sky-300">Next scheduled:</strong> '+esc(upcoming['date'])+' &middot; '+esc(upcoming['workout_type'])+' &middot; '+esc(upcoming['amount'])+'</p>'
        freshness+='</div>'
    freshness+=''.join('<p class="text-amber-300 mt-2">'+esc(message)+'</p>' for message in status.get("errors",[]))+'</div>'
    if overview:
        freshness+='<div class="flex gap-2 mt-3"><button onclick="setDataWindow(28)" class="px-3 py-1 rounded bg-slate-800 border border-slate-700 text-sky-300 text-xs">Last 4 weeks</button><button onclick="setDataWindow(56)" class="px-3 py-1 rounded bg-slate-800 border border-slate-700 text-sky-300 text-xs">Last 8 weeks</button></div>'
        for days,stats in overview['windows'].items():
            def shown(value,unit):return esc(value)+' '+unit if value is not None else 'Unavailable'
            freshness+='<div data-running-window="'+days+'" class="'+('hidden ' if days=='28' else '')+'grid grid-cols-3 gap-3 text-xs mt-3 bg-slate-800/60 border border-slate-700 rounded-xl p-4"><p class="text-slate-300"><span class="text-slate-400">Running distance</span><br><strong class="text-sky-300">'+shown(stats['distance_km'],'km')+'</strong></p><p class="text-slate-300"><span class="text-slate-400">Recorded ascent</span><br><strong class="text-sky-300">'+shown(stats['ascent_m'],'m')+'</strong></p><p class="text-slate-300"><span class="text-slate-400">Longest run</span><br><strong class="text-sky-300">'+shown(stats['longest_km'],'km')+'</strong></p><p class="col-span-3 text-slate-400">'+esc(int(days)//7)+' weeks: '+esc(stats['runs'])+' recorded runs &middot; distance known for '+esc(stats['distance_coverage'])+'/'+esc(stats['runs'])+' &middot; ascent known for '+esc(stats['ascent_coverage'])+'/'+esc(stats['runs'])+'. No duration-to-distance estimates.</p></div>'
    planned_dates={session["date"] for session in sessions}
    other=''
    unmatched=[(d,item) for d,items in exported.items() if d not in planned_dates for item in items]
    if unmatched:
        other='<details class="bg-slate-800/60 border border-slate-700 rounded-xl p-4"><summary class="text-sky-300 cursor-pointer">Other recent recorded activities</summary><ul class="text-sm text-slate-300 mt-3 space-y-2">'+''.join('<li>'+esc(d)+' &middot; '+esc(item["name"])+' &middot; '+esc(item["type"])+' &middot; '+esc(item["actual_km"])+' km</li>' for d,item in unmatched)+'</ul></details>'
    ids=defaultdict(list)
    if canonical:
        for session in sorted(canonical["sessions"],key=lambda value:(value["date"],value["id"])):
            ids[session["date"]+'|'+session["workout_type"]].append(session["id"])
    partial=sum(item['unknown_sessions'] for key,item in (overview or {}).get('planned_weeks',{}).items() if key in {week.isoformat() for week in weeks})
    other='<p class="text-xs text-slate-400">Weekly chart: actual running distance versus known planned running km. '+esc(partial)+' time-only, alternative or unclassified planned sessions excluded; no completion inference.</p>'+other
    document=render_layout(assets=assets,weeks_json=safe_json(['w/c '+w.strftime('%d/%m') for w in weeks]),km_json=safe_json([round(totals[w.isoformat()][0],1) for w in weeks]),load_json=safe_json([round(totals[w.isoformat()][1],1) if totals[w.isoformat()][2] else None for w in weeks]),raw_markdown_json=safe_json(markdown),activities_json=safe_json(exported),macro_review_json=safe_json(review),last_updated_str=esc(generated_at),today_json=safe_json(today.isoformat()),freshness_html=freshness,other_activities_html=other,session_ids_json=safe_json(ids),planned_json=safe_json(planned_chart),agenda_html=panels["agenda"],focus_html=panels["focus"],assessment_html=panels["assessment"],history_html=panels["history"],feedback_html=panels["feedback"],workout_sessions_json=safe_json({item["id"]:item for item in (canonical["sessions"] if canonical else [])}))
    # Same-filesystem replacement prevents readers seeing partially written HTML.
    output_html.parent.mkdir(parents=True, exist_ok=True)
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=output_html.parent, suffix=".tmp", delete=False) as handle:
            temp_path = handle.name
            handle.write(document)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, output_html)
    finally:
        if temp_path and os.path.exists(temp_path):
            os.unlink(temp_path)
    print(f"Dashboard saved: {output_html}")
    return output_html

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path)
    parser.add_argument("--plan", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    build_dashboard(db_path=args.db, plan_file=args.plan, output_html=args.output)
