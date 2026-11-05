"""Persistent public workflow health, independent from last-good dashboard publishing."""
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import re
import shlex
import subprocess

def stamp():
    return datetime.now(timezone.utc).isoformat()

def atomic_json(path,value):
    path=Path(path);temp=path.with_name(path.name+'.tmp')
    with temp.open('w',encoding='utf-8') as handle:
        json.dump(value,handle,indent=2,allow_nan=False);handle.flush();os.fsync(handle.fileno())
    os.replace(temp,path)

def read_state(project):
    try:
        state=json.loads((Path(project)/'health_state.json').read_text(encoding='utf-8'))
        return state if isinstance(state,dict) else {}
    except (OSError,ValueError):return {}

AUTH_ACTIONS={
    'ok':('Garmin authentication succeeded.',''),
    'missing_key':('Garmin needs your Personal API key.','Set GARMIN_PERSONAL_API_KEY privately in the VM .env file, then run python garmin_sync.py.'),
    'key_rejected':('Garmin rejected the Personal API key; it may be expired, revoked, invalid or lack permission.','Check your key/access in Garmin Personal API settings. If the existing key is corrected, run python garmin_sync.py --refresh-token then python garmin_sync.py. For a new replacement key for the same account, update GARMIN_PERSONAL_API_KEY privately and run python garmin_sync.py --accept-key-rotation.'),
    'access_token_rejected':('Garmin rejected the short-lived access token after renewal.','Check Personal API access, run python garmin_sync.py --refresh-token, then python garmin_sync.py. A short-lived access-token failure does not establish that the Personal API key expired.'),
    'permission_denied':('Garmin denied access to the requested activity data.','Check your Personal API account permissions, then run python garmin_sync.py. Do not share the key in chat or the dashboard.'),
    'rate_limited':('Garmin temporarily limited requests.','Wait for the saved retry interval before another sync; do not repeatedly retry.'),
    'temporary_error':('Garmin authentication or sync is temporarily unavailable.','Retain the last-good data and retry through the normal bounded sync.'),
    'account_binding_required':('Garmin account/key setup needs your confirmation.','Use --accept-key-rotation only for a confirmed replacement key for the same account; use a fresh workspace for another account, region or lost binding.'),
    'verification_pending':('Garmin authentication has not been verified yet.','Run python garmin_sync.py after configuring the private Personal API key.'),
    'unknown':('Garmin authentication status is unknown.','Check the VM sync status; no key expiry date has been established.')}


def public_garmin_auth(value):
    if not isinstance(value,dict):return None
    raw_state=value.get('state');raw_code=value.get('code')
    state=raw_state if isinstance(raw_state,str) and raw_state in {'ok','action_required','temporary_error','unknown'} else 'unknown'
    code=raw_code if isinstance(raw_code,str) and raw_code in AUTH_ACTIONS else 'unknown'
    message,action=AUTH_ACTIONS[code]
    result={'schema_version':1,'state':state,'code':code,'message':message,'action':action}
    try:
        checked=datetime.fromisoformat(value.get('checked_at',''))
        if checked.tzinfo is not None:result['checked_at']=checked.isoformat()
    except (ValueError,TypeError):pass
    status=value.get('http_status')
    if type(status) is int and 100<=status<=599:result['http_status']=status
    retry=value.get('retry_after')
    if type(retry) in (int,float) and math.isfinite(retry) and retry>0:result['retry_after']=retry
    return result


def public_state(state):
    keys=('phase','last_pipeline_attempt_at','last_pipeline_success_at','last_sync_success_at','last_coach_success_at','last_render_success_at','last_publish_attempt_at','last_publish_success_at','plan_revision','status_upload_at')
    public={key:state.get(key) for key in keys}
    issues=list(state.get('pipeline_errors') or [])
    for key in ('publish_error','status_transport_error'):
        if state.get(key):issues.append(state[key])
    auth=public_garmin_auth(state.get('garmin_auth'))
    if auth:
        public['garmin_auth']=auth
        if auth['state']=='action_required':
            message='INPUT REQUIRED: '+auth['message']+' '+auth['action']+' Last confirmed activity sync: '+str(public.get('last_sync_success_at') or 'not yet confirmed')
            if message not in issues:issues.append(message)
    public.update(schema_version=1,updated_at=state.get('updated_at'),issues=issues)
    running=public.get('phase') in ('syncing','processing','coaching','rendering','publishing')
    waiting=public.get('phase') in ('awaiting_publish','publish_blocked')
    public['status']='error' if issues else 'running' if running else 'warning' if waiting else 'ok' if public.get('last_pipeline_success_at') and public.get('last_publish_success_at') else 'unknown'
    if auth and auth['state']=='action_required':public['status']='input_required'
    try:
        attempted=datetime.fromisoformat(public['last_pipeline_attempt_at'])
        succeeded=datetime.fromisoformat(public['last_pipeline_success_at'])
        if attempted>succeeded and public['status']=='ok':public['status']='warning'
    except (ValueError,TypeError,KeyError):
        if public['status']=='ok':public['status']='unknown' 
    return public

def update_health(project,**changes):
    from cron_daily_update import pipeline_lock
    project=Path(project)
    with pipeline_lock(project/'.health.lock'):
        state=read_state(project);state.update(changes,updated_at=stamp())
        atomic_json(project/'health_state.json',state)
        atomic_json(project/'coach-status.json',public_state(state))
    return state

def transport_from_script(script,project):
    """Accept only the existing simple SCP form; never execute parsed shell text."""
    project=Path(project).resolve();commands=[]
    for line in Path(script).read_text(encoding='utf-8').splitlines():
        stripped=line.strip()
        if not stripped or stripped.startswith('#'):continue
        tokens=shlex.split(stripped,comments=True)
        if not tokens or Path(tokens[0]).name!='scp':
            raise ValueError('Status upload needs a simple SCP-only existing upload script')
        commands.append(tokens)
    if len(commands)!=1:raise ValueError('Exactly one SCP command is required')
    tokens=commands[0];options=[];index=1
    while index<len(tokens)-2:
        flag=tokens[index]
        if flag in ('-i','-P'):
            if index+1>=len(tokens)-2:raise ValueError('Incomplete SCP option')
            value=tokens[index+1]
            if flag=='-i':value=str(Path(value).expanduser())
            elif not value.isdigit():raise ValueError('Invalid port')
            options.extend([flag,value]);index+=2
        else:raise ValueError('Unsupported SCP option in status transport')
    if index!=len(tokens)-2:raise ValueError('Invalid SCP arguments')
    source=Path(tokens[-2]).expanduser().resolve()
    if source!=project/'dashboard.html':raise ValueError('Upload source differs from this project dashboard')
    match=re.fullmatch(r'([A-Za-z0-9_.-]+@[A-Za-z0-9_.-]+):(/[A-Za-z0-9_./-]+)',tokens[-1])
    if not match:raise ValueError('Unsupported upload destination syntax')
    host,remote=match.groups()
    if '..' in Path(remote).parts:raise ValueError('Unsafe remote path')
    directory=remote.rsplit('/',1)[0]
    ssh_options=[]
    for i in range(0,len(options),2):ssh_options.extend(['-p' if options[i]=='-P' else options[i],options[i+1]])
    return options,ssh_options,host,directory

def publishing_enabled(project):
    value=os.getenv('COACH_PUBLISH_ENABLED','').strip().lower()
    if value in ('0','false','no','off'):return False
    if value in ('1','true','yes','on'):return True
    return (Path(project)/'upload.sh').exists()


def upload_health(project,script=None,runner=None):
    """Publish only the status sidecar using the user's unchanged SCP transport."""
    project=Path(project);script=Path(script or project/'upload.sh');runner=runner or subprocess.run
    if not publishing_enabled(project) and not script.exists():return True
    if os.getenv('COACH_PUBLISH_ENABLED','').strip().lower() in ('0','false','no','off'):return True
    try:
        options,ssh_options,host,directory=transport_from_script(script,project)
        # Clear only prior status-transport failure before the attempt. Preserve pipeline/publish failures.
        update_health(project,status_transport_error=None,status_upload_at=stamp())
        source=project/'coach-status.json';remote=directory+'/coach-status.json';temporary=directory+'/.coach-status.json.tmp'
        runner(['scp',*options,'-o','BatchMode=yes',str(source),host+':'+temporary],check=True,capture_output=True,text=True,timeout=30)
        runner(['ssh',*ssh_options,'-o','BatchMode=yes','-o','ConnectTimeout=10',host,'mv -- '+shlex.quote(temporary)+' '+shlex.quote(remote)],check=True,capture_output=True,text=True,timeout=30)
        return True
    except (OSError,ValueError,subprocess.SubprocessError):
        update_health(project,status_transport_error='Live status could not be delivered to Home Assistant')
        return False
