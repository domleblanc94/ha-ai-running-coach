"""Persistent action queue and local dashboard publication for the HA app."""

from contextlib import closing
from datetime import datetime, timedelta, timezone
import json
import importlib.util
import os
from pathlib import Path
import re
import signal
import sqlite3
import subprocess
import sys
from zoneinfo import ZoneInfo

from dotenv import dotenv_values

from . import diagnostics, hr_learning
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
    'daily_request_limit': 'COACH_MAX_DAILY_REQUESTS',
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
MANUAL_EXTRA_REASON = 'Confirmed Run once anyway in Home Assistant.'


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
        self.command_failure = None
        core = Path(core_dir) if core_dir else Path(__file__).resolve().parent.parent / 'core'
        self.calendar_rules = None
        self.calendar_cache = None
        self.running_calendar_cache = None
        with file_lock(self.state_lock):
            # Install only when no wrapper subprocess is using the workspace.
            with file_lock(self.run_lock, blocking=False):
                install_core(core, self.workspace)
            specification = importlib.util.spec_from_file_location('coach_calendar_rules', core / 'training_calendar.py')
            self.calendar_rules = importlib.util.module_from_spec(specification)
            specification.loader.exec_module(self.calendar_rules)
            if not checked(self.state_file).exists():
                self._write_state({
                    'schema_version': 1, 'queued': None, 'running': None,
                    'last_run': None, 'last_success_at': None,
                    'schedule': {'enabled': False, 'time': '03:00', 'timezone': 'UTC', 'last_date': None},
                })
            state = self._state()
            previous = self._last_run(state)
        print('AI Coach [startup/ready] App workspace is ready.', flush=True)
        if previous and previous.get('status') == 'failed':
            self._log_run(previous, previous=True)

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
        try:
            request_limit = max(1, min(20, int(env.get('COACH_MAX_DAILY_REQUESTS', '2'))))
        except ValueError:
            request_limit = None
        config = {
            'provider': provider if provider in CHOICES['provider'] else '',
            'model': model if valid_model(model) else '',
            'source': source if source in CHOICES['source'] else '',
            'garmin_region': region if region in CHOICES['garmin_region'] else '',
            'daily_request_limit': request_limit,
        }
        config.update({f'has_{field}': bool(env.get(key, '').strip()) for field, key in SECRETS.items()})
        return config

    def _status(self, state):
        config = self._config(self._env())
        profile_ready = checked(self.workspace / 'athlete.md').is_file()
        plan_ready = checked(self.workspace / 'training_plan.json').is_file()
        issues = []
        if config['daily_request_limit'] is None:
            issues.append('Set a daily AI request limit from 1 to 20 in Settings.')
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
        learning = hr_learning.learning_enabled(self.profile())
        local_day = utc_now().astimezone(ZoneInfo(state['schedule']['timezone'])).date()
        return {
            'config': config, 'profile_ready': profile_ready, 'plan_ready': plan_ready,
            'dashboard_ready': checked(self.public / 'dashboard.html').is_file(),
            'setup_ready': not issues, 'setup_issues': issues,
            'schedule': state['schedule'], 'last_run': self._last_run(state),
            'queued': state['queued'], 'running': state['running'],
            'worker_ok': self.worker_error is None, 'worker_error': self.worker_error,
            'hr_learning': hr_learning.status(learning, self.workspace / 'ai_coach_master.sqlite', local_day),
            'training_day_conflicts': self._training_day_conflicts(local_day),
            'running_session_conflicts': self._running_session_conflicts(local_day),
            'request_budget': self._request_budget(config['daily_request_limit'], state),
        }

    def _request_budget(self, limit, state, now=None):
        """Read the request ledger without creating a database or reserving calls."""
        now = (now or utc_now()).astimezone(timezone.utc)
        day = now.date().isoformat()
        reset = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        used, extra_used = 0, False
        try:
            db = checked(self.workspace / 'ai_coach_master.sqlite').resolve()
            if db.is_file():
                with closing(sqlite3.connect(db.as_uri() + '?mode=ro', uri=True)) as conn:
                    if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='coach_api_requests'").fetchone():
                        rows = conn.execute('''SELECT request_kind, COUNT(*) FROM coach_api_requests
                            WHERE date(started_at)=? AND request_kind IN ('daily','operator_recovery','manual_override')
                            GROUP BY request_kind''', (day,)).fetchall()
                        used = sum(count for _, count in rows)
                        extra_used = any(kind in ('manual_override', 'operator_recovery') and count for kind, count in rows)
        except (OSError, RuntimeError, sqlite3.Error):
            used, extra_used = None, None
        reason = ('invalid_limit' if limit is None else 'ledger_unavailable' if used is None else 'used' if extra_used else
                  'allowance_remaining' if used < limit else 'busy' if state['queued'] or state['running'] else 'available')
        return {'utc_date': day, 'used': used, 'limit': limit, 'resets_at': reset.isoformat(),
                'extra_used': extra_used, 'extra_available': reason == 'available', 'extra_status': reason}

    def status(self):
        with file_lock(self.state_lock):
            return self._status(self._state())

    def profile(self):
        try:
            return checked(self.workspace / 'athlete.md').read_text(encoding='utf-8')
        except FileNotFoundError:
            return ''

    def _training_day_conflicts(self, today, plan=None, profile=None):
        try:
            if plan is None:
                path = checked(self.workspace / 'training_plan.json')
                athlete = checked(self.workspace / 'athlete.md')
                key = (path.stat().st_mtime_ns, path.stat().st_size, athlete.stat().st_mtime_ns, today)
                if self.calendar_cache and self.calendar_cache[0] == key:
                    return self.calendar_cache[1]
                plan, profile = read_json(path), self.profile()
            else:
                key = None
            parsed = self.calendar_rules.calendar_profile(profile)
            conflicts = self.calendar_rules.training_day_conflicts(plan, parsed, today)
            if key is not None:
                self.calendar_cache = (key, conflicts)
            return conflicts
        except (OSError, ValueError, TypeError, KeyError):
            return []

    def _running_session_conflicts(self, today):
        """Warn about an existing saved plan without changing or reviewing it."""
        try:
            path = checked(self.workspace / 'training_plan.json')
            key = (path.stat().st_mtime_ns, path.stat().st_size, today)
            if self.running_calendar_cache and self.running_calendar_cache[0] == key:
                return self.running_calendar_cache[1]
            conflicts = self.calendar_rules.running_session_conflicts(read_json(path), today)
            self.running_calendar_cache = (key, conflicts)
            return conflicts
        except (OSError, ValueError, TypeError, KeyError):
            return []

    def export(self, kind):
        """Read one saved revision while the worker cannot commit another."""
        if kind not in {'athlete.md', 'training_plan.md', 'training_calendar.pdf'}:
            raise ValueError('Unknown export.')
        with file_lock(self.run_lock, blocking=False), file_lock(self.state_lock):
            if kind == 'athlete.md':
                return checked(self.workspace / kind).read_bytes()
            if checked(self.workspace / '.plan-commit.json').exists():
                raise RuntimeError('The saved plan is being committed. Try again when the run finishes.')
            plan = read_json(self.workspace / 'training_plan.json')
            if kind == 'training_plan.md':
                return checked(self.workspace / kind).read_bytes()
            from .exports import calendar_pdf

            profile = self.profile()
            match = re.search(r'^\*\*Athlete Name:\*\*[ \t]*(.*)$', profile, re.M)
            today = utc_now().astimezone(ZoneInfo(self._state()['schedule']['timezone'])).date()
            conflicts = self._training_day_conflicts(today, plan, profile)
            return calendar_pdf(plan, match[1] if match else '', conflicts=conflicts)

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
            if field == 'daily_request_limit' and (not re.fullmatch(r'[0-9]{1,2}', value) or not 1 <= int(value) <= 20):
                raise ValueError('Choose a daily AI request limit from 1 to 20.')
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
            self.calendar_cache = None
            return self._status(state)

    def save_baseline_feedback(self, payload):
        payload = hr_learning.validate_feedback(payload)
        with file_lock(self.state_lock):
            state = self._state()
            self._idle(state)
            if not hr_learning.learning_enabled(self.profile()):
                raise ValueError('Enable Learn heart-rate zones before saving baseline observations.')
            timezone_name = state['schedule']['timezone']
            today = utc_now().astimezone(ZoneInfo(timezone_name)).date()
            database = self.workspace / 'ai_coach_master.sqlite'
            try:
                rows = hr_learning.candidates(database, today)
            except sqlite3.Error:
                raise RuntimeError('Baseline activities could not be read. Try after the next successful sync.') from None
            activity = next((row for row in rows if row['activity_id'] == payload['activity_id']), None)
            if activity is None:
                raise ValueError('Choose a recent official Garmin running activity from the list.')
            feedback = {name: payload[name] for name in ('rpe', 'talk_test', 'sensor')}
            if activity['feedback'] != feedback:
                checkin = hr_learning.checkin(payload, activity, today)
                if self._command('baseline_feedback', input_text=json.dumps(checkin), timeout=15, timezone_name=timezone_name):
                    raise RuntimeError('The baseline observation could not be saved.')
            return self._status(state)

    def enqueue(self, action, manual_extra=None):
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
            if manual_extra is not None:
                if (not isinstance(manual_extra, dict) or set(manual_extra) != {'date', 'confirmed'}
                        or manual_extra['confirmed'] is not True):
                    raise ValueError('Confirm one extra AI request before continuing.')
                budget = status['request_budget']
                if manual_extra['date'] != budget['utc_date']:
                    raise ValueError('The UTC day changed. Refresh and confirm the extra request again.')
                if not budget['extra_available']:
                    raise ValueError('An extra request is available only after the normal allowance is used, once per UTC day.')
                state['queued']['manual_extra'] = {'approved_day': budget['utc_date'], 'reason': MANUAL_EXTRA_REASON}
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
        last = self._last_run(state) or {}
        running = state['running']
        result = last.get('status')
        success = state['last_success_at']
        status = 'running' if running else {'success': 'ok', 'failed': 'error', 'interrupted': 'warning'}.get(result, 'unknown')
        if not running and last.get('code') in {'provider_auth', 'provider_config', 'garmin_auth'}:
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

    def _command(self, action, *, input_text=None, timeout=2500, timezone_name='UTC', manual_extra=None):
        check_tree(self.workspace)
        allowed = {'PATH', 'SYSTEMROOT', 'WINDIR', 'TEMP', 'TMP', 'TMPDIR', 'LANG', 'SSL_CERT_FILE', 'SSL_CERT_DIR'}
        env = {key: value for key, value in os.environ.items() if key.upper() in allowed}
        coaching = action not in ('validate_profile', 'baseline_feedback')
        if coaching:
            env.update(self._env())
        env.update(COACH_PUBLISH_ENABLED='false', PYTHONIOENCODING='utf-8', TZ=timezone_name)
        # The isolated interpreter ignores PYTHONIOENCODING; UTF-8 mode also covers stdin.
        command = [sys.executable, '-I', '-X', 'utf8', str(Path(__file__).with_name('core_runner.py').resolve()), str(self.workspace.resolve()), action]
        if manual_extra is not None:
            command += ['--manual-extra-attempt', manual_extra['approved_day'], '--manual-reason', manual_extra['reason']]
        options = {'creationflags': subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == 'nt' else {'start_new_session': True}
        try:
            with subprocess.Popen(command, cwd=self.workspace, env=env,
                                  stdin=subprocess.PIPE if input_text is not None else subprocess.DEVNULL,
                                  text=True, encoding='utf-8', errors='replace', stdout=subprocess.DEVNULL,
                                  stderr=subprocess.PIPE if coaching else subprocess.DEVNULL, **options) as process:
                try:
                    _, stderr = process.communicate(input_text, timeout=timeout)
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
                    if coaching:
                        self.command_failure = 'command_timeout'
                    return 1
                if coaching and process.returncode:
                    self.command_failure = diagnostics.command_failure(stderr)
                return process.returncode
        except OSError as error:
            if coaching:
                self.command_failure = 'workspace_permissions' if isinstance(error, PermissionError) else 'core_startup_failed'
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

    def _request_details(self, started, finished=None):
        try:
            db = checked(self.workspace / 'ai_coach_master.sqlite').resolve()
            if not db.is_file():
                return None, 0
            with closing(sqlite3.connect(db.as_uri() + '?mode=ro', uri=True)) as conn:
                if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='coach_api_requests'").fetchone():
                    return None, 0
                rows = conn.execute('''
                    SELECT started_at, status, error FROM coach_api_requests
                    WHERE request_kind IN ('daily', 'operator_recovery', 'manual_override') ORDER BY id DESC LIMIT 20
                ''').fetchall()
            current = [row for row in rows if diagnostics.current_timestamp(row[0], started, finished)]
            return (current[0] if current else None), len(current)
        except (OSError, RuntimeError, sqlite3.Error):
            return None, None

    def _failure(self, started, request, *, finished=None, command_failure=None):
        report = None
        try:
            candidate = read_json(self.workspace / 'pipeline_status.json')
            if isinstance(candidate, dict) and diagnostics.current_timestamp(candidate.get('attempted_at'), started, finished):
                report = candidate
        except (OSError, ValueError, TypeError):
            pass
        errors = report.get('errors') if report else None
        if not isinstance(errors, list):
            errors = []
        if 'Activity sync failed; showing saved data' in errors:
            auth = report.get('garmin_auth')
            if (isinstance(auth, dict) and isinstance(auth.get('code'), str) and auth['code'] in GARMIN_ISSUES
                    and diagnostics.current_timestamp(auth.get('checked_at'), started, finished)):
                return ('garmin_auth' if auth.get('state') == 'action_required' else 'garmin_sync', GARMIN_ISSUES[auth['code']])
            return 'activity_sync_failed', diagnostics.MESSAGES['activity_sync_failed']
        if 'Activity processing failed' in errors:
            return 'activity_processing_failed', diagnostics.MESSAGES['activity_processing_failed']
        if request:
            code = diagnostics.request_failure(request[1], request[2])
            if code:
                return code, diagnostics.MESSAGES[code]
        if 'Coach review pending; previous review retained' in errors:
            code = command_failure or 'pipeline_failed'
            return code, diagnostics.MESSAGES[code]
        if 'Dashboard generation failed; previous HTML retained' in errors:
            return 'dashboard_render_failed', diagnostics.MESSAGES['dashboard_render_failed']
        if command_failure:
            return command_failure, diagnostics.MESSAGES[command_failure]
        if finished is not None and report and report.get('errors') == []:
            return 'publication_failed', diagnostics.MESSAGES['publication_failed']
        code = 'pipeline_failed' if report or finished is not None else 'core_startup_failed'
        return code, diagnostics.MESSAGES[code]

    def _with_dashboard_context(self, message):
        if (self.public / 'dashboard.html').is_file():
            return message + ' The last successful dashboard remains available.'
        return message + ' A dashboard has not been published yet.'

    def _last_run(self, state):
        last = state['last_run']
        if not last or last.get('status') != 'failed' or last.get('code') != 'pipeline_failed' or last.get('stage'):
            return last
        try:
            started = datetime.fromisoformat(last['started_at'])
            finished = datetime.fromisoformat(last['finished_at'])
            if started.tzinfo is None or finished.tzinfo is None or finished < started:
                return last
        except (ValueError, KeyError, TypeError):
            return last
        request, attempts = self._request_details(started, finished)
        code, message = self._failure(started, request, finished=finished)
        return dict(last, code=code, stage=diagnostics.failure_stage(code),
                    message=self._with_dashboard_context(message), request_attempts=attempts)

    @staticmethod
    def _log_run(run, *, previous=False):
        attempts = run.get('request_attempts')
        count = str(attempts) if type(attempts) is int and 0 <= attempts <= 20 else 'unknown'
        code = run.get('code')
        stage = run.get('stage')
        allowed_stages = {'sync', 'activities', 'coaching', 'render', 'publication', 'startup', 'unknown'}
        allowed_codes = set(diagnostics.MESSAGES) | {'complete', 'garmin_sync', 'garmin_auth'}
        if not isinstance(code, str) or not isinstance(stage, str) or code not in allowed_codes or stage not in allowed_stages:
            return
        guidance = diagnostics.MESSAGES.get(code, {
            'complete': 'Dashboard updated.',
            'garmin_auth': 'Check Garmin Personal API access in Settings.',
            'garmin_sync': 'Check Garmin sync status in the app.',
        }.get(code, ''))
        context = 'Previous run' if previous else 'Run'
        try:
            finished = datetime.fromisoformat(run.get('finished_at'))
            if finished.tzinfo is not None:
                context += ' at ' + finished.astimezone(timezone.utc).isoformat()
        except (ValueError, TypeError):
            pass
        print(f'AI Coach [{stage}/{code}] {context}: {guidance} Request attempts: {count}.', flush=True)

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
            if action.get('manual_extra', {}).get('approved_day') == now.astimezone(timezone.utc).date().isoformat():
                # Audit the explicit action. Only the core's durable request
                # reservation consumes its allowance; preflight can fail first.
                state['last_manual_extra'] = dict(action['manual_extra'], action=action['action'], claimed_at=now.isoformat())
            self._write_state(state)
            return state['running']

    def tick(self):
        try:
            with file_lock(self.run_lock, blocking=False):
                started = utc_now()
                action = self._claim(started)
                if not action:
                    return False
                attempts = None
                try:
                    self.command_failure = None
                    operation = action['action']
                    options = {'timezone_name': action['timezone']}
                    extra = action.get('manual_extra')
                    if extra is not None:
                        options['manual_extra'] = extra
                    if hr_learning.learning_enabled(self.profile()):
                        operation = 'learn_' + operation
                        options['input_text'] = hr_learning.LEARNING_QUESTION
                    if extra is not None and extra['approved_day'] != utc_now().date().isoformat():
                        self.command_failure = 'manual_extra_expired'
                        result = 1
                    else:
                        result = self._command(operation, **options)
                    request, attempts = self._request_details(started)
                    if result == 0:
                        try:
                            success = self._publish(started)
                        except (OSError, RuntimeError, ValueError, TypeError, KeyError):
                            success = False
                        code = 'complete' if success else 'publication_failed'
                        message = 'Dashboard updated.' if success else diagnostics.MESSAGES[code]
                    else:
                        success = False
                        code, message = self._failure(started, request, command_failure=self.command_failure)
                except (OSError, RuntimeError, ValueError, TypeError, KeyError):
                    success = False
                    code, message = 'core_startup_failed', diagnostics.MESSAGES['core_startup_failed']
                stage = 'publication' if success else diagnostics.failure_stage(code)
                if not success:
                    message = self._with_dashboard_context(message)
                finished = utc_now().isoformat()
                with file_lock(self.state_lock):
                    state = self._state()
                    state['running'] = None
                    state['last_run'] = dict(action, status='success' if success else 'failed',
                                             finished_at=finished, stage=stage, code=code, message=message,
                                             request_attempts=attempts)
                    if success:
                        state['last_success_at'] = finished
                    self._write_state(state)
                self._log_run(state['last_run'])
                return True
        except LockBusy:
            return False

    def run_worker(self, stop_event):
        while not stop_event.is_set():
            try:
                self.tick()
            except (OSError, RuntimeError, ValueError, TypeError, KeyError):
                self.worker_error = 'The coach worker stopped. Check app storage and restart the app.'
                print('AI Coach [worker/stopped] ' + self.worker_error, flush=True)
                return
            stop_event.wait(2)
