"""Private chat bridge settings and reviewed, local coach actions."""

import hashlib
import hmac
import json
import re
import secrets

from .storage import atomic_write, checked, file_lock

REMOTE_ACTIONS = frozenset({'context', 'plan', 'activities', 'race_context', 'save_feedback', 'propose_adjustment'})
REVIEW_ACTIONS = frozenset({'list_proposals', 'get_proposal', 'apply_proposal', 'reject_proposal'})
CHAT_MESSAGES = {
    'invalid_input': 'The chat request is invalid. Check its fields and try again.',
    'not_ready': 'Save an athlete profile and a training plan before using this action.',
    'busy': 'Another coach action is using the workspace. Wait for it to finish.',
    'source_policy': 'The saved data source cannot be used for this chat request.',
    'stale_proposal': 'Your profile or plan has changed. Ask the chat coach for a new proposal.',
    'not_found': 'This proposal is unavailable. Refresh the proposal list.',
    'conflict': 'The proposal conflicts with the saved plan. Ask for a revised proposal.',
    'unavailable': 'Coach chat is temporarily unavailable. Your saved plan is retained.',
}


class ChatFailure(RuntimeError):
    def __init__(self, code):
        self.code = code if code in CHAT_MESSAGES else 'unavailable'
        super().__init__(CHAT_MESSAGES[self.code])


class ChatRuntime:
    @staticmethod
    def _chat_status(state):
        saved = state.get('chat_bridge', {})
        return {'enabled': saved.get('enabled') is True,
                'has_token': bool(saved.get('token_sha256')),
                'changed_at': saved.get('changed_at'), 'port': 8100,
                'endpoint': '/coach-chat'}

    def configure_chat(self, payload):
        if not isinstance(payload, dict) or set(payload) != {'action'} or payload['action'] not in {'enable', 'rotate', 'disable'}:
            raise ValueError('Choose Enable, Rotate token or Disable chat connection.')
        with file_lock(self.state_lock):
            state = self._state()
            token = None
            if payload['action'] in {'enable', 'rotate'}:
                token = secrets.token_urlsafe(32)
                state['chat_bridge'] = {'enabled': True, 'token_sha256': hashlib.sha256(token.encode()).hexdigest(),
                                        'changed_at': self._now().isoformat()}
            else:
                state['chat_bridge'] = {'enabled': False, 'changed_at': self._now().isoformat()}
            self._write_state(state)
            result = {'chat_bridge': self._chat_status(state)}
            if token:
                result['token'] = token
            return result

    def chat_authorized(self, authorization):
        # Store only a digest. The raw secret is returned once via protected ingress.
        supplied = authorization[7:] if isinstance(authorization, str) and authorization.startswith('Bearer ') else ''
        if not re.fullmatch(r'[A-Za-z0-9_-]{43}', supplied):
            supplied = ''
        with file_lock(self.state_lock):
            saved = self._state().get('chat_bridge', {})
            expected = saved.get('token_sha256', '0' * 64)
            matches = hmac.compare_digest(hashlib.sha256(supplied.encode()).hexdigest(), expected)
            return bool(saved.get('enabled') is True and supplied and matches)

    def _chat_call(self, action, payload):
        code, output = self._command('chat_dispatch', input_text=json.dumps({'action': action, 'payload': payload}, allow_nan=False),
                                     timeout=45 if action == 'apply_proposal' else 20,
                                     timezone_name=self._state()['schedule']['timezone'], capture=True)
        if code or not isinstance(output, str) or len(output.encode('utf-8')) > 520 * 1024:
            raise ChatFailure('unavailable')
        try:
            result = json.loads(output)
            if not isinstance(result, dict) or type(result.get('ok')) is not bool:
                raise ValueError('Invalid envelope')
            if not result['ok']:
                raise ChatFailure(result.get('code'))
            value = result['value']
            if not isinstance(value, dict):
                raise ValueError('Invalid action result')
            return value
        except (ValueError, KeyError, TypeError):
            raise ChatFailure('unavailable') from None

    def chat_dispatch(self, action, payload, *, remote=False):
        allowed = REMOTE_ACTIONS if remote else REVIEW_ACTIONS
        if not isinstance(action, str) or action not in allowed or not isinstance(payload, dict):
            raise ValueError('Choose a supported chat action.')
        with file_lock(self.run_lock, blocking=False):
            with file_lock(self.state_lock):
                state = self._state()
                self._idle(state)
            result = self._chat_call(action, payload)
            if action == 'apply_proposal' and result.get('status') == 'applied':
                published = False
                if result.get('dashboard_refreshed') is True:
                    try:
                        page = checked(self.workspace / 'dashboard.html').read_bytes()
                        if not page or len(page) > 16 * 1024 * 1024:
                            raise ValueError('Invalid dashboard')
                        atomic_write(self.public / 'dashboard.html', page)
                        published = True
                    except (OSError, ValueError, RuntimeError):
                        pass
                result['dashboard_published'] = published
                with file_lock(self.state_lock):
                    state = self._state()
                    if published:
                        state['dashboard_revision'] = self._now().isoformat()
                    self._write_state(state)
                # A committed edit is never reported as an unapplied request if
                # rendering fails. The UI explains that the old dashboard remains.
            return result
