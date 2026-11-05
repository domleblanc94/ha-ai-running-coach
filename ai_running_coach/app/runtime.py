"""Persistent action queue and local dashboard publication for the HA app."""

from contextlib import closing
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import signal
import sqlite3
import subprocess
import sys
from zoneinfo import ZoneInfo

from dotenv import dotenv_values

from .scheduler import due_date, validate_schedule
from .storage import (
    LockBusy, atomic_json, atomic_write, check_tree, checked, checked_root, file_lock,
    install_core, private_directory,
)


SETTINGS = {
    'provider': 'COACH_PROVIDER',
    'model': 'COACH_MODELS',
    'source': 'COACH_ACTIVITY_SOURCE',
    'garmin_region': 'GARMIN_PERSONAL_REGION',
}
SECRETS = {
    'gemini_api_key': 'GEMINI_API_KEY',
    'openai_api_key': 'OPENAI_API_KEY',
    'anthropic_api_key': 'ANTHROPIC_API_KEY',
    'garmin_api_key': 'GARMIN_PERSONAL_API_KEY',
}
ENV_KEYS = set(SETTINGS.values()) | set(SECRETS.values()) | {
    'COACH_PUBLISH_ENABLED', 'COACH_MAX_OUTPUT_TOKENS', 'COACH_MAX_ATTEMPTS',
    'COACH_MAX_DAILY_REQUESTS', 'COACH_MAX_INPUT_CHARS', 'COACH_HTTP_TIMEOUT_SECONDS',
    'GARMIN_HTTP_TIMEOUT_SECONDS', 'GARMIN_MAX_REQUESTS_PER_RUN', 'GARMIN_BACKFILL_DAYS',
    'OPENAI_BASE_URL', 'OPENAI_TOKEN_PARAMETER', 'COACH_ALLOW_KEYLESS',
    'GEMINI_BASE_URL', 'ANTHROPIC_BASE_URL', 'ANTHROPIC_VERSION',
}
CHOICES = {
    'provider': {'gemini', 'openai', 'anthropic'},
    'source': {'offline', 'garmin'},
    'garmin_region': {'global', 'china'},
}
GARMIN_ISSUES = {
    'missing_key': 'Add your Garmin Personal API key in Settings.',
    'key_rejected': 'Garmin rejected the key. Check its validity and permissions in Garmin, then update Settings.',
    'access_token_rejected': 'Garmin rejected the renewed access token. Check Garmin Personal API access.',
    'permission_denied': 'Garmin denied access to activity data. Check your Personal API permissions.',
    'account_binding_required': 'Garmin account binding needs attention. See the app migration and key rotation instructions.',
    'rate_limited': 'Garmin is limiting requests. Wait before running again.',
    'temporary_error': 'Garmin is temporarily unavailable. Try a later run.',
}


def utc_now():
    return datetime.now(timezone.utc)


def valid_model(value):
    if not isinstance(value, str) or len(value) > 200:
        return False
    models = value.split(',')
    return 1 <= len(models) <= 2 and all(
        re.fullmatch(r'[A-Za-z0-9_.:/-]+', model.strip()) for model in models
    )


def read_json(path):
    return json.loads(checked(path).read_bytes())


class Runtime:
    def __init__(self, data_dir, core_dir=None):
        self.data = private_directory(checked_root(data_dir))
        self.workspace = private_directory(self.data / 'workspace')
        self.public = private_directory(self.data / 'public')
        self.state_file = self.data / 'app-state.json'
        self.state_lock = self.data / '.state.lock'
        self.run_lock = self.data / '.run.lock'
        self.worker_error = None
        core = Path(core_dir) if core_dir else Path(__file__).resolve().parent.parent / 'core'
        with file_lock(self.state_lock):
            # Install only when no wrapper subprocess is using the workspace.
            with file_lock(self.run_lock, blocking=False):
                install_core(core, self.workspace)
            if not checked(self.state_file).exists():
                self._write_state({
                    'schema_version': 1, 'queued': None, 'running': None,
                    'last_run': None, 'last_success_at': None,
                    'schedule': {'enabled': False, 'time': '03:00', 'timezone': 'UTC', 'last_date': None},
                })

    def _state(self):
        state = read_json(self.state_file)
        if not isinstance(state, dict) or state.get('schema_version') != 1:
            raise RuntimeError('Saved app state is unavailable.')
        return state

    def _write_state(self, state):
        atomic_json(self.state_file, state)

    def _env(self):
        env = dotenv_values(checked(self.workspace / '.env'), interpolate=False)
        if set(env) - ENV_KEYS:
            raise RuntimeError('Private configuration contains an unsupported setting.')
        return {key: value for key, value in env.items() if value is not None}

    def _config(self, env):
        provider = env.get('COACH_PROVIDER', 'gemini')
        model = env.get('COACH_MODELS', 'gemini-3.5-flash-lite' if provider == 'gemini' else '')
        source = env.get('COACH_ACTIVITY_SOURCE', 'offline')
        region = env.get('GARMIN_PERSONAL_REGION', 'global')
        config = {
            'provider': provider if provider in CHOICES['provider'] else '',
            'model': model if valid_model(model) else '',
            'source': source if source in CHOICES['source'] else '',
            'garmin_region': region if region in CHOICES['garmin_region'] else '',
        }
        config.update({f'has_{field}': bool(env.get(key, '').strip()) for field, key in SECRETS.items()})
        return config

    def _status(self, state):
        config = self._config(self._env())
        profile_ready = checked(self.workspace / 'athlete.md').is_file()
        plan_ready = checked(self.workspace / 'training_plan.json').is_file()
        issues = []
        if not profile_ready:
            issues.append('Save an athlete profile.')
        if not config['provider'] or not config['model']:
            issues.append('Choose a supported AI provider and model.')
        elif not config[f"has_{config['provider']}_api_key"]:
            issues.append('Add the selected AI provider key.')
        if not config['source']:
            issues.append('Choose an activity source.')
        if config['source'] == 'garmin' and (not config['has_garmin_api_key'] or not config['garmin_region']):
            issues.append('Add a Garmin Personal API key and region.')
        return {
            'config': config, 'profile_ready': profile_ready, 'plan_ready': plan_ready,
            'dashboard_ready': checked(self.public / 'dashboard.html').is_file(),
            'setup_ready': not issues, 'setup_issues': issues,
            'schedule': state['schedule'], 'last_run': state['last_run'],
            'queued': state['queued'], 'running': state['running'],
            'worker_ok': self.worker_error is None, 'worker_error': self.worker_error,
        }

    def status(self):
        with file_lock(self.state_lock):
            return self._status(self._state())

    def profile(self):
        try:
            return checked(self.workspace / 'athlete.md').read_text(encoding='utf-8')
        except FileNotFoundError:
            return ''

    @staticmethod
    def _idle(state):
        if state['queued'] or state['running']:
            raise RuntimeError('Wait for the current coach action to finish.')

    def save_config(self, values):
        if not isinstance(values, dict) or not values or set(values) - (set(SETTINGS) | set(SECRETS)):
            raise ValueError('Unsupported settings fields.')
        for field, value in values.items():
            if not isinstance(value, str):
                raise ValueError('Settings values must be text.')
            if field in CHOICES and value not in CHOICES[field]:
                raise ValueError(f'Choose a supported {field.replace("_", " ")}.')
            if field == 'model' and not valid_model(value):
                raise ValueError('Enter a supported model identifier.')
            if field in SECRETS and value and not re.fullmatch(r'[A-Za-z0-9._~+/=\-]{1,4096}', value):
                raise ValueError('A key contains unsupported characters.')
        with file_lock(self.state_lock):
            state = self._state()
            self._idle(state)
            env = self._env()
            for field, value in values.items():
                if field not in SECRETS or value:
                    env[(SETTINGS | SECRETS)[field]] = value
            env['COACH_PUBLISH_ENABLED'] = 'false'
            text = ''.join(f'{name}={json.dumps(value)}\n' for name, value in sorted(env.items()))
            atomic_write(self.workspace / '.env', text.encode())
            if not self._status(state)['setup_ready'] and state['schedule']['enabled']:
                state['schedule']['enabled'] = False
                self._write_state(state)
            return self._status(state)

    def save_profile(self, markdown):
        if not isinstance(markdown, str) or not markdown.strip() or len(markdown.encode()) > 32000 or '\0' in markdown:
            raise ValueError('Provide a profile of at most 32 KB.')
        with file_lock(self.state_lock):
            state = self._state()
            self._idle(state)
            if self._command('validate_profile', input_text=markdown, timeout=15):
                raise ValueError('Profile validation failed. Check the heart-rate zones or leave them explicitly unknown.')
            atomic_write(self.workspace / 'athlete.md', markdown.encode())
            return self._status(state)

    def enqueue(self, action):
        if action not in ('new_plan', 'run_now'):
            raise ValueError('Choose a supported coach action.')
        with file_lock(self.state_lock):
            if self.worker_error:
                raise RuntimeError(self.worker_error)
            state = self._state()
            self._idle(state)
            status = self._status(state)
            if not status['setup_ready']:
                raise ValueError('Complete setup before running the coach.')
            if action == 'run_now' and not status['plan_ready']:
                raise ValueError('Generate the first plan before running a review.')
            state['queued'] = {'action': action, 'trigger': 'manual', 'queued_at': utc_now().isoformat()}
            self._write_state(state)
            return self._status(state)

    def set_schedule(self, enabled, time='03:00', timezone='UTC'):
        validate_schedule(enabled, time, timezone)
        with file_lock(self.state_lock):
            if enabled and self.worker_error:
                raise RuntimeError(self.worker_error)
            state = self._state()
            status = self._status(state)
            if enabled and not (status['setup_ready'] and status['plan_ready'] and status['dashboard_ready']):
                raise ValueError('Complete setup and generate a dashboard before enabling the schedule.')
            old = state['schedule']
            last_date = old['last_date']
            if enabled and (not old['enabled'] or (time, timezone) != (old['time'], old['timezone'])):
                now = utc_now().astimezone(ZoneInfo(timezone))
                if now.strftime('%H:%M') >= time:
                    last_date = max(last_date or '', now.date().isoformat())
            state['schedule'] = {'enabled': enabled, 'time': time, 'timezone': timezone, 'last_date': last_date}
            self._write_state(state)
            return self._status(state)

    def dashboard(self):
        try:
            checked(self.public)
            return checked(self.public / 'dashboard.html').read_bytes()
        except FileNotFoundError:
            return None

    def health(self):
        try:
            with file_lock(self.state_lock):
                state = self._state()
        except (OSError, RuntimeError, ValueError):
            return {'schema_version': 1, 'status': 'error', 'updated_at': utc_now().isoformat(),
                    'issues': ['Saved app state is unavailable. Check the app storage and restart.']}
        last = state['last_run'] or {}
        running = state['running']
        result = last.get('status')
        success = state['last_success_at']
        status = 'running' if running else {'success': 'ok', 'failed': 'error', 'interrupted': 'warning'}.get(result, 'unknown')
        if not running and last.get('code') in {'provider_auth', 'garmin_auth'}:
            status = 'input_required'
        if self.worker_error:
            status = 'error'
        return {
            'schema_version': 1, 'status': status,
            'updated_at': (running or {}).get('started_at') or last.get('finished_at'),
            'issues': ([self.worker_error] if self.worker_error else
                       [last['message']] if not running and result in {'failed', 'interrupted'} else []),
            'last_pipeline_attempt_at': (running or last).get('started_at'),
            'last_pipeline_success_at': success, 'last_publish_success_at': success,
        }

    def _command(self, action, *, input_text=None, timeout=2500, timezone_name='UTC'):
        check_tree(self.workspace)
        allowed = {'PATH', 'SYSTEMROOT', 'WINDIR', 'TEMP', 'TMP', 'TMPDIR', 'LANG', 'SSL_CERT_FILE', 'SSL_CERT_DIR'}
        env = {key: value for key, value in os.environ.items() if key.upper() in allowed}
        if action != 'validate_profile':
            env.update(self._env())
        env.update(COACH_PUBLISH_ENABLED='false', PYTHONIOENCODING='utf-8', TZ=timezone_name)
        # The isolated interpreter ignores PYTHONIOENCODING; UTF-8 mode also covers stdin.
        command = [sys.executable, '-I', '-X', 'utf8', str(Path(__file__).with_name('core_runner.py').resolve()), str(self.workspace.resolve()), action]
        options = {'creationflags': subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == 'nt' else {'start_new_session': True}
        try:
            with subprocess.Popen(command, cwd=self.workspace, env=env,
                                  stdin=subprocess.PIPE if input_text is not None else subprocess.DEVNULL,
                                  text=True, encoding='utf-8', stdout=subprocess.DEVNULL,
                                  stderr=subprocess.DEVNULL, **options) as process:
                try:
                    process.communicate(input_text, timeout=timeout)
                except subprocess.TimeoutExpired:
                    if os.name == 'nt':
                        try:
                            subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'],
                                           env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                           timeout=10, check=False)
                        except (OSError, subprocess.TimeoutExpired):
                            pass
                    else:
                        try:
                            os.killpg(process.pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                    if process.poll() is None:
                        process.kill()
                    process.communicate()
                    return 1
                return process.returncode
        except OSError:
            return 1

    def _publish(self, started):
        if checked(self.workspace / '.plan-commit.json').exists():
            return False
        report = read_json(self.workspace / 'pipeline_status.json')
        attempted = datetime.fromisoformat(report['attempted_at'])
        if attempted < started or report.get('errors') != [] or report.get('sync_success_at') != report['attempted_at']:
            return False
        html = checked(self.workspace / 'dashboard.html')
        modified = html.stat().st_mtime
        if modified < attempted.timestamp():
            return False
        for name in ('athlete.md', 'training_plan.json', 'ai_coach_master.sqlite'):
            if checked(self.workspace / name).stat().st_mtime > modified:
                return False
        data = html.read_bytes()
        if not data:
            return False
        checked(self.public)
        atomic_write(self.public / 'dashboard.html', data)
        return True

    def _failure(self, started):
        try:
            auth = read_json(self.workspace / 'garmin_auth_status.json')
            if self._config(self._env())['source'] == 'garmin' and auth.get('code') in GARMIN_ISSUES:
                return ('garmin_auth' if auth.get('state') == 'action_required' else 'garmin_sync', GARMIN_ISSUES[auth['code']])
        except (OSError, ValueError, TypeError, AttributeError):
            pass
        try:
            db = checked(self.workspace / 'ai_coach_master.sqlite').resolve()
            with closing(sqlite3.connect(db.as_uri() + '?mode=ro', uri=True)) as conn:
                request = conn.execute('SELECT started_at, error FROM coach_api_requests ORDER BY id DESC LIMIT 1').fetchone()
            if request and datetime.fromisoformat(request[0]) >= started:
                error = request[1] or ''
                if re.search(r'\bHTTP (401|403)\b', error):
                    return 'provider_auth', 'The AI provider rejected access. Check the selected model and key in Settings.'
                if re.search(r'\bHTTP 429\b', error):
                    return 'provider_quota', 'The AI provider limited this request. Check your quota before trying again.'
        except (OSError, ValueError, TypeError, sqlite3.Error):
            pass
        return 'pipeline_failed', 'Coach run did not complete. The last successful dashboard is retained.'

    def _claim(self, now):
        with file_lock(self.state_lock):
            state = self._state()
            if state['running']:
                state['last_run'] = dict(state['running'], status='interrupted', code='interrupted',
                                         finished_at=now.isoformat(), message='A previous run was interrupted. Check the saved plan and retry manually.')
                state['running'] = None
                state['schedule']['enabled'] = False
                self._write_state(state)
                return None
            action = state['queued']
            if not action:
                day = due_date(state['schedule'], now)
                if not day:
                    return None
                status = self._status(state)
                if not (status['setup_ready'] and status['plan_ready'] and status['dashboard_ready']):
                    state['schedule']['enabled'] = False
                    self._write_state(state)
                    return None
                action = {'action': 'run_now', 'trigger': 'schedule', 'scheduled_date': day}
                state['schedule']['last_date'] = day
            state['queued'] = None
            state['running'] = dict(action, started_at=now.isoformat(), timezone=state['schedule']['timezone'])
            self._write_state(state)
            return state['running']

    def tick(self):
        try:
            with file_lock(self.run_lock, blocking=False):
                started = utc_now()
                action = self._claim(started)
                if not action:
                    return False
                try:
                    success = self._command(action['action'], timezone_name=action['timezone']) == 0 and self._publish(started)
                    if success:
                        code, message = 'complete', 'Dashboard updated.'
                    else:
                        code, message = self._failure(started)
                except (OSError, RuntimeError, ValueError, TypeError, KeyError):
                    success = False
                    code, message = 'pipeline_failed', 'Coach run did not complete. The last successful dashboard is retained.'
                finished = utc_now().isoformat()
                with file_lock(self.state_lock):
                    state = self._state()
                    state['running'] = None
                    state['last_run'] = dict(action, status='success' if success else 'failed',
                                             finished_at=finished, code=code, message=message)
                    if success:
                        state['last_success_at'] = finished
                    self._write_state(state)
                return True
        except LockBusy:
            return False

    def run_worker(self, stop_event):
        while not stop_event.is_set():
            try:
                self.tick()
            except (OSError, RuntimeError, ValueError, TypeError, KeyError):
                self.worker_error = 'The coach worker stopped. Check app storage and restart the app.'
                return
            stop_event.wait(2)
