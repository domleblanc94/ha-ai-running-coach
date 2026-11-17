"""Private race-course storage and an explicit, shared-budget coaching action."""

from datetime import date
import base64
import json
from zoneinfo import ZoneInfo

from .storage import atomic_json, checked, file_lock


GPX_LIMIT = 5 * 1024 * 1024
MESSAGES = {
    'daily_budget': 'The shared AI request allowance is used for this UTC day. Your saved race plan is retained.',
    'input_too_large': 'The race context is too large for the configured model input limit. Shorten your question or use a simpler course.',
    'provider_auth': 'Check the selected AI provider key and model access in Setup & settings.',
    'provider_rate_limit': 'The AI provider is limiting requests. Wait before requesting another race review.',
    'provider_unavailable': 'The AI provider is temporarily unavailable. Your saved race plan is retained.',
    'provider_request': 'The provider could not complete this race request. Check your model settings before trying again.',
    'response_invalid': 'The race response did not pass validation. Your last valid race plan is retained.',
    'route_discontinuous': 'This GPX has disconnected track sections. Upload a continuous course before generating pacing or a course FIT.',
    'invalid_input': 'The GPX or race details are invalid. Use a valid GPX track or route, a race name and an optional ISO date.',
    'busy': 'Another coach action is using the workspace. Wait for it to finish.',
    'interrupted': 'The race review was interrupted. Your last valid race plan is retained; it will not retry automatically.',
    'race_unavailable': 'Race planning could not read its saved data. Check the app logs and try again after the current action finishes.',
}


class RaceFailure(RuntimeError):
    def __init__(self, code):
        self.code = code if code in MESSAGES else 'race_unavailable'
        super().__init__(MESSAGES[self.code])


class RaceRuntime:
    def _race_call(self, operation, payload, *, timezone_name='UTC'):
        result, output = self._command(operation, input_text=json.dumps(payload, allow_nan=False),
                                       timeout=150 if operation == 'race_review' else 20,
                                       timezone_name=timezone_name, capture=True)
        if result or not isinstance(output, str) or len(output) > 16 * 1024 * 1024:
            raise RaceFailure('race_unavailable')
        try:
            envelope = json.loads(output)
            if not isinstance(envelope, dict) or type(envelope.get('ok')) is not bool:
                raise ValueError('Invalid envelope')
            if not envelope['ok']:
                raise RaceFailure(envelope.get('code'))
            return envelope['value']
        except (ValueError, KeyError, TypeError):
            raise RaceFailure('race_unavailable') from None

    def _race_course(self):
        path = checked(self.data / 'race-course.json')
        if not path.exists():
            return None
        try:
            course = json.loads(path.read_bytes())
            if not isinstance(course, dict) or not isinstance(course.get('route_hash'), str):
                raise ValueError('Invalid course')
            return course
        except (ValueError, OSError):
            raise RaceFailure('race_unavailable') from None

    def _race_ready(self, state):
        config = self._config(self._env())
        return bool(self.profile().strip() and config['provider'] and config['model']
                    and config.get('has_' + config['provider'] + '_api_key') and not self.worker_error)

    def race_status(self):
        with file_lock(self.state_lock):
            state = self._state()
            course = self._race_course()
            result = {'course': None, 'plan': None, 'last_run': state.get('race_last_run'),
                      'ready': self._race_ready(state), 'busy': bool(state['queued'] or state['running'])}
        if course:
            # High-resolution GPS stays in private storage; only the bounded
            # preview is needed by the local ingress page.
            result['course'] = {key: course[key] for key in ('schema_version', 'route_hash', 'name', 'sport', 'summary', 'preview')}
            result['plan'] = self._race_call('race_read', {'route_hash': course['route_hash']})
        return result

    def save_race_course(self, payload):
        if set(payload) != {'gpx', 'filename'} or not isinstance(payload['gpx'], str):
            raise ValueError('Choose a GPX file to upload.')
        if not isinstance(payload['filename'], str) or len(payload['filename']) > 200 or '\0' in payload['filename']:
            raise ValueError('Choose a GPX filename under 200 characters.')
        if not payload['gpx'].strip() or len(payload['gpx'].encode('utf-8')) > GPX_LIMIT:
            raise ValueError('Choose a GPX file no larger than 5 MB.')
        with file_lock(self.run_lock, blocking=False), file_lock(self.state_lock):
            state = self._state()
            self._idle(state)
            course = self._race_call('race_parse', payload)
            atomic_json(self.data / 'race-course.json', course)
            # A previous course's status must not be presented as this course's
            # result. Its saved plans remain in the private race history.
            state.pop('race_last_run', None)
            self._write_state(state)
        return self.race_status()

    def enqueue_race(self, payload):
        if set(payload) != {'route_hash', 'question', 'race_name', 'race_date'}:
            raise ValueError('Provide the displayed course and race details.')
        for key, limit in (('route_hash', 64), ('question', 2000), ('race_name', 120), ('race_date', 10)):
            if not isinstance(payload[key], str) or len(payload[key]) > limit or '\0' in payload[key]:
                raise ValueError('Keep the race name under 120 characters and coaching question under 2000 characters.')
        if not payload['race_name'].strip():
            raise ValueError('Enter a race name.')
        if payload['race_date']:
            try:
                if date.fromisoformat(payload['race_date']).isoformat() != payload['race_date']:
                    raise ValueError('Invalid date')
            except ValueError:
                raise ValueError('Use a valid race date or leave it blank.') from None
        with file_lock(self.state_lock):
            state = self._state()
            self._idle(state)
            if not self._race_ready(state):
                raise ValueError('Save an athlete profile and AI provider connection before asking the race coach.')
            course = self._race_course()
            if not course or course['route_hash'] != payload['route_hash']:
                raise ValueError('The course changed. Refresh Race planning before generating strategies.')
            if course['summary'].get('discontinuities'):
                raise RaceFailure('route_discontinuous')
            state['queued'] = {'action': 'race_review', 'trigger': 'manual',
                               'queued_at': self._now().isoformat(), 'race_request': dict(payload)}
            self._write_state(state)
            return self._status(state)

    def _tick_race(self, action, started):
        """Runs inside the existing global worker lock, never publishes training."""
        attempts = 0
        cached = False
        code = 'race_unavailable'
        try:
            course = self._race_course()
            request = action['race_request']
            if not course or course['route_hash'] != request['route_hash']:
                raise RaceFailure('invalid_input')
            local_day = self._now().astimezone(ZoneInfo(action['timezone'])).date().isoformat()
            result = self._race_call('race_review', {**request, 'route': course, 'today': local_day}, timezone_name=action['timezone'])
            if not isinstance(result, dict) or result.get('route_hash') != course['route_hash']:
                raise RaceFailure('response_invalid')
            cached = result.get('cached') is True
            code = 'complete'
        except RaceFailure as error:
            code = error.code
        except (ValueError, OSError, RuntimeError, TypeError, KeyError):
            code = 'race_unavailable'
        _, attempts = self._request_details(started)
        success = code == 'complete'
        message = ('Saved race strategies reused; no AI request was needed.' if cached else 'Your race strategies are ready.') if success else MESSAGES[code]
        with file_lock(self.state_lock):
            state = self._state()
            state['running'] = None
            state['race_last_run'] = {
                'action': 'race_review', 'status': 'success' if success else 'failed', 'code': code,
                'started_at': action['started_at'], 'finished_at': self._now().isoformat(),
                'route_hash': action['race_request']['route_hash'], 'message': message,
                'request_attempts': attempts, 'cached': cached,
            }
            self._write_state(state)
        # Only fixed text, status codes and a bounded request count enter logs.
        count = attempts if type(attempts) is int and 0 <= attempts <= 20 else 'unknown'
        print(f'AI Coach [race/{code}] {message} Request attempts: {count}.', flush=True)
        return True

    def race_export(self, kind, strategy_id=None, *, route_hash=None, request_id=None):
        if kind not in ('fit', 'gpx'):
            raise ValueError('Choose the course GPX or course FIT download.')
        if strategy_id is not None and (kind != 'fit' or strategy_id not in ('conservative', 'ambitious')):
            raise ValueError('Choose a saved race strategy.')
        with file_lock(self.run_lock, blocking=False), file_lock(self.state_lock):
            course = self._race_course()
            if not course:
                raise FileNotFoundError('Upload a GPX first.')
            if not isinstance(route_hash, str) or route_hash != course['route_hash']:
                raise ValueError('The course changed. Refresh Race planning before downloading it.')
            strategy = None
            if strategy_id:
                saved = self._race_call('race_read', {'route_hash': course['route_hash']})
                if not saved or saved.get('route_hash') != course['route_hash']:
                    raise ValueError('Generate strategies for this course before exporting a timed course.')
                if type(request_id) is not int or request_id < 1 or saved.get('request_id') != request_id:
                    raise ValueError('The saved race strategies changed. Refresh Race planning before downloading a timed course.')
                strategy = next((row for row in saved['strategies'] if row['id'] == strategy_id), None)
                if not strategy or not strategy.get('finish_seconds'):
                    raise ValueError('This strategy has no reliable numeric timing. Download the untimed course instead.')
            content = self._race_call('race_export', {'route': course, 'format': kind, 'strategy': strategy})
        try:
            return base64.b64decode(content, validate=True)
        except (ValueError, TypeError):
            raise RaceFailure('race_unavailable') from None
