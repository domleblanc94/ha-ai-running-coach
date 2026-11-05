"""Narrow subprocess adapter for the immutable standalone coach."""

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
    if operation == 'run_now':
        from cron_daily_update import main as daily

        return daily(())
    if operation == 'new_plan':
        from cron_daily_update import pipeline_lock, run_pipeline

        with pipeline_lock(workspace / '.pipeline.lock'):
            return run_pipeline(('--new-plan',))
    raise ValueError('Unknown internal coach operation.')


if __name__ == '__main__':
    sys.exit(main())
