"""Translate core diagnostics to fixed messages safe for Ingress and app logs."""

from datetime import datetime
import re


MESSAGES = {
    'activity_sync_failed': 'Activity sync failed. Check Garmin access and wait if the service is limiting requests.',
    'activity_processing_failed': 'The activity database could not be updated. Check app storage before running again.',
    'provider_auth': 'The AI provider rejected access. Check the selected provider key and model in Settings.',
    'provider_request': 'The AI provider rejected the request. Check that the selected model is available to your account and accepts this request.',
    'provider_quota': 'The AI provider limited this request. Check your quota before trying again.',
    'provider_unavailable': 'The AI provider was temporarily unavailable. Try later if your request budget permits.',
    'provider_transport': 'The app could not reach the AI provider. Check Home Assistant network access.',
    'provider_config': 'Check the selected AI provider, model and key in Settings.',
    'provider_truncated': 'The AI response reached its output limit and could not be applied.',
    'response_invalid': 'The AI response did not pass validation. The app kept the saved plan state.',
    'availability_conflict': 'The AI plan scheduled training outside your selected days, so it was not applied. Review your saved training days and generate a new plan when your request allowance permits.',
    'daily_budget': 'The app has used its coaching request allowance for this UTC day. Wait for the next UTC day.',
    'review_budget': 'The review has used its attempts for this UTC day. Wait for the next UTC day.',
    'input_too_large': 'The coaching input is too large. Shorten oversized profile notes before trying again.',
    'profile_invalid': 'The saved profile could not be used. Review the profile and its heart-rate zone entries.',
    'source_provenance': 'The database contains unsupported activity sources. Check the app migration and data-source guidance.',
    'dashboard_render_failed': 'The coach could not render the dashboard. Check the saved profile and app storage.',
    'publication_failed': 'The run finished, but the app could not verify and publish its dashboard. Check app storage.',
    'core_dependency': 'A bundled coach dependency is unavailable. Restart the app or reinstall this version after taking a backup.',
    'workspace_permissions': 'The coach could not read or write its private workspace. Check app storage and permissions.',
    'pipeline_busy': 'Another coach process holds the pipeline lock. Wait for that run to finish.',
    'command_timeout': 'The coach run timed out. Check provider access and app storage before running again.',
    'core_startup_failed': 'The coach process stopped before reporting a pipeline result. Restart the app and check its logs.',
    'pipeline_failed': 'The coach run did not complete. Check the app logs before running again.',
}


TRUNCATION = 'Model output was truncated by its token budget; last-good plan retained.'


def failure_stage(code):
    if code.startswith('garmin_') or code == 'activity_sync_failed':
        return 'sync'
    if code == 'activity_processing_failed':
        return 'activities'
    if code.startswith('provider_') or code in {
        'daily_budget', 'review_budget', 'input_too_large', 'response_invalid',
        'profile_invalid', 'source_provenance', 'availability_conflict',
    }:
        return 'coaching'
    if code == 'dashboard_render_failed':
        return 'render'
    if code == 'publication_failed':
        return 'publication'
    if code in {'core_dependency', 'workspace_permissions', 'pipeline_busy', 'core_startup_failed'}:
        return 'startup'
    return 'unknown'


def current_timestamp(value, started, finished=None):
    try:
        timestamp = datetime.fromisoformat(value)
        return timestamp >= started and (finished is None or timestamp <= finished)
    except (ValueError, TypeError):
        return False


def command_failure(stderr):
    """Recognize pinned core messages without returning raw subprocess output."""
    if not isinstance(stderr, str):
        return None
    known = {
        'Daily coaching API request budget exhausted; pending review retained for a later UTC day.': 'daily_budget',
        'Pending review attempt budget exhausted for this evidence; inspect before explicit retry/reset.': 'review_budget',
        'Coaching input exceeds COACH_MAX_INPUT_CHARS; retained last-good plan. Inspect oversized notes/schedule before retrying.': 'input_too_large',
        'This database contains legacy or unidentified activity provenance and cannot be sent to the coach. Preserve it as an archive, then use setup_coach.py --fresh-garmin-workspace DEST for an isolated official Garmin dataset; do not relabel old records.': 'source_provenance',
        'Profile must specify an explicit Zone 2 before migration/review.': 'profile_invalid',
        'Invalid profile zone boundaries.': 'profile_invalid',
        'Set COACH_MODELS to a model available to your selected provider/account.': 'provider_config',
        'Provider base URL must be an HTTP(S) endpoint without credentials, query or fragment.': 'provider_config',
        'Gemini and Anthropic provider endpoints must use HTTPS.': 'provider_config',
    }
    for provider in ('GEMINI', 'OPENAI', 'ANTHROPIC'):
        known[f'Set {provider}_API_KEY, or explicitly enable keyless mode for a custom OpenAI-compatible endpoint.'] = 'provider_config'
    for line in stderr.splitlines():
        # The pinned pipeline logs a child's error on one of these prefixes.
        # Match its complete fixed error; arbitrary provider content must not
        # turn a request failure into a budget or configuration diagnosis.
        match = re.fullmatch(r'(?:ERROR:root:|\d{4}-\d\d-\d\d \d\d:\d\d:\d\d,\d{3} ERROR )ai_coach\.py failed: (.*)', line)
        if match and match[1] in known:
            return known[match[1]]
        if line == 'RuntimeError: Another daily pipeline is running':
            return 'pipeline_busy'
        if re.fullmatch(r"ModuleNotFoundError: No module named '[A-Za-z0-9_.]+'", line):
            return 'core_dependency'
        if line.startswith('PermissionError: [Errno '):
            return 'workspace_permissions'
    return None


def request_failure(status, error):
    if status == 'invalid':
        if isinstance(error, str) and error.startswith('Training-day conflict: coach scheduled training outside selected days: '):
            return 'availability_conflict'
        return 'provider_truncated' if error == TRUNCATION else 'response_invalid'
    if status != 'failed' or not isinstance(error, str):
        return None
    match = re.match(r'^(?:gemini|openai|anthropic) API HTTP (\d{3}):', error)
    if match:
        number = int(match[1])
        if number in (401, 403):
            return 'provider_auth'
        if number == 429:
            return 'provider_quota'
        if number in (500, 502, 503, 504, 529):
            return 'provider_unavailable'
        if 400 <= number < 500:
            return 'provider_request'
    if error in {f'{provider} API: transport failure' for provider in ('gemini', 'openai', 'anthropic')}:
        return 'provider_transport'
    if error in {
        f'{provider} API: {message}'
        for provider in ('gemini', 'openai', 'anthropic')
        for message in ('response was not JSON', 'unexpected response envelope', 'text response was missing')
    }:
        return 'response_invalid'
    return None
