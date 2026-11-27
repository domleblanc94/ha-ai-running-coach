"""Narrow subprocess adapter for the immutable standalone coach."""

import json
import os
from pathlib import Path
import sys


def race_operation(workspace, operation):
    """Return a bounded JSON envelope; provider output never reaches app logs."""
    from contextlib import redirect_stdout
    import base64
    try:
        payload = json.load(sys.stdin)
        with redirect_stdout(sys.stderr):
            if operation == 'race_parse':
                from race_gpx import parse_gpx
                raw = base64.b64decode(payload['gpx_base64'], validate=True) if 'gpx_base64' in payload else payload['gpx'].encode('utf-8')
                value = parse_gpx(raw, name=payload['filename'], sport='running')
            elif operation == 'race_read':
                from race_planner import read_saved_race_plan
                value = read_saved_race_plan(workspace, payload['route_hash'])
            elif operation == 'race_export':
                from race_exports import course_fit, course_gpx
                if payload['format'] == 'source':
                    from race_elevation import source_notes
                    data = source_notes(payload['route']).encode('utf-8')
                else:
                    data = course_fit(payload['route'], strategy=payload.get('strategy')) if payload['format'] == 'fit' else course_gpx(payload['route'])
                value = base64.b64encode(data).decode('ascii')
            elif operation == 'race_elevation':
                from race_elevation import enrich_missing_elevations
                value = enrich_missing_elevations(payload['route'], workspace / '.race-elevation-cache', current_route=payload.get('current_route'))
            elif operation == 'race_review':
                from race_planner import run_race_review
                value = run_race_review(workspace, payload['route'], payload['question'],
                                        race_name=payload['race_name'], race_date=payload['race_date'] or None, today=payload['today'])
            else:
                raise ValueError('Unsupported race operation')
        envelope = {'ok': True, 'value': value}
    except Exception as error:
        # Fixed codes only. Raw XML, provider replies and private context are
        # deliberately not returned or logged by the wrapper.
        code = getattr(error, 'code', 'invalid_input' if isinstance(error, ValueError) else 'race_unavailable')
        allowed = {'daily_budget', 'input_too_large', 'provider_auth', 'provider_rate_limit', 'provider_unavailable',
                   'provider_request', 'response_invalid', 'route_discontinuous', 'busy', 'invalid_input', 'race_unavailable',
                   'elevation_unavailable', 'elevation_rate_limit', 'elevation_invalid_response', 'elevation_no_data', 'elevation_too_fragmented', 'elevation_no_improvement'}
        envelope = {'ok': False, 'code': code if code in allowed else 'race_unavailable'}
    print(json.dumps(envelope, allow_nan=False))
    return 0


def chat_operation(workspace):
    """Bounded local dispatch; no provider request or raw exception output."""
    from contextlib import redirect_stdout
    try:
        payload = json.load(sys.stdin)
        with redirect_stdout(sys.stderr):
            from coach_chat_service import CoachChatService
            value = CoachChatService(workspace).dispatch(payload['action'], payload['payload'])
        envelope = {'ok': True, 'value': value}
    except Exception as error:
        allowed = {'invalid_input', 'not_ready', 'busy', 'source_policy', 'stale_proposal', 'not_found', 'conflict', 'unavailable'}
        code = getattr(error, 'code', 'unavailable')
        envelope = {'ok': False, 'code': code if code in allowed else 'unavailable'}
    print(json.dumps(envelope, allow_nan=False))
    return 0


def activity_details_backfill_operation(workspace):
    """Explicit single-activity read from Garmin; never calls a coach/provider."""
    from contextlib import redirect_stdout
    from garmin_personal_api import GarminConfigurationError, GarminPersonalError
    import garmin_sync
    try:
        payload = json.load(sys.stdin)
        if (not isinstance(payload, dict) or set(payload) != {'activity_id'}
                or type(payload['activity_id']) is not int or not 0 < payload['activity_id'] <= 2**63 - 1):
            raise ValueError('Invalid activity ID')
        from cron_daily_update import pipeline_lock
        with redirect_stdout(sys.stderr), pipeline_lock(workspace / '.pipeline.lock'), pipeline_lock(workspace / '.garmin_personal_sync.lock'):
            client = None
            try:
                client = garmin_sync.get_garmin_client()
                client.timeout = 15
                client.max_requests = 8
                value = garmin_sync.backfill_activity_details(payload['activity_id'], client=client)
            except (GarminConfigurationError, GarminPersonalError) as error:
                garmin_sync.record_auth_error(garmin_sync.AUTH_STATUS_FILE, error)
                raise
            finally:
                if client is not None:
                    client.close()
        envelope = {'ok': True, 'value': value}
    except Exception as error:
        allowed = {'missing_key', 'key_rejected', 'access_token_rejected', 'permission_denied',
                   'account_binding_required', 'rate_limited', 'temporary_error'}
        code = getattr(error, 'code', 'invalid_input' if isinstance(error, ValueError) else 'unavailable')
        envelope = {'ok': False, 'code': code if code in allowed | {'invalid_input', 'busy'} else 'unavailable'}
    print(json.dumps(envelope, allow_nan=False))
    return 0


def main():
    workspace = Path(sys.argv[1]).resolve()
    operation = sys.argv[2]
    approval = tuple(sys.argv[3:])
    if approval and (len(approval) != 4 or approval[0] != '--manual-extra-attempt' or approval[2] != '--manual-reason'):
        raise ValueError('Invalid internal coach approval.')
    sys.path.insert(0, str(workspace))
    os.chdir(workspace)
    if operation in ('race_parse', 'race_read', 'race_review', 'race_export', 'race_elevation'):
        if approval:
            raise ValueError('Race planning does not use a manual-extra approval.')
        return race_operation(workspace, operation)
    if operation == 'activity_details_backfill':
        if approval:
            raise ValueError('Activity backfill cannot use coaching request approval.')
        return activity_details_backfill_operation(workspace)
    if operation == 'chat_dispatch':
        if approval:
            raise ValueError('Chat actions cannot use coaching request approval.')
        return chat_operation(workspace)
    if operation == 'render_dashboard':
        if approval:
            raise ValueError('Dashboard refresh cannot use coaching request approval.')
        from datetime import datetime
        from zoneinfo import ZoneInfo
        from cron_daily_update import pipeline_lock
        from generate_dashboard import build_dashboard

        with pipeline_lock(workspace / '.pipeline.lock'), pipeline_lock(workspace / '.coach.lock'):
            build_dashboard(output_html=workspace / 'dashboard-refresh.html',
                            today=datetime.now(ZoneInfo(os.environ.get('TZ', 'UTC'))).date())
        return 0
    if operation == 'validate_profile':
        from planning_engine import parse_profile

        parse_profile(sys.stdin.read())
        return 0
    if operation == 'baseline_feedback':
        from coach_checkin import record_checkin

        report = json.load(sys.stdin)
        record_checkin(workspace / 'ai_coach_master.sqlite', **report)
        return 0
    if operation == 'run_now' and not approval:
        from cron_daily_update import main as daily

        return daily(())
    if operation in ('new_plan', 'learn_new_plan', 'learn_run_now', 'run_now'):
        from cron_daily_update import pipeline_lock, run_pipeline

        arguments = ('--new-plan',) if operation in ('new_plan', 'learn_new_plan') else ()
        arguments += approval
        if operation.startswith('learn_'):
            arguments += (sys.stdin.read(),)
        with pipeline_lock(workspace / '.pipeline.lock'):
            return run_pipeline(arguments)
    raise ValueError('Unknown internal coach operation.')


if __name__ == '__main__':
    sys.exit(main())
