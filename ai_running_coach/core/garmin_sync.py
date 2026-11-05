"""Bounded incremental sync using the official Garmin Connect Personal API only."""
import argparse
from contextlib import closing
from datetime import date,datetime,timedelta,timezone
import json
import math
import hashlib
import os
from pathlib import Path
import sqlite3
import sys

from database_manager import get_existing_source_ids
from garmin_personal_api import GarminPersonalClient,GarminPersonalError,GarminConfigurationError,private_json_write,BASE_URLS
from source_policy import assert_coaching_sources,activity_source_mode

SCRIPT_DIR=Path(__file__).resolve().parent
DB_PATH=SCRIPT_DIR/'ai_coach_master.sqlite'
DATA_DIR=SCRIPT_DIR/'ai_coach_data'
STATE_FILE=SCRIPT_DIR/'.garmin_personal_sync.json'
TOKEN_FILE=SCRIPT_DIR/'.garmin_personal_token.json'
SOURCE='garmin_personal'
AUTH_STATUS_FILE=SCRIPT_DIR/'garmin_auth_status.json'

AUTH_MESSAGES={
    'missing_key':('INPUT REQUIRED: Add your Garmin Personal API key to the VM configuration.','Set GARMIN_PERSONAL_API_KEY in the VM .env, keep it private, then run the official Garmin sync.'),
    'key_rejected':('INPUT REQUIRED: Garmin rejected the Personal API key exchange.','The key may be expired, revoked, invalid, or restricted. Verify it in Garmin Connect. After fixing access with the same key, run garmin_sync.py --refresh-token and then the normal sync. For a replacement key to the same account, update the VM .env and use --accept-key-rotation; otherwise use a fresh workspace.'),
    'access_token_rejected':('INPUT REQUIRED: Garmin still rejected access after token renewal.','Verify your account and Personal API permissions, then run garmin_sync.py --refresh-token and sync again. Access rejection alone does not establish key expiry.'),
    'permission_denied':('INPUT REQUIRED: Garmin refused permission to activity data.','Verify account/API permissions and key settings, then retry the official sync. This does not establish that the key expired.'),
    'account_binding_required':('INPUT REQUIRED: Confirm the Garmin account/workspace configuration.','Restore the private sync-state file or confirm a replacement key belongs to the same account with --accept-key-rotation. A different account or region requires a fresh workspace.'),
    'rate_limited':('Garmin is rate limited; the previous data and plan are retained.','Wait for the recorded retry time; no key replacement is requested.'),
    'temporary_error':('Garmin could not complete this sync; the previous data and plan are retained.','Retry the scheduled sync later; this is not evidence that the Personal API key expired.'),
    'verification_pending':('Garmin token renewed; activity access still needs verification.','Run the normal official Garmin sync to verify recovery.'),
    'ok':('Garmin activity access verified by a successful sync.','')}


def auth_context():
    key=os.getenv('GARMIN_PERSONAL_API_KEY','').strip()
    base=BASE_URLS.get(os.getenv('GARMIN_PERSONAL_REGION','global'),'invalid-region')
    return hashlib.sha256((base+'\0'+key).encode()).hexdigest() if key else 'missing-key'


def previous_auth_status(path,context):
    try:
        record=json.loads(Path(path).with_name('.garmin_auth_context.json').read_text(encoding='utf-8'))
        if not isinstance(record,dict) or record.get('fingerprint')!=context:return {}
        status=record.get('status',{})
        return status if isinstance(status,dict) else {}
    except (OSError,ValueError,TypeError):return {}


def write_auth_status(path,code,*,state=None,error=None,context=None):
    message,action=AUTH_MESSAGES.get(code,AUTH_MESSAGES['temporary_error'])
    if state is None:state='action_required' if code in {'missing_key','key_rejected','access_token_rejected','permission_denied','account_binding_required'} else 'ok' if code=='ok' else 'temporary_error'
    if state=='action_required' and not message.startswith('INPUT REQUIRED:'):message='INPUT REQUIRED: '+message
    checked=getattr(error,'checked_at',None) or datetime.now(timezone.utc).isoformat()
    value={'schema_version':1,'state':state,'code':code,'message':message,'action':action,'checked_at':checked}
    if isinstance(getattr(error,'status_code',None),int):value['http_status']=error.status_code
    if isinstance(getattr(error,'retry_at',None),(int,float)) and math.isfinite(error.retry_at):value['retry_after']=error.retry_at
    context=context or auth_context()
    private_json_write(Path(path).with_name('.garmin_auth_context.json'),{'fingerprint':context,'status':value})
    private_json_write(path,value)
    return value


def record_auth_error(path,error,context=None):
    code=getattr(error,'code','temporary_error')
    if code not in AUTH_MESSAGES:code='rate_limited' if getattr(error,'status_code',None)==429 else 'temporary_error'
    context=context or auth_context()
    previous=previous_auth_status(path,context)
    if code in {'temporary_error','rate_limited'} and previous.get('state')=='action_required':
        retained_code=previous.get('code') if previous.get('code') in AUTH_MESSAGES else 'verification_pending'
        value=write_auth_status(path,retained_code,state='action_required',context=context)
        value['last_attempt_issue']=code
        if isinstance(getattr(error,'retry_at',None),(int,float)) and math.isfinite(error.retry_at):value['retry_after']=error.retry_at
        private_json_write(Path(path).with_name('.garmin_auth_context.json'),{'fingerprint':context,'status':value})
        private_json_write(path,value)
    else:value=write_auth_status(path,code,error=error,context=context)
    print(value['message']+' '+value['action'],file=sys.stderr)
    return value


def publish_direct_auth_status():
    if os.getenv('COACH_PIPELINE_CHILD')=='1':return
    from cron_daily_update import read_garmin_auth
    from health_status import update_health,upload_health
    if activity_source_mode()!='garmin':
        update_health(SCRIPT_DIR,garmin_auth=None)
        if not upload_health(SCRIPT_DIR):
            print('Warning: local Garmin status was saved, but delivery to Home Assistant failed; its banner may remain stale.',file=sys.stderr)
        return
    auth=read_garmin_auth(SCRIPT_DIR)
    if auth:
        update_health(SCRIPT_DIR,garmin_auth=auth)
        if not upload_health(SCRIPT_DIR):
            print('Warning: local Garmin status was saved, but delivery to Home Assistant failed; its banner may remain stale.',file=sys.stderr)


def load_environment():
    try:
        from dotenv import load_dotenv
        load_dotenv(SCRIPT_DIR/'.env')
    except ImportError:pass


def get_garmin_client():
    load_environment()
    return GarminPersonalClient(os.getenv('GARMIN_PERSONAL_API_KEY',''),TOKEN_FILE,
        region=os.getenv('GARMIN_PERSONAL_REGION','global'),
        timeout=int(os.getenv('GARMIN_HTTP_TIMEOUT_SECONDS','30')),
        max_requests=int(os.getenv('GARMIN_MAX_REQUESTS_PER_RUN','100')))


def number(value):
    if isinstance(value,bool):return None
    try:
        value=float(value)
        return value if math.isfinite(value) and value>=0 else None
    except (TypeError,ValueError):return None


def timestamp(value,utc=False):
    if value is None:return None
    try:
        parsed=datetime.fromisoformat(str(value).replace('Z','+00:00'))
        if utc and parsed.tzinfo is None:parsed=parsed.replace(tzinfo=timezone.utc)
        return parsed.isoformat()
    except (ValueError,TypeError):raise ValueError('Official activity has an invalid timestamp.') from None


def map_garmin_to_payload(activity,details=None,hr_zones=None):
    """Map documented summary/detail fields; unspecified measurements stay unknown."""
    details=details or {}
    summary=details.get('summaryDTO') or {}
    combined=dict(activity);combined.update({key:value for key,value in summary.items() if value is not None})
    identifier=details.get('activityId',activity.get('activityId'))
    if type(identifier) is not int or identifier<=0:raise ValueError('Official activity is missing a positive ID.')
    kind=details.get('activityTypeDTO') or activity.get('activityType') or {}
    if not isinstance(kind,dict):raise ValueError('Official activity type has an invalid shape.')
    description=details.get('description') if details.get('description') is not None else activity.get('description')
    moving=combined.get('movingDuration')
    zones=hr_zones or []
    seconds={str(i):number(activity.get('hrTimeInZone_'+str(i))) for i in range(1,6)}
    known_seconds={zone:value for zone,value in seconds.items() if value is not None}
    metrics={'id':identifier,'name':details.get('activityName',activity.get('activityName')),
        'description':description,'type':kind.get('typeKey'),
        'start_date':timestamp(combined.get('startTimeLocal')),
        'source_start_date_utc':timestamp(combined.get('startTimeGMT'),utc=True),
        'distance_m':number(combined.get('distance')),'moving_time_s':number(moving),
        'timer_duration_s':number(combined.get('duration')),
        'elapsed_time_s':number(combined.get('elapsedDuration')),
        'elevation_gain_m':number(combined.get('elevationGain')),
        'calories':number(combined.get('calories')),
        'avg_heartrate':number(combined.get('averageHR')) or None,'max_heartrate':number(combined.get('maxHR')) or None,
        'avg_watts':number(combined.get('averagePower',activity.get('avgPower'))),
        'weighted_avg_watts':number(combined.get('normalizedPower',activity.get('normPower'))),
        'training_load':number(combined.get('activityTrainingLoad')),
        'perceived_exertion':None,
        'provider_metrics_json':json.dumps({'perceivedWorkoutEffort':summary.get('perceivedWorkoutEffort'),
            'directWorkoutFeel':summary.get('directWorkoutFeel'),'effort_scale':'not specified by documented schema'},allow_nan=False),
        'hr_zones_json':json.dumps(zones,allow_nan=False),
        'hr_zone_seconds_json':json.dumps(known_seconds,allow_nan=False),
        'hr_zone_basis':'reported_zone_seconds_fraction' if len(known_seconds)==5 else 'partial_or_unknown_reported_seconds'}
    if not metrics['start_date']:raise ValueError('Official activity has no local start date.')
    for zone in range(1,6):metrics['hr_zone_'+str(zone)+'_pct']=None
    if len(known_seconds)==5:
        total=sum(known_seconds.values())
        if total:
            for zone,value in known_seconds.items():metrics['hr_zone_'+zone+'_pct']=round(value/total*100,1)
        else:metrics['hr_zone_basis']='no_reported_zone_duration'
    splits=details.get('splitSummaries') or activity.get('splitSummaries')
    if splits:metrics['splits_metric']=splits
    return {'source':SOURCE,'metadata_only':True,'activity_metrics':metrics,
            'athlete_zones':{'official_configurations':zones},'time_series_streams':{}}


def assert_existing_database_safe(path):
    if Path(path).exists():
        with closing(sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro',uri=True)) as conn:assert_coaching_sources(conn)


def fetch_and_save_garmin_data(client=None,*,db_path=None,data_dir=None,state_file=None,
                               today=None,start_date=None,end_date=None,backfill_days=None,accept_key_rotation=False,auth_status_file=None,historical=False):
    load_environment()
    db_path=Path(db_path or DB_PATH);data_dir=Path(data_dir or DATA_DIR);state_file=Path(state_file or STATE_FILE)
    auth_status_file=Path(auth_status_file or state_file.parent/'garmin_auth_status.json')
    today=date.fromisoformat(today) if isinstance(today,str) else (today or date.today())
    assert_existing_database_safe(db_path)
    try:state=json.loads(state_file.read_text(encoding='utf-8'))
    except (OSError,ValueError):state={}
    if not isinstance(state,dict):state={}
    if historical and (not start_date or not end_date):
        raise ValueError('Historical backfill requires explicit --start-date and --end-date.')
    if start_date or end_date or backfill_days is not None:
        begin=date.fromisoformat(start_date) if start_date else today-timedelta(days=(backfill_days or 56)-1)
        end=date.fromisoformat(end_date) if end_date else today
        window={'start_date':begin.isoformat(),'end_date':end.isoformat(),'next_offset':0}
        previous=state.get('active_window') or {}
        if previous.get('start_date')==window['start_date'] and previous.get('end_date')==window['end_date']:
            window=previous
    elif state.get('active_window'):
        window=state['active_window']
        begin=date.fromisoformat(window['start_date']);end=date.fromisoformat(window['end_date'])
    else:
        if state.get('last_completed_date'):
            begin=date.fromisoformat(state['last_completed_date'])-timedelta(days=13)
        else:begin=today-timedelta(days=(backfill_days or int(os.getenv('GARMIN_BACKFILL_DAYS','56')))-1)
        end=today;window={'start_date':begin.isoformat(),'end_date':end.isoformat(),'next_offset':0}
    if begin>end or end>today:raise ValueError('Garmin date window must be ordered and cannot extend into the future.')
    data_dir.mkdir(parents=True,exist_ok=True)
    own_client=client is None
    try:client=client or get_garmin_client()
    except GarminConfigurationError as error:
        record_auth_error(auth_status_file,error)
        raise
    processed=0
    try:
        if not state.get('source_identity') and db_path.exists():
            with closing(sqlite3.connect(db_path.resolve().as_uri()+'?mode=ro',uri=True)) as existing:
                columns={row[1] for row in existing.execute('PRAGMA table_info(activities)')}
                if 'source' in columns and existing.execute("SELECT 1 FROM activities WHERE source='garmin_personal' LIMIT 1").fetchone():
                    raise GarminConfigurationError('The official Garmin dataset has no saved account/key binding. Restore its private sync-state file or use a fresh workspace; a different account must not be adopted silently.')
        identity=client.fingerprint
        base_url=client.base_url
        if state.get('base_url') and state['base_url']!=base_url:
            raise GarminConfigurationError('Garmin region changed; use a fresh workspace rather than mixing regional accounts.')
        if state.get('source_identity') and state['source_identity']!=identity:
            if not accept_key_rotation:raise GarminConfigurationError('Garmin key changed. Use a fresh workspace for a different account, or --accept-key-rotation only after confirming it is the same account.')
            window['next_offset']=0
            state.pop('completed_window',None)
        if historical and state.get('completed_window')=={'start_date':begin.isoformat(),'end_date':end.isoformat()}:
            print('This historical Garmin window is already complete; no requests needed.')
            return 2
        state['source_identity']=identity;state['base_url']=base_url
        state['active_window']=dict(window);private_json_write(state_file,state)
        zones=[]
        if not historical:
            try:zones=client.heart_rate_zones()
            except GarminPersonalError as exc:
                if exc.stage!='data' or exc.status_code not in (403,404):raise
        while True:
            offset=int(window['next_offset'])
            activities=client.activities(window['start_date'],window['end_date'],start=offset,limit=50)
            if not activities:break
            for activity in activities:
                if not isinstance(activity,dict):raise ValueError('Activity search returned an invalid item.')
                identifier=activity.get('activityId')
                details=client.activity(identifier)
                payload=map_garmin_to_payload(activity,details,zones)
                private_json_write(data_dir/('garmin_personal_'+str(identifier)+'_ai_payload.json'),payload)
                processed+=1;window['next_offset']+=1
                state['active_window']=dict(window);private_json_write(state_file,state)
            if len(activities)<50:break
        state.pop('active_window',None)
        state['last_completed_date']=window['end_date'];state['last_window_completed_at']=datetime.now(timezone.utc).isoformat()
        needs_catchup=not (start_date or end_date or backfill_days is not None) and window['end_date']<today.isoformat()
        if historical:
            state['completed_window']={'start_date':window['start_date'],'end_date':window['end_date']}
        elif not needs_catchup:state['last_success_at']=state['last_window_completed_at']
        private_json_write(state_file,state)
        if needs_catchup:
            print('Historical Garmin window completed; newer dates are still pending. Run the normal sync again to catch up before coaching.',file=sys.stderr)
            return 1
        if not historical:write_auth_status(auth_status_file,'ok',context=client.fingerprint)
        print(f'Official Garmin sync completed: {processed} activity payload(s); measurements and provenance retained.')
        return 0 if processed else 2
    except GarminPersonalError as exc:
        if historical and exc.code=='batch_limit':
            print('Historical batch request ceiling reached; saved payloads/checkpoint can resume next batch.',file=sys.stderr)
            return 1
        record_auth_error(auth_status_file,exc,context=client.fingerprint)
        return 1
    except GarminConfigurationError as exc:
        record_auth_error(auth_status_file,exc,context=client.fingerprint)
        raise
    finally:
        if own_client:client.close()


def verify_history(client,begin,end,state_file):
    """Fresh summary-ID pass; repair absent official IDs without refetching known detail."""
    assert_existing_database_safe(DB_PATH)
    state=json.loads(state_file.read_text(encoding='utf-8'))
    progress=state.get('verification') or {}
    window={'start_date':begin,'end_date':end}
    if progress.get('window')!=window or progress.get('complete'):
        progress={'window':window,'next_offset':0,'seen_ids':[],'repaired_ids':[]}
    known=get_existing_source_ids(DB_PATH,SOURCE)
    seen=set(progress['seen_ids'])
    try:
        while True:
            activities=client.activities(begin,end,start=progress['next_offset'],limit=50)
            if not activities:break
            for activity in activities:
                identifier=activity.get('activityId')
                if type(identifier) is not int or identifier<=0:raise ValueError('History verification received an invalid activity ID.')
                if identifier not in known:
                    payload=map_garmin_to_payload(activity,client.activity(identifier),[])
                    private_json_write(DATA_DIR/('garmin_personal_'+str(identifier)+'_ai_payload.json'),payload)
                    known.add(identifier);progress['repaired_ids'].append(identifier)
                seen.add(identifier);progress['seen_ids']=sorted(seen)
                progress['next_offset']+=1
                state['verification']=progress;private_json_write(state_file,state)
            if len(activities)<50:break
        progress['scan_complete']=True;progress['complete']=False
        state['verification']=progress;private_json_write(state_file,state)
        print(f'Historical verification completed: {len(seen)} distinct available top-level Garmin IDs checked; {len(progress["repaired_ids"])} missing IDs queued for repair.')
        return 0
    except GarminPersonalError as error:
        if error.code=='batch_limit':print('History verification batch saved; repeat the same command to continue.',file=sys.stderr)
        else:record_auth_error(AUTH_STATUS_FILE,error,context=client.fingerprint)
        return 1


def finish_history_verification(state_file):
    """Only attest completeness after the payload transaction actually committed."""
    state=json.loads(state_file.read_text(encoding='utf-8'))
    progress=state['verification']
    missing=sorted(set(progress['seen_ids'])-get_existing_source_ids(DB_PATH,SOURCE))
    progress['missing_ids']=missing
    progress['complete']=bool(progress.get('scan_complete')) and not missing
    progress['import_checked_at']=datetime.now(timezone.utc).isoformat()
    if missing:progress['next_offset']=0
    private_json_write(state_file,state)
    print(f'Historical import coverage: {len(progress["seen_ids"])} observed distinct official IDs, {len(missing)} missing after ingestion.')
    return 1 if missing else 0


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--start-date',help='YYYY-MM-DD, or earliest for official historical discovery');parser.add_argument('--end-date')
    parser.add_argument('--backfill-days',type=int)
    parser.add_argument('--historical-backfill',action='store_true',help='Import an explicit historical window using an isolated resumable checkpoint; never marks daily sync fresh')
    parser.add_argument('--verify-history',action='store_true',help='Fresh summary-ID scan of the historical window, repairing missing official IDs; requires --historical-backfill')
    parser.add_argument('--refresh-token',action='store_true',help='Explicitly renew the private access-token cache; no activity requests')
    parser.add_argument('--accept-key-rotation',action='store_true',help='Confirm a replacement key belongs to the same Garmin account; never use for another account')
    args=parser.parse_args(argv)
    try:
        from cron_daily_update import pipeline_lock
        from contextlib import nullcontext
        # Historical batches also ingest their queue under the daily pipeline lock.
        # This prevents cron ingest from racing a replacement/deletion of payloads.
        outer=pipeline_lock(SCRIPT_DIR/'.pipeline.lock') if args.historical_backfill else nullcontext()
        with outer:
            return run_locked_sync(args)
    except (ValueError,GarminPersonalError,OSError,RuntimeError) as exc:
        if isinstance(exc,(GarminConfigurationError,GarminPersonalError)):
            record_auth_error(AUTH_STATUS_FILE,exc)
        print(str(exc),file=sys.stderr);return 1


def run_locked_sync(args):
    attempted=False
    try:
        from cron_daily_update import pipeline_lock
        # Separate from the outer pipeline lock: direct backfills and cron must
        # never race the same private token/checkpoint files.
        with pipeline_lock(SCRIPT_DIR/'.garmin_personal_sync.lock'):
            if args.backfill_days is not None and not 1<=args.backfill_days<=3650:raise ValueError('Backfill days must be between 1 and 3650.')
            if args.historical_backfill and (not args.start_date or not args.end_date or args.refresh_token or args.backfill_days is not None):
                raise ValueError('Use --historical-backfill with explicit --start-date and --end-date only.')
            if args.verify_history and not args.historical_backfill:raise ValueError('--verify-history requires --historical-backfill.')
            if args.historical_backfill:
                if date.fromisoformat(args.end_date)>date.today():raise ValueError('Historical end date cannot be in the future.')
                if args.start_date!='earliest' and date.fromisoformat(args.start_date)>date.fromisoformat(args.end_date):raise ValueError('Historical date window must be ordered.')
                assert_existing_database_safe(DB_PATH)
            attempted=True
            if args.refresh_token:
                client=get_garmin_client()
                try:client.access_token(force_refresh=True)
                finally:client.close()
                previous=previous_auth_status(AUTH_STATUS_FILE,client.fingerprint)
                write_auth_status(AUTH_STATUS_FILE,'verification_pending',state='action_required' if previous.get('state')=='action_required' else 'unknown',context=client.fingerprint)
                print('Official Garmin access-token cache refreshed privately.');return 0
            state_file=STATE_FILE
            if args.historical_backfill:
                state_file=SCRIPT_DIR/'.garmin_personal_history.json'
                if not state_file.exists():
                    try:binding=json.loads(STATE_FILE.read_text(encoding='utf-8'))
                    except (OSError,ValueError):binding={}
                    if not isinstance(binding,dict) or not binding.get('source_identity') or not binding.get('base_url'):
                        raise GarminConfigurationError('Run the normal official Garmin sync successfully before importing history; its account binding is required.')
                    private_json_write(state_file,{key:binding[key] for key in ('source_identity','base_url')})
            elif args.start_date=='earliest':
                raise ValueError('--start-date earliest requires --historical-backfill.')
            if args.start_date=='earliest' or args.verify_history:
                # Discovery and payload requests share the same client's request ceiling.
                with closing(get_garmin_client()) as client:
                    state=json.loads(state_file.read_text(encoding='utf-8'))
                    if state.get('source_identity')!=client.fingerprint or state.get('base_url')!=client.base_url:
                        raise GarminConfigurationError('Historical discovery requires the existing account/key binding; verify normal sync first.')
                    begin=state.get('discovered_earliest_date') if args.start_date=='earliest' else args.start_date
                    if not begin:
                        first=client.activities(None,args.end_date,start=0,limit=1)
                        if first:
                            begin=str(first[0].get('startTimeLocal',''))[:10]
                            date.fromisoformat(begin)
                        else:begin=args.end_date
                        state['discovered_earliest_date']=begin
                        private_json_write(state_file,state)
                    if args.verify_history:
                        # Full-history coverage must not depend on discovery ordering.
                        result=verify_history(client,None if args.start_date=='earliest' else begin,args.end_date,state_file)
                    else:result=fetch_and_save_garmin_data(client,start_date=begin,end_date=args.end_date,state_file=state_file,historical=True)
            else:
                result=fetch_and_save_garmin_data(start_date=args.start_date,end_date=args.end_date,backfill_days=args.backfill_days,accept_key_rotation=args.accept_key_rotation,state_file=state_file,historical=args.historical_backfill)
            if args.historical_backfill:
                from database_manager import build_master_database
                build_master_database(DB_PATH,DATA_DIR)
                if args.verify_history and result==0:result=finish_history_verification(state_file)
            return result
    except (ValueError,GarminPersonalError,OSError,RuntimeError) as exc:
        if isinstance(exc,(GarminConfigurationError,GarminPersonalError)):
            record_auth_error(AUTH_STATUS_FILE,exc)
        print(str(exc),file=sys.stderr);return 1
    finally:
        if attempted:
            try:publish_direct_auth_status()
            except (OSError,RuntimeError,ValueError):print('Garmin access status was saved locally but its live status delivery could not complete.',file=sys.stderr)


if __name__=='__main__':raise SystemExit(main())
