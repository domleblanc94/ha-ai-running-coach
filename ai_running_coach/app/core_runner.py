"""Narrow subprocess adapter for the immutable standalone coach."""

import json
import os
from pathlib import Path
import sys


def main():
    workspace = Path(sys.argv[1]).resolve()
    operation = sys.argv[2]
    sys.path.insert(0, str(workspace))
    os.chdir(workspace)
    if operation == 'validate_profile':
        from planning_engine import parse_profile

        parse_profile(sys.stdin.read())
        return 0
    if operation == 'baseline_feedback':
        from coach_checkin import record_checkin

        report = json.load(sys.stdin)
        record_checkin(workspace / 'ai_coach_master.sqlite', **report)
        return 0
    if operation == 'run_now':
        from cron_daily_update import main as daily

        return daily(())
    if operation in ('new_plan', 'learn_new_plan', 'learn_run_now'):
        from cron_daily_update import pipeline_lock, run_pipeline

        arguments = ('--new-plan',) if operation in ('new_plan', 'learn_new_plan') else ()
        if operation.startswith('learn_'):
            arguments += (sys.stdin.read(),)
        with pipeline_lock(workspace / '.pipeline.lock'):
            return run_pipeline(arguments)
    raise ValueError('Unknown internal coach operation.')


if __name__ == '__main__':
    sys.exit(main())
