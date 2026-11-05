"""Explicit offline migration; original source files are never overwritten implicitly."""
import argparse
import json
import os
from pathlib import Path
import shutil
import tempfile
from datetime import datetime
from planning_engine import parse_profile, parse_plan, plan_conflicts, render_plan


def atomic_write(path, text):
    path = Path(path)
    temporary=None
    try:
        with tempfile.NamedTemporaryFile(mode='w',encoding='utf-8',dir=path.parent,delete=False) as handle:
            temporary=handle.name
            handle.write(text);handle.flush();os.fsync(handle.fileno())
        os.replace(temporary,path)
        if os.name!='nt':
            directory=os.open(path.parent,os.O_RDONLY)
            try: os.fsync(directory)
            finally: os.close(directory)
    finally:
        if temporary and os.path.exists(temporary): os.unlink(temporary)


def migrate(profile_path, plan_path, output_dir, apply=False):
    profile_text = Path(profile_path).read_text(encoding='utf-8-sig')
    original = Path(plan_path).read_text(encoding='utf-8-sig')
    profile = parse_profile(profile_text)
    plan = parse_plan(original, profile)
    result = {'conflicts': plan_conflicts(original, profile), 'plan': plan, 'applied': apply}
    if apply:
        output = Path(output_dir); output.mkdir(parents=True, exist_ok=True)
        backup = output / ('migration-backup-' + datetime.now().strftime('%Y%m%dT%H%M%S%f'))
        backup.mkdir()
        shutil.copy2(profile_path, backup / 'athlete.md')
        shutil.copy2(plan_path, backup / 'source_training_plan.md')
        for name in ('training_plan.json', 'training_plan.md'):
            destination = output / name
            if destination.exists(): shutil.copy2(destination, backup / name)
        atomic_write(output / 'training_plan.json', json.dumps(plan, indent=2, ensure_ascii=False))
        atomic_write(output / 'training_plan.md', render_plan(plan))
        result['backup'] = str(backup)
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile', default='athlete.md')
    parser.add_argument('--plan', default='training_plan.md')
    parser.add_argument('--output-dir', default='.')
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    print(json.dumps(migrate(args.profile, args.plan, args.output_dir, args.apply), indent=2, ensure_ascii=False))
