"""Official, read-only Garmin Connect Personal API transport.

Protocol references: Garmin Personal API FAQ/Swagger and Garmin's reference client:
https://developer.garmin.com/gc-developer-program/personal/personal-faq/
https://developer.garmin.com/gc-developer-program/personal/swagger-docs/
https://github.com/garmin/connect-personal-api-python
"""
import base64
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import hashlib
import json
import math
import os
from pathlib import Path
import tempfile
import time

import requests

BASE_URLS = {'global': 'https://personal.connectapi.garmin.com',
             'china': 'https://personal.connectapi.garmin.cn'}
TOKEN_PATH = '/personalapitoken-service/personal-api/personal-api-key/token'
ACTIVITIES_PATH = '/activitylist-service/activities/search/activities'


class GarminPersonalError(RuntimeError):
    def __init__(self, message, status_code=None, retry_at=None, *, code='request_failed',
                 stage='data', action_required=False, checked_at=None):
        self.status_code = status_code
        self.retry_at = retry_at
        self.code = code
        self.stage = stage
        self.action_required = action_required
        self.checked_at = checked_at
        prefix = f'Garmin Personal API HTTP {status_code}: ' if status_code else 'Garmin Personal API: '
        super().__init__(prefix + message)


class GarminConfigurationError(ValueError):
    def __init__(self,message,code='account_binding_required'):
        self.code=code
        self.action_required=True
        super().__init__(message)


def private_json_write(path, value):
    """Replace a private cache/state file without exposing partial credentials."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix='.' + path.name)
        with os.fdopen(descriptor, 'w', encoding='utf-8') as handle:
            json.dump(value, handle, allow_nan=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)


class GarminPersonalClient:
    def __init__(self, api_key, cache_path, *, region='global', session=None,
                 timeout=30, max_requests=100, clock=time.time):
        if not isinstance(api_key, str) or not api_key.strip():
            raise GarminConfigurationError('INPUT REQUIRED: Set GARMIN_PERSONAL_API_KEY to your own official Personal API key in the VM .env.',code='missing_key')
        if region not in BASE_URLS:
            raise GarminConfigurationError('INPUT REQUIRED: GARMIN_PERSONAL_REGION must be global or china.')
        self._key = api_key.strip()
        self.base_url = BASE_URLS[region]
        self.cache_path = Path(cache_path)
        self.timeout = max(5, min(120, int(timeout)))
        self.max_requests = max(1, min(500, int(max_requests)))
        self.request_count = 0
        self.clock = clock
        self.session = session or requests.Session()
        if session is None:
            self.session.mount('https://', requests.adapters.HTTPAdapter(max_retries=0))
        self.fingerprint = hashlib.sha256((self.base_url + '\0' + self._key).encode()).hexdigest()
        try:
            self.cache = json.loads(self.cache_path.read_text(encoding='utf-8'))
            if not isinstance(self.cache, dict): self.cache = {}
        except (OSError, ValueError):
            self.cache = {}
        if self.cache.get('fingerprint') != self.fingerprint:
            self.cache = {'fingerprint':self.fingerprint}
        self._token_from_cache=False
        self.auth_verified=False

    def close(self):
        self.session.close()

    def _retry_after(self, value):
        try:
            seconds = float(value)
            if math.isfinite(seconds) and seconds >= 0:
                return self.clock() + seconds
        except (TypeError, ValueError):
            pass
        try:
            stamp = parsedate_to_datetime(value)
            if stamp.tzinfo is None: stamp = stamp.replace(tzinfo=timezone.utc)
            return max(self.clock(), stamp.timestamp())
        except (TypeError, ValueError, OverflowError):
            # The service does not publish a universal quota. Do not retry in a burst.
            return self.clock() + 15 * 60

    def _request(self, method, path, headers, params=None):
        stage='token' if method=='POST' and path==TOKEN_PATH else 'data'
        not_before = self.cache.get('not_before', 0)
        if isinstance(not_before, (int, float)) and self.clock() < not_before:
            raise GarminPersonalError('rate-limit cooldown is still active', 429, not_before,code='rate_limited',stage=stage)
        if self.request_count >= self.max_requests:
            raise GarminPersonalError('per-run request ceiling reached; saved progress can resume next run',code='batch_limit')
        self.request_count += 1
        try:
            response = self.session.request(method, self.base_url + path, headers=headers,
                                            params=params, timeout=(min(10, self.timeout), self.timeout),
                                            allow_redirects=False)
        except requests.RequestException:
            raise GarminPersonalError('network request failed; credentials and server bodies omitted',code='temporary_error',stage=stage) from None
        if response.status_code == 429:
            retry_at = self._retry_after(response.headers.get('Retry-After'))
            self.cache['not_before'] = retry_at
            private_json_write(self.cache_path, self.cache)
            raise GarminPersonalError('rate limited; retry after ' + datetime.fromtimestamp(retry_at, timezone.utc).isoformat(), 429, retry_at,code='rate_limited',stage=stage)
        if response.status_code != 200:
            if stage=='token' and response.status_code in (400,401,403):
                checked=datetime.fromtimestamp(self.clock(),timezone.utc).isoformat()
                self.cache.pop('access_token',None)
                self.cache['expires_at']=0
                self.cache['credential_rejection']={'status_code':response.status_code,'checked_at':checked}
                private_json_write(self.cache_path,self.cache)
                raise GarminPersonalError('INPUT REQUIRED: Garmin rejected the Personal API key exchange. The key may be expired, revoked, invalid, or restricted. Verify your Garmin Personal API key and access settings.',response.status_code,code='key_rejected',stage=stage,action_required=True,checked_at=checked)
            if response.status_code == 401 and method == 'GET':
                self.cache.pop('access_token', None)
                self.cache['expires_at'] = 0
                private_json_write(self.cache_path, self.cache)
            if stage=='data' and response.status_code==403:
                raise GarminPersonalError('Garmin refused permission to this data endpoint; this does not establish that the Personal API key expired.',403,code='permission_denied',stage=stage,action_required=True)
            raise GarminPersonalError('request rejected; check access settings or try a later run', response.status_code,stage=stage)
        if stage=='data':self.auth_verified=True
        return response

    def access_token(self, force_refresh=False):
        rejection=self.cache.get('credential_rejection')
        access_rejection=self.cache.get('access_rejection')
        if not force_refresh and rejection:
            raise GarminPersonalError('INPUT REQUIRED: the configured Personal API key was previously rejected. Replace or verify it, then explicitly retry authentication; automatic exchange attempts are paused.',rejection.get('status_code'),code='key_rejected',stage='token',action_required=True,checked_at=rejection.get('checked_at'))
        if not force_refresh and access_rejection:
            raise GarminPersonalError('INPUT REQUIRED: Garmin rejected a newly issued access token. Verify account/API access, then explicitly refresh and sync again.',401,code='access_token_rejected',action_required=True,checked_at=access_rejection.get('checked_at'))
        if not force_refresh and self.cache.get('fingerprint') == self.fingerprint:
            expiry = self.cache.get('expires_at')
            token = self.cache.get('access_token')
            if isinstance(expiry, (int, float)) and math.isfinite(expiry) and expiry - self.clock() > 300 and isinstance(token, str) and token.strip():
                self._token_from_cache=True
                return token
        response = self._request('POST', TOKEN_PATH, {'Authorization': 'ApiKey ' + self._key, 'Accept': '*/*'})
        # Garmin's official reference client documents a raw JWT response.
        token = response.text.strip()
        try:
            encoded = token.split('.')[1]
            payload = json.loads(base64.urlsafe_b64decode(encoded + '=' * (-len(encoded) % 4)))
            expiry = float(payload['exp'])
            if not math.isfinite(expiry) or expiry <= self.clock(): raise ValueError()
        except (ValueError, KeyError, TypeError, IndexError):
            raise GarminPersonalError('token response was invalid; no token was logged') from None
        self.cache = {'fingerprint': self.fingerprint, 'access_token': token,
                      'expires_at': min(expiry, self.clock() + 24 * 60 * 60)}
        private_json_write(self.cache_path, self.cache)
        self._token_from_cache=False
        return token

    def _get_json(self, path, params=None):
        token=self.access_token()
        was_cached=self._token_from_cache
        try:
            response=self._request('GET',path,{'Authorization':'Bearer '+token,'Accept':'application/json'},params=params)
        except GarminPersonalError as error:
            if error.status_code!=401 or error.stage!='data':raise
            if was_cached:
                # Exactly one renewal and one replay, both counted by the same
                # per-run ceiling and rate-limit cooldown. Never loop on denial.
                token=self.access_token(force_refresh=True)
                try:
                    response=self._request('GET',path,{'Authorization':'Bearer '+token,'Accept':'application/json'},params=params)
                except GarminPersonalError as renewed_error:
                    if renewed_error.status_code!=401:raise
                    response=None
            else:response=None
            if response is None:
                checked=datetime.fromtimestamp(self.clock(),timezone.utc).isoformat()
                self.cache['access_rejection']={'checked_at':checked}
                private_json_write(self.cache_path,self.cache)
                raise GarminPersonalError('INPUT REQUIRED: Garmin rejected a newly issued access token. Verify account/API permissions; this is not proof of Personal API key expiry.',401,code='access_token_rejected',action_required=True,checked_at=checked)
        try:
            return response.json()
        except ValueError:
            raise GarminPersonalError('data response was not JSON') from None

    def activities(self, start_date, end_date, start=0, limit=50):
        params={'endDate':end_date,'start':start,'limit':limit,
                'sortBy':'startLocal','sortOrder':'asc','excludeChildren':'true'}
        if start_date is not None:params['startDate']=start_date
        data = self._get_json(ACTIVITIES_PATH,params)
        if not isinstance(data, list): raise GarminPersonalError('activity search response was not an array')
        return data

    def activity(self, activity_id):
        if type(activity_id) is not int or activity_id <= 0: raise ValueError('Invalid activity ID.')
        data = self._get_json('/activity-service/activity/' + str(activity_id))
        if not isinstance(data, dict): raise GarminPersonalError('activity detail response was not an object')
        return data

    def heart_rate_zones(self):
        data = self._get_json('/biometric-service/heartRateZones/v2', {'generateDefaults': 'false'})
        if not isinstance(data, list): raise GarminPersonalError('heart-rate configuration was not an array')
        return data
