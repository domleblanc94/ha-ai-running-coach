"""Local first-run setup and synthetic offline demonstration. Never calls an API."""
import argparse
from contextlib import closing
from datetime import date, timedelta
import importlib.util
import os
import json
from pathlib import Path
import sqlite3
import sys
from database_manager import ensure_schema
from planning_engine import parse_profile, render_plan
from model_provider import DEFAULT_MODELS,model_advisories

ROOT=Path(__file__).resolve().parent

def initialize(root, provider="gemini", model=None, source="offline"):
    root=Path(root);root.mkdir(parents=True,exist_ok=True)
    if provider not in DEFAULT_MODELS or source not in {"offline","garmin"}:raise ValueError("Unknown provider or activity source")
    model=model or DEFAULT_MODELS[provider]
    if not model:raise ValueError("Choose an exact model identifier with --model for this provider")
    if any(c in model for c in "\r\n") or not model.strip():raise ValueError("Invalid model identifier")
    env=root/".env"
    if not env.exists():
        text=(ROOT/".env.example").read_text(encoding="utf-8")
        replacements={'COACH_PROVIDER':provider,'COACH_MODELS':model,'COACH_ACTIVITY_SOURCE':source}
        lines=[]
        for line in text.splitlines():
            key=line.partition('=')[0]
            lines.append(key+'='+replacements[key] if key in replacements else line)
        text='\n'.join(lines)+'\n'
        env.write_text(text,encoding="utf-8")
        try:env.chmod(0o600)
        except OSError:pass
    profile=root/"athlete.md"
    if not profile.exists():profile.write_text((ROOT/"examples/athlete.template.md").read_text(encoding="utf-8"),encoding="utf-8")
    with closing(sqlite3.connect(root/"ai_coach_master.sqlite")) as conn,conn:ensure_schema(conn)
    (root/"ai_coach_data").mkdir(exist_ok=True)
    return root

def demo(root):
    """Only writes a dedicated folder; examples are illustrative, not training advice."""
    root=Path(root)
    if root.exists() and any(root.iterdir()):raise ValueError("Demo output must be an empty/new directory")
    initialize(root)
    today=date.today()
    text="""# SYNTHETIC DEMO - illustrative data only
**Athlete Name:** Fictional runner
**Zone 1 (Recovery):** < 120 bpm
**Zone 2 (Endurance):** 120 - 140 bpm
**Zone 3 (Tempo):** 141 - 155 bpm
**Zone 4 (Hard):** 156 - 170 bpm
**Zone 5 (Max):** >= 171 bpm
**Active Injuries:** Unknown
"""
    text += "\n**A-Race:** Fictional community 10km\n**Date:** "+(today+timedelta(days=56)).isoformat()+"\n**Target Time:** Enjoy participating\n"
    (root/"athlete.md").write_text(text,encoding="utf-8")
    profile=parse_profile(text)
    sessions=[{"id":"demo-"+str(i),"date":(today+timedelta(days=i)).isoformat(),"workout_type":"DEMO example run" if i%2==0 else "Rest","amount":"4 km" if i%2==0 else "-","target":"Illustrative session only; replace with an individualized coach plan."} for i in range(7)]
    plan={"schema_version":1,"revision":1,"profile_hash":profile["source_hash"],"goals":profile["goals"],"sessions":sessions}
    (root/"training_plan.json").write_text(json.dumps(plan,indent=2),encoding="utf-8")
    (root/"training_plan.md").write_text(render_plan(plan),encoding="utf-8")
    with closing(sqlite3.connect(root/"ai_coach_master.sqlite")) as conn,conn:
        conn.execute("INSERT INTO activities(id,name,type,start_date,distance_m,moving_time_s,elevation_gain_m,source,metrics_source) VALUES(?,?,?,?,?,?,?,?,?)",(1,"Synthetic example run","Run",(today-timedelta(days=1)).isoformat()+"T08:00:00Z",4000,1800,35,"local:demo","local:demo"))
    from generate_dashboard import build_dashboard
    build_dashboard(root/"ai_coach_master.sqlite",root/"training_plan.md",root/"dashboard.html")
    page=root/"dashboard.html"
    html=page.read_text(encoding="utf-8")
    marker='<body class="bg-slate-900 text-slate-100 font-sans min-h-screen p-4 md:p-8">'
    html=html.replace(marker,marker+'<div role="note" style="background:#854d0e;color:white;padding:16px;text-align:center;font-weight:bold">SYNTHETIC DEMO - fictional activities and illustrative sessions, not an individualized training plan</div>',1)
    page.write_text(html,encoding="utf-8")
    return page

def doctor(root,offline=False):
    root=Path(root);errors=[]
    try:
        profile=parse_profile((root/"athlete.md").read_text(encoding="utf-8"))
        print("Profile: valid; HR zones "+("configured" if profile["zones"] else "explicitly unknown")+"; "+str(len(profile["goals"]))+" event goal(s)")
    except (OSError,ValueError) as exc:errors.append("Edit athlete.md: "+str(exc))
    try:
        with closing(sqlite3.connect((root/"ai_coach_master.sqlite").resolve().as_uri()+"?mode=ro",uri=True)) as conn:
            columns={row[1] for row in conn.execute("pragma table_info(activities)")}
            if not {"id","start_date","distance_m"}.issubset(columns):errors.append("Database schema missing; run setup_coach.py")
            from source_policy import assert_coaching_sources
            try:assert_coaching_sources(conn)
            except ValueError:errors.append("Database contains legacy/unidentified sources; use --fresh-garmin-workspace instead of relabeling old history")
    except sqlite3.Error:errors.append("Database missing/unreadable; run setup_coach.py")
    try:
        from dotenv import dotenv_values
        config={**dotenv_values(root/".env"),**os.environ}
    except ImportError:
        config=dict(os.environ);errors.append("Install requirements.txt to read .env configuration")
    provider=config.get("COACH_PROVIDER","gemini")
    if provider not in DEFAULT_MODELS:errors.append("Set COACH_PROVIDER to gemini/openai/anthropic")
    model=config.get("COACH_MODELS") or DEFAULT_MODELS.get(provider)
    if not model:errors.append("Set COACH_MODELS for the selected provider")
    print("Provider configuration: "+provider+"; model "+("set" if model else "missing"))
    for advisory in model_advisories(provider,[item.strip() for item in (model or '').split(',') if item.strip()]):
        print('WARNING: '+advisory)
    keyless=False
    saved_environment=dict(os.environ)
    try:
        os.environ.update({key:str(value) for key,value in config.items() if value is not None})
        from model_provider import configured_base_url,configured_models,DEFAULT_URLS
        endpoint=configured_base_url(provider)
        configured_models()
        keyless=provider=="openai" and endpoint!=DEFAULT_URLS["openai"] and config.get("COACH_ALLOW_KEYLESS","").lower()=="true"
    except (ValueError,KeyError) as exc:
        errors.append("Provider configuration: "+str(exc))
    finally:
        os.environ.clear();os.environ.update(saved_environment)
    if not offline and not keyless and not config.get({"gemini":"GEMINI_API_KEY","openai":"OPENAI_API_KEY","anthropic":"ANTHROPIC_API_KEY"}.get(provider,"")):
        errors.append("Add the selected provider API key to .env before live coaching (offline dry runs need no key)")
    source=config.get("COACH_ACTIVITY_SOURCE","offline")
    if source not in {"offline","garmin"}:errors.append("Set a supported COACH_ACTIVITY_SOURCE")
    if source == "garmin":
        if not config.get("GARMIN_PERSONAL_API_KEY"):
            errors.append("Official Garmin source selected: add GARMIN_PERSONAL_API_KEY from your own Garmin Personal API access")
        if config.get("GARMIN_PERSONAL_REGION","global") not in {"global","china"}:
            errors.append("GARMIN_PERSONAL_REGION must be global or china")
        for key,default in (("GARMIN_HTTP_TIMEOUT_SECONDS","30"),("GARMIN_MAX_REQUESTS_PER_RUN","100"),("GARMIN_BACKFILL_DAYS","56")):
            try:int(config.get(key,default))
            except (TypeError,ValueError):errors.append(key+" must be an integer")
    if config.get("COACH_PUBLISH_ENABLED","").lower() in {"true","1","yes","on"}:
        try:
            from health_status import transport_from_script
            transport_from_script(root/"upload.sh",root)
        except (OSError,ValueError):errors.append("Publishing enabled: configure a supported private upload.sh or set COACH_PUBLISH_ENABLED=false")
    for name in ("requests","dotenv"):
        if importlib.util.find_spec(name) is None:errors.append("Missing dependency: "+name+"; install requirements.txt")
    for message in errors:print("ACTION: "+message)
    print("No API/credential validation or network request performed.")
    return 1 if errors else 0

def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--provider",choices=DEFAULT_MODELS)
    p.add_argument("--model",help="Explicit provider model for a new configuration; existing .env is preserved (Gemini default: "+DEFAULT_MODELS['gemini']+")")
    p.add_argument("--source",choices=("offline","garmin"))
    p.add_argument("--non-interactive",action="store_true")
    p.add_argument("--demo",metavar="EMPTY_DIRECTORY")
    p.add_argument("--fresh-garmin-workspace",metavar="NEW_DIRECTORY",help="Copy only public source into an empty separate directory and initialize a fresh official-Garmin database")
    p.add_argument("--doctor",action="store_true",help="Check local setup without API calls or credential output")
    p.add_argument("--offline",action="store_true",help="With --doctor, check offline readiness without requiring a model key")
    a=p.parse_args(argv)
    if a.fresh_garmin_workspace:
        destination=Path(a.fresh_garmin_workspace).resolve()
        if destination==ROOT or ROOT in destination.parents:raise ValueError("Choose a fresh directory outside the current checkout")
        from prepare_release import build_release
        build_release(ROOT,destination,create_archive=False)
        initialize(destination,a.provider or "gemini",a.model,"garmin")
        print("Fresh official-Garmin workspace created at "+str(destination)+". Existing workspace is untouched. Re-enter approved profile facts and credentials; no activity history, plan, feedback or secrets were copied.")
        return 0
    if a.doctor:return doctor(ROOT,a.offline)
    if a.demo:print("Synthetic demo: "+str(demo(a.demo).resolve()));return 0
    provider=a.provider
    source=a.source
    if not a.non_interactive:
        provider=provider or input("AI provider [gemini/openai/anthropic] (gemini): ").strip() or "gemini"
        source=source or input("Activity source [offline/garmin] (offline): ").strip() or "offline"
    model=a.model
    if not a.non_interactive and (provider or "gemini") != "gemini" and not model:
        model=input("Model identifier available to your selected provider/account: ").strip()
    initialize(ROOT,provider or "gemini",model,source or "offline")
    print("Setup complete. Existing files were preserved. Edit athlete.md with your own zones/goals and .env with your chosen provider credentials. No API calls were made. Then run ai_coach.py --new-plan with your request.")
    return 0

if __name__=="__main__":
    try:sys.exit(main())
    except (ValueError,OSError,sqlite3.Error) as exc:print(str(exc),file=sys.stderr);sys.exit(1)
