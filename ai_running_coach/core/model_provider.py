"""Small, explicit provider boundary. No automatic retries or cross-provider fallback.

Verified API references (2026-11-04):
https://ai.google.dev/api/generate-content
https://developers.openai.com/api/docs/guides/structured-outputs?api-mode=chat
https://docs.ollama.com/api/openai-compatibility
https://platform.claude.com/docs/en/api/messages/create
"""
from dataclasses import dataclass
import hashlib
import os
from types import SimpleNamespace
from urllib.parse import quote, urlsplit

import requests

SUPPORTED_PROVIDERS = ('gemini', 'openai', 'anthropic')
DEFAULT_MODELS = {'gemini': 'gemini-3.5-flash-lite','openai':None,'anthropic':None}
DEFAULT_URLS = {
    'gemini': 'https://generativelanguage.googleapis.com/v1beta',
    'openai': 'https://api.openai.com/v1',
    'anthropic': 'https://api.anthropic.com/v1',
}


class ProviderError(RuntimeError):
    """Sanitized error with a status code usable by the existing retry policy."""
    def __init__(self, provider, message, code=None):
        self.provider = provider
        self.code = code
        super().__init__(f'{provider} API HTTP {code}: {message}' if code else f'{provider} API: {message}')


def provider_name():
    value = os.getenv('COACH_PROVIDER', 'gemini').strip().lower()
    if value not in SUPPORTED_PROVIDERS:
        raise ValueError('COACH_PROVIDER must be gemini, openai or anthropic.')
    return value


def configured_models():
    provider = provider_name()
    models = [item.strip() for item in os.getenv('COACH_MODELS', DEFAULT_MODELS.get(provider) or '').split(',') if item.strip()]
    if not models:
        raise ValueError('Set COACH_MODELS to a model available to your selected provider/account.')
    return models[:2]


def model_advisories(provider,models):
    """Offline lifecycle advice; never changes an explicitly configured model."""
    if provider=='gemini' and any(model.removeprefix('models/')=='gemini-2.5-flash' for model in models):
        return ['Google marks gemini-2.5-flash deprecated and limits access to existing users until December 1, 2026; from that date Google says requests route to gemini-3.5-flash-lite. Review https://aistudio.google.com/docs/models/gemini-2.5-flash and choose an accessible model explicitly. Your saved model choice has not been changed.']
    return []


def configured_base_url(provider=None):
    provider = provider or provider_name()
    value = (os.getenv(provider.upper() + '_BASE_URL', '').strip() or DEFAULT_URLS[provider]).rstrip('/')
    parsed = urlsplit(value)
    if parsed.scheme not in ('http', 'https') or not parsed.netloc or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError('Provider base URL must be an HTTP(S) endpoint without credentials, query or fragment.')
    if parsed.scheme != 'https' and provider != 'openai':
        raise ValueError('Gemini and Anthropic provider endpoints must use HTTPS.')
    return value


def provider_identity():
    """No credentials in cache identity; endpoint changes must invalidate reuse."""
    provider = provider_name()
    endpoint = configured_base_url(provider)
    return {'provider': provider, 'models': configured_models(),
            'endpoint_hash': hashlib.sha256(endpoint.encode()).hexdigest()}


@dataclass
class Usage:
    data: dict
    def model_dump(self, mode='json'):
        return self.data


@dataclass
class ProviderResponse:
    text: str
    usage_metadata: Usage
    finish_reason: str = ''


class ProviderChat:
    def __init__(self, client, model, config):
        self.client = client
        self.model = model
        self.config = dict(config or {})
        self.messages = []

    def send_message(self, message):
        candidate = self.messages + [{'role': 'user', 'content': message}]
        response = self.client.generate_messages(self.model, candidate, self.config, json_mode=False)
        self.messages = candidate + [{'role': 'assistant', 'content': response.text}]
        return response


class ProviderClient:
    def __init__(self, provider=None, session=None):
        self.provider = provider or provider_name()
        if self.provider not in SUPPORTED_PROVIDERS:
            raise ValueError('Unsupported provider.')
        self.base_url = configured_base_url(self.provider)
        self.key = os.getenv(self.provider.upper() + '_API_KEY', '').strip()
        keyless = os.getenv('COACH_ALLOW_KEYLESS', 'false').lower() == 'true'
        custom_openai = self.provider == 'openai' and self.base_url != DEFAULT_URLS['openai']
        if not self.key and not (keyless and custom_openai):
            raise ValueError('Set ' + self.provider.upper() + '_API_KEY, or explicitly enable keyless mode for a custom OpenAI-compatible endpoint.')
        self.timeout = max(5, min(120, int(os.getenv('COACH_HTTP_TIMEOUT_SECONDS', '60'))))
        self.session = session or requests.Session()
        # requests defaults to zero transport retries; make it explicit.
        if session is None:
            self.session.mount('https://', requests.adapters.HTTPAdapter(max_retries=0))
            self.session.mount('http://', requests.adapters.HTTPAdapter(max_retries=0))
        self.models = self
        self.chats = SimpleNamespace(create=lambda model, config=None: ProviderChat(self, model, config))

    def generate_content(self, model, contents, config=None):
        config = dict(config or {})
        return self.generate_messages(model, [{'role': 'user', 'content': contents}], config,
                                      json_mode=config.get('response_mime_type') == 'application/json')

    def _post(self, path, payload, headers):
        try:
            response = self.session.post(self.base_url + path, headers=headers, json=payload,
                                         timeout=(min(10, self.timeout), self.timeout), allow_redirects=False)
        except requests.Timeout:
            raise ProviderError(self.provider, 'request timed out', 504) from None
        except requests.RequestException:
            # Do not expose request URLs, headers, keys or echoed server bodies.
            raise ProviderError(self.provider, 'transport failure') from None
        if response.status_code != 200:
            raise ProviderError(self.provider, 'request rejected or service unavailable', response.status_code)
        try:
            data = response.json()
        except (ValueError, TypeError):
            raise ProviderError(self.provider, 'response was not JSON') from None
        if not isinstance(data, dict):
            raise ProviderError(self.provider, 'unexpected response envelope')
        return data

    def generate_messages(self, model, messages, config, json_mode):
        maximum = int(config.get('max_output_tokens', 4096))
        if not 1 <= maximum <= 8192:
            raise ValueError('Output token budget must be between 1 and 8192.')
        system = config.get('system_instruction', '')
        maximum_input=max(8000,min(120000,int(os.getenv('COACH_MAX_INPUT_CHARS','60000'))))
        if len(system)+sum(len(message['content']) for message in messages)>maximum_input:
            raise ValueError('Conversation exceeds COACH_MAX_INPUT_CHARS; shorten the context before retrying.')
        if json_mode:
            system = (system + '\nReturn only a JSON object, without Markdown fences.').strip()
        headers = {'Content-Type': 'application/json'}
        if self.provider == 'gemini':
            headers['x-goog-api-key'] = self.key
            model_name=model.removeprefix('models/')
            generation = {'maxOutputTokens': maximum}
            # New Gemini releases remove sampling controls. Preserve them only
            # for the older model families that support them.
            if 'temperature' in config and model_name.startswith(('gemini-1.','gemini-2.')):
                generation['temperature']=config['temperature']
            if json_mode:
                generation['responseMimeType'] = 'application/json'
            if model_name in {'gemini-2.5-flash', 'gemini-2.5-flash-lite'}:
                generation['thinkingConfig'] = {'thinkingBudget': 0}
            elif model_name=='gemini-3.5-flash-lite':
                generation['thinkingConfig'] = {'thinkingLevel':'MINIMAL'}
            payload = {'contents': [{'role': 'model' if message['role'] == 'assistant' else 'user',
                                     'parts': [{'text': message['content']}]} for message in messages],
                       'generationConfig': generation}
            if system:
                payload['systemInstruction'] = {'parts': [{'text': system}]}
            data = self._post('/models/' + quote(model.removeprefix('models/'), safe='-._') + ':generateContent', payload, headers)
            candidates = data.get('candidates') or []
            candidate = candidates[0] if candidates else {}
            text = ''.join(part.get('text', '') for part in candidate.get('content', {}).get('parts', []) if not part.get('thought'))
            raw = data.get('usageMetadata') or {}
            counts = (raw.get('promptTokenCount'), raw.get('candidatesTokenCount'), raw.get('totalTokenCount'))
            finish = candidate.get('finishReason', '')
        elif self.provider == 'openai':
            if self.key:
                headers['Authorization'] = 'Bearer ' + self.key
            payload = {'model': model, 'messages': ([{'role': 'system', 'content': system}] if system else []) + messages}
            parameter = os.getenv('OPENAI_TOKEN_PARAMETER', '').strip() or ('max_completion_tokens' if self.base_url == DEFAULT_URLS['openai'] else 'max_tokens')
            if parameter not in ('max_tokens', 'max_completion_tokens'):
                raise ValueError('OPENAI_TOKEN_PARAMETER must be max_tokens or max_completion_tokens.')
            payload[parameter] = maximum
            if json_mode:
                payload['response_format'] = {'type': 'json_object'}
            data = self._post('/chat/completions', payload, headers)
            choice = (data.get('choices') or [{}])[0]
            text = choice.get('message', {}).get('content') or ''
            raw = data.get('usage') or {}
            counts = (raw.get('prompt_tokens'), raw.get('completion_tokens'), raw.get('total_tokens'))
            finish = choice.get('finish_reason', '')
        else:
            headers.update({'x-api-key': self.key, 'anthropic-version': os.getenv('ANTHROPIC_VERSION', '').strip() or '2026-10-21'})
            payload = {'model': model, 'max_tokens': maximum, 'messages': messages}
            if system:
                payload['system'] = system
            data = self._post('/messages', payload, headers)
            text = ''.join(block.get('text', '') for block in data.get('content', []) if block.get('type') == 'text')
            raw = data.get('usage') or {}
            input_count, output_count = raw.get('input_tokens'), raw.get('output_tokens')
            total = input_count + output_count if isinstance(input_count, int) and isinstance(output_count, int) else None
            counts = (input_count, output_count, total)
            finish = data.get('stop_reason', '')
        if not isinstance(text, str):
            raise ProviderError(self.provider, 'text response was missing')
        usage = {'prompt_token_count': counts[0], 'candidates_token_count': counts[1],
                 'total_token_count': counts[2], 'provider_usage': raw, 'provider': self.provider}
        return ProviderResponse(text, Usage(usage), finish)
