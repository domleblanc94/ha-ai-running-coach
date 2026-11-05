"""Run an existing upload script only after a recent successful complete pipeline."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys
from cron_daily_update import pipeline_lock
from health_status import update_health, upload_health, stamp, publishing_enabled

def ready(project, now=None, max_age_hours=6):
    project=Path(project)
    now=now or datetime.now(timezone.utc)
    if (project/".plan-commit.json").exists():
        return False,"Plan recovery pending"
    try:
        status=json.loads((project/"pipeline_status.json").read_text(encoding="utf-8"))
        attempted=datetime.fromisoformat(status["attempted_at"])
        if attempted.tzinfo is None:
            return False,"Pipeline timestamp has no timezone"
        age=(now-attempted).total_seconds()
        if status.get("errors") or not 0 <= age <= max_age_hours*3600:
            return False,"Pipeline failed or success is stale"
        if status.get("sync_success_at")!=status["attempted_at"]:
            return False,"No successful sync in this pipeline"
        html=project/"dashboard.html"
        rendered=html.stat().st_mtime
        if not html.stat().st_size or rendered < attempted.timestamp():
            return False,"Dashboard predates this pipeline"
        for name in ("athlete.md","training_plan.json","ai_coach_master.sqlite"):
            source=project/name
            if not source.exists() or source.stat().st_mtime>rendered:
                return False,"Dashboard predates saved source state"
        return True,"Recent successful pipeline and dashboard verified"
    except (OSError,ValueError,KeyError,TypeError) as exc:
        return False,"Pipeline state unavailable: "+type(exc).__name__

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project",type=Path,default=Path(__file__).resolve().parent)
    parser.add_argument("--upload-script",type=Path)
    args=parser.parse_args()
    try:
        from dotenv import load_dotenv
        load_dotenv(args.project/".env",override=False)
    except ImportError:
        pass
    if args.upload_script and not publishing_enabled(args.project):
        print("Publishing disabled; no upload attempted")
        return 0
    try:
        with pipeline_lock(args.project/".pipeline.lock"):
            allowed,message=ready(args.project)
            print(message)
            if not allowed:
                if args.upload_script:
                    update_health(args.project,phase="publish_blocked",last_publish_attempt_at=stamp(),publish_error="Dashboard publication blocked: "+message)
                    upload_health(args.project,args.upload_script)
                return 1
            if args.upload_script:
                update_health(args.project,phase="publishing",last_publish_attempt_at=stamp(),publish_error=None)
                try:
                    result=subprocess.run([str(args.upload_script.resolve())],cwd=args.project,timeout=120)
                    code=result.returncode
                except (OSError,subprocess.SubprocessError):code=1
                if code==0:
                    update_health(args.project,phase="ready",last_publish_success_at=stamp(),publish_error=None)
                else:
                    update_health(args.project,phase="publish_failed",publish_error="Dashboard upload failed; Home Assistant may show the previous plan")
                health_delivered=upload_health(args.project,args.upload_script)
                return code if code else 0 if health_delivered else 1
            return 0
    except RuntimeError as exc:
        print(str(exc));return 1

if __name__=="__main__":
    sys.exit(main())
