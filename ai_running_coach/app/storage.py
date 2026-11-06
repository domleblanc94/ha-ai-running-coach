"""Private workspace and atomic state operations for the Home Assistant app."""

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import tempfile


RUNTIME_SCRIPTS = {
    'ai_coach.py', 'coach_checkin.py', 'cron_daily_update.py', 'dashboard_panels.py',
    'dashboard_template.py', 'database_manager.py', 'garmin_personal_api.py',
    'garmin_sync.py', 'generate_dashboard.py', 'health_status.py',
    'migrate_plan.py', 'model_provider.py', 'planning_engine.py', 'publish_if_ready.py',
    'source_policy.py', 'training_calendar.py', 'workout_schema.py',
}


def checked(path):
    path = Path(path)
    if path.is_symlink() or getattr(path, 'is_junction', lambda: False)():
        raise RuntimeError('Links are not allowed in the app data or core paths.')
    return path


def checked_root(path):
    path = Path(os.path.abspath(path))
    for component in (*reversed(path.parents), path):
        checked(component)
    return path


def check_tree(root):
    """Reject links before handing a workspace to the path-bound core."""
    checked(root)
    for directory, dirs, files in os.walk(root, followlinks=False):
        for name in (*dirs, *files):
            checked(Path(directory) / name)


def private_directory(path):
    path = checked(path)
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if not path.is_dir():
        raise RuntimeError('App storage must be a directory.')
    return path


def atomic_write(path, data):
    path = checked(path)
    checked(path.parent)
    fd, temporary = tempfile.mkstemp(prefix='.write-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        if os.name != 'nt':
            parent = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(parent)
            finally:
                os.close(parent)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def atomic_json(path, value):
    atomic_write(path, json.dumps(value, indent=2, allow_nan=False).encode())


class LockBusy(RuntimeError):
    pass


@contextmanager
def file_lock(path, *, blocking=True):
    """An OS lock is released when the worker process exits."""
    with checked(path).open('a+b') as handle:
        if os.name == 'nt':
            import msvcrt

            if not handle.tell():
                handle.write(b'0')
                handle.flush()
            handle.seek(0)
            mode = msvcrt.LK_LOCK if blocking else msvcrt.LK_NBLCK
            try:
                msvcrt.locking(handle.fileno(), mode, 1)
            except OSError as exc:
                raise LockBusy('Another app operation is running.') from exc
        else:
            import fcntl

            mode = fcntl.LOCK_EX if blocking else fcntl.LOCK_EX | fcntl.LOCK_NB
            try:
                fcntl.flock(handle.fileno(), mode)
            except BlockingIOError as exc:
                raise LockBusy('Another app operation is running.') from exc
        yield


def install_core(core, workspace):
    core = checked_root(core)
    manifest_bytes = checked(core / 'release-manifest.json').read_bytes()
    pin = json.loads(checked(core.parent / 'core-pin.json').read_bytes())
    if hashlib.sha256(manifest_bytes).hexdigest() != pin['manifest_sha256']:
        raise RuntimeError('The bundled core manifest does not match its pin.')
    manifest = json.loads(manifest_bytes)
    entries = {entry['path']: entry['sha256'] for entry in manifest['files']}
    assets = {
        name for name in entries
        if name.startswith('dashboard_assets/') and name.count('/') == 1
    }
    names = RUNTIME_SCRIPTS | assets | {'.env.example'}
    verified = {}
    for name in sorted(names):
        source = core / name
        checked(source.parent)
        data = checked(source).read_bytes()
        if hashlib.sha256(data).hexdigest() != entries.get(name):
            raise RuntimeError('A bundled core file does not match its manifest.')
        verified[name] = data
    check_tree(workspace)
    for name, data in verified.items():
        destination = workspace / name
        private_directory(destination.parent)
        if not destination.exists() or destination.read_bytes() != data:
            atomic_write(destination, data)
