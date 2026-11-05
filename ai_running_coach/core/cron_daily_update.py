"""Single-run daily pipeline. Publish only when this command exits zero."""
import argparse
import sqlite3
import re
import contextlib
import json
import logging
import math
import os
from pathlib import Path
import subprocess
import sys
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from health_status import update_health, upload_health, stamp, publishing_enabled
from source_policy import activity_source_mode

SCRIPT_DIR = Path(__file__).resolve().parent

@contextlib.contextmanager
def pipeline_lock(path):
    # OS advisory locks release automatically even after a killed process.
    handle = open(path, "a+b")
    try:
        if os.name == "nt":
            import msvcrt
            handle.seek(0)
            handle.write(b"0")
            handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        raise RuntimeError("Another daily pipeline is running")
    try:
        yield
    finally:
        handle.close()

def run_script(name, timeout=600, args=()):
    env = dict(os.environ, PYTHONIOENCODING="utf-8",COACH_PIPELINE_CHILD="1")
    try:
        result = subprocess.run([sys.executable, str(SCRIPT_DIR / name), *args],
            cwd=SCRIPT_DIR, env=env, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout)
        if result.stdout.strip():
            logging.info("%s: %s", name, result.stdout.strip())
        if result.returncode not in (0, 2):
            logging.error("%s failed: %s", name, result.stderr.strip())
        return result.returncode
    except (OSError, subprocess.TimeoutExpired) as exc:
        logging.error("%s: %s", name, exc)
        return 1

def write_status(status):
    target = SCRIPT_DIR / "pipeline_status.json"
    temporary = target.with_suffix(".tmp")
    temporary.write_text(json.dumps(status), encoding="utf-8")
    os.replace(temporary, target)


def read_garmin_auth(project):
    """Read only fixed, public-safe codes/messages; never private token metadata."""
    from garmin_sync import AUTH_MESSAGES
    try:
        raw=json.loads((Path(project)/'garmin_auth_status.json').read_text(encoding='utf-8'))
        if not isinstance(raw,dict) or raw.get('code') not in AUTH_MESSAGES:return None
        code=raw['code'];state=raw.get('state')
        if state not in {'ok','unknown','temporary_error','action_required'}:return None
        message,action=AUTH_MESSAGES[code]
        if state=='action_required' and not message.startswith('INPUT REQUIRED:'):message='INPUT REQUIRED: '+message
        checked=datetime.fromisoformat(raw['checked_at'])
        if checked.tzinfo is None:return None
        result={'schema_version':1,'state':state,'code':code,'message':message,'action':action,'checked_at':checked.isoformat()}
        if type(raw.get('http_status')) is int:result['http_status']=raw['http_status']
        if type(raw.get('retry_after')) in (float,int) and math.isfinite(raw['retry_after']):result['retry_after']=raw['retry_after']
        return result
    except (OSError,ValueError,TypeError,KeyError):return None

def coach_failure_message(project, attempted_at):
    """Report recognized failures from this run without exposing provider content."""
    try:
        uri = (Path(project) / "ai_coach_master.sqlite").resolve().as_uri() + "?mode=ro"
        with contextlib.closing(sqlite3.connect(uri, uri=True)) as conn:
            request = conn.execute(
                "SELECT status,error,started_at FROM coach_api_requests "
                "WHERE request_kind IN ('daily','operator_recovery') ORDER BY id DESC LIMIT 1"
            ).fetchone()
        if not request or datetime.fromisoformat(request[2]) < datetime.fromisoformat(attempted_at):
            return None
        status, error, _ = request
        if status == "invalid" and error == "Model output was truncated by its token budget; last-good plan retained.":
            return "AI response exceeded its output limit; last published dashboard retained"
        if status == "failed" and re.search(r"(?:^|HTTP )(500|502|503|504|529)\b", error or ""):
            return "AI provider remained unavailable; last successful review retained"
    except (OSError, sqlite3.Error, TypeError, ValueError):
        return None
    return None


def run_pipeline(coach_args=()):
    status = {"attempted_at": datetime.now(timezone.utc).isoformat(), "errors": []}
    previous = SCRIPT_DIR / "pipeline_status.json"
    if previous.exists():
        try:
            prior = json.loads(previous.read_text(encoding="utf-8"))
            if prior.get("sync_success_at"):
                status["sync_success_at"] = prior["sync_success_at"]
        except (ValueError, OSError):
            pass
    update_health(SCRIPT_DIR,phase="syncing",last_pipeline_attempt_at=status["attempted_at"],pipeline_errors=[])
    source = activity_source_mode()
    sync = 2 if source == "offline" else run_script("garmin_sync.py")
    if source=='garmin':
        auth=read_garmin_auth(SCRIPT_DIR)
        if auth:
            status['garmin_auth']=auth
            update_health(SCRIPT_DIR,garmin_auth=auth)
    else:
        # A saved Garmin rejection is irrelevant when this workspace uses local data.
        update_health(SCRIPT_DIR,garmin_auth=None)
    if sync not in (0, 2):
        status["errors"].append("Activity sync failed; showing saved data")
    else:
        status["sync_success_at"] = status["attempted_at"]
        update_health(SCRIPT_DIR,last_sync_success_at=stamp())
    # Always process pending imports, including after an earlier interrupted run.
    update_health(SCRIPT_DIR,phase="processing")
    database = run_script("database_manager.py")
    if database != 0:
        status["errors"].append("Activity processing failed")
    # Core owns request fingerprints, retry eligibility and the API attempt budget.
    # One invocation only: no outer model-request retry multiplier.
    if database == 0 and sync in (0, 2):
        update_health(SCRIPT_DIR,phase="coaching")
        if run_script("ai_coach.py", args=("--attempts-this-run", "1", *coach_args)) != 0:
            status["errors"].append("Coach review pending; previous review retained")
        else:
            update_health(SCRIPT_DIR,last_coach_success_at=stamp())
    write_status(status)
    # Render saved state even when sync or the AI provider is unavailable.
    update_health(SCRIPT_DIR,phase="rendering")
    if run_script("generate_dashboard.py") != 0:
        status["errors"].append("Dashboard generation failed; previous HTML retained")
        write_status(status)
    else:
        update_health(SCRIPT_DIR,last_render_success_at=stamp())
    revision=None
    try:revision=json.loads((SCRIPT_DIR/"training_plan.json").read_text(encoding="utf-8"))["revision"]
    except (OSError,ValueError,KeyError):pass
    health_errors = list(status["errors"])
    if health_errors == ["Coach review pending; previous review retained"]:
        now = datetime.now(timezone.utc)
        scheduled = now.replace(hour=4, minute=15, second=0, microsecond=0)
        if now < scheduled and deferred_retry_eligible(SCRIPT_DIR, scheduled)[0]:
            health_errors = ["AI provider temporarily unavailable; 04:15 UTC retry scheduled; previous review retained"]
        else:
            message = coach_failure_message(SCRIPT_DIR, status["attempted_at"])
            if message:
                health_errors = [message]
    changes={"phase":"failed" if status["errors"] else "awaiting_publish","pipeline_errors":health_errors,"plan_revision":revision}
    if not status["errors"]:changes["last_pipeline_success_at"]=stamp()
    update_health(SCRIPT_DIR,**changes)
    # This channel bypasses the dashboard success gate so the last-good page can report failures.
    if not upload_health(SCRIPT_DIR):
        logging.error("Live status could not be delivered; last-good dashboard retained")
        status["errors"].append("Live status delivery failed")
    write_status(status)
    return 1 if status["errors"] else 0

def deferred_retry_eligible(project, now=None):
    """Read-only gate: spend the remaining call later, only for transient server failures."""
    now = now or datetime.now(timezone.utc)
    project = Path(project)
    try:
        status = json.loads((project / "pipeline_status.json").read_text(encoding="utf-8"))
        if status.get("errors") != ["Coach review pending; previous review retained"]:
            return False, "No isolated pending coach failure"
        if datetime.fromisoformat(status["attempted_at"]).date() != now.date():
            return False, "No pending run from today"
        conn = sqlite3.connect((project / "ai_coach_master.sqlite").resolve().as_uri() + "?mode=ro", uri=True)
        try:
            row = conn.execute("SELECT job_fingerprint,status,error,started_at FROM coach_api_requests WHERE request_kind='daily' ORDER BY id DESC LIMIT 1").fetchone()
            count = conn.execute("SELECT count(*) FROM coach_api_requests WHERE request_kind IN ('daily','operator_recovery') AND substr(started_at,1,10)=?", (now.date().isoformat(),)).fetchone()[0]
            job = conn.execute("SELECT status FROM coach_review_jobs WHERE fingerprint=?", (row[0],)).fetchone() if row else None
        finally:
            conn.close()
        if not row or row[1] != "failed" or not job or job[0] != "pending":
            return False, "No transient pending request"
        if not re.search(r"(?:^|HTTP )(500|502|503|504|529)\b", row[2] or ""):
            return False, "Failure is not an eligible temporary server error"
        started = datetime.fromisoformat(row[3])
        if started.tzinfo is None or started.date() != now.date() or (now-started).total_seconds() < 3600:
            return False, "Retry must wait at least one hour on the same UTC day"
        # Core independently enforces configured request budgets under its lock.
        if count >= 2:
            return False, "Daily automatic request allowance spent"
        return True, "Retrying temporary server failure after a cooling-off interval"
    except (OSError, ValueError, KeyError, TypeError, sqlite3.Error):
        return False, "Retry state unavailable; no call made"

def main(argv=()):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--retry-pending", action="store_true")
    parser.add_argument("--operator-extra-attempt", metavar="UTC_DATE")
    parser.add_argument("--operator-reason")
    args = parser.parse_args(argv)
    try:
        from dotenv import load_dotenv
        load_dotenv(SCRIPT_DIR / ".env", override=False)
    except ImportError:
        pass
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
        handlers=[RotatingFileHandler(SCRIPT_DIR / "cron_pipeline.log", maxBytes=2_000_000,
                  backupCount=3, encoding="utf-8"), logging.StreamHandler()])
    try:
        with pipeline_lock(SCRIPT_DIR / ".pipeline.lock"):
            try:
                if args.retry_pending:
                    eligible, reason = deferred_retry_eligible(SCRIPT_DIR)
                    logging.info("Deferred retry: %s", reason)
                    if not eligible:
                        return 0
                if args.retry_pending and args.operator_extra_attempt:
                    raise ValueError("Scheduled retries cannot use an operator exception")
                coach_args = ()
                if args.operator_extra_attempt:
                    if not args.operator_reason:
                        raise ValueError("Operator exception requires an approval reason")
                    coach_args = ("--operator-extra-attempt", args.operator_extra_attempt, "--operator-reason", args.operator_reason)
                return run_pipeline(coach_args)
            except Exception:
                logging.exception("Pipeline aborted unexpectedly")
                update_health(SCRIPT_DIR,phase="failed",pipeline_errors=["Daily workflow stopped unexpectedly; previous plan retained"])
                upload_health(SCRIPT_DIR)
                return 1
    except RuntimeError as exc:
        logging.error("%s", exc)
        return 1

if __name__ == "__main__":
    sys.exit(main(None))
