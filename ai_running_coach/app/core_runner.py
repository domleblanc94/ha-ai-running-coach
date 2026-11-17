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
                value = parse_gpx(payload['gpx'].encode('utf-8'), name=payload['filename'], sport='running')
            elif operation == 'race_read':
                from race_planner import read_saved_race_plan
                value = read_saved_race_plan(workspace, payload['route_hash'])
            elif operation == 'race_export':
                from race_exports import course_fit, course_gpx
                data = course_fit(payload['route'], strategy=payload.get('strategy')) if payload['format'] == 'fit' else course_gpx(payload['route'])
                value = base64.b64encode(data).decode('ascii')
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
                   'provider_request', 'response_invalid', 'route_discontinuous', 'busy', 'invalid_input', 'race_unavailable'}
        envelope = {'ok': False, 'code': code if code in allowed else 'race_unavailable'}
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
    if operation in ('race_parse', 'race_read', 'race_review', 'race_export'):
        if approval:
            raise ValueError('Race planning does not use a manual-extra approval.')
        return race_operation(workspace, operation)
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
