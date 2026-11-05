"""Exercise the production container using synthetic Home Assistant ingress traffic."""

import argparse
import subprocess
import uuid


def docker(*args, **kwargs):
    return subprocess.run(["docker", *args], check=True, **kwargs)


def cleanup(*args):
    subprocess.run(["docker", *args], check=False, capture_output=True)


TRUSTED = """
import json
import sys
import time
import urllib.error
import urllib.request

base = 'http://172.30.32.3:8099'
headers = {'X-Ingress-Path': '/api/hassio_ingress/synthetic-session'}
for attempt in range(30):
    try:
        with urllib.request.urlopen(urllib.request.Request(base + '/', headers=headers), timeout=2) as response:
            assert response.status == 200 and response.read()
        break
    except urllib.error.URLError:
        if attempt == 29:
            raise
        time.sleep(1)
with urllib.request.urlopen(urllib.request.Request(base + '/api/status', headers=headers), timeout=5) as response:
    assert response.status == 200
    status = json.load(response)
    cookie = response.headers['Set-Cookie'].split(';', 1)[0]
asset = base + headers['X-Ingress-Path'] + '/assets/coach.js'
with urllib.request.urlopen(urllib.request.Request(asset, headers=headers), timeout=5) as response:
    assert response.status == 200 and response.read()
if sys.argv[1] == 'create':
    assert not status['profile_ready'] and not status['schedule']['enabled']
    writes = {
        '/api/profile': {'name': 'Synthetic Smoke Runner', 'days': ['Monday']},
        '/api/schedule': {'enabled': False, 'time': '03:00', 'timezone': 'Europe/London'},
    }
    for route, values in writes.items():
        request = urllib.request.Request(base + route, data=json.dumps(values).encode(), headers={
            **headers, 'Content-Type': 'application/json', 'Cookie': cookie, 'X-Coach-CSRF': status['csrf'],
        })
        with urllib.request.urlopen(request, timeout=15) as response:
            assert response.status == 200
else:
    assert status['profile_ready'] and not status['schedule']['enabled']
    assert status['schedule']['timezone'] == 'Europe/London'
    with urllib.request.urlopen(urllib.request.Request(base + '/api/profile', headers=headers), timeout=5) as response:
        assert json.load(response)['name'] == 'Synthetic Smoke Runner'

"""

UNTRUSTED = """
import urllib.error
import urllib.request

base = 'http://172.30.32.3:8099'
headers = {'X-Forwarded-For': '172.30.32.2', 'X-Ingress-Path': '/api/hassio_ingress/spoof'}
try:
    urllib.request.urlopen(urllib.request.Request(base + '/', headers=headers), timeout=5)
except urllib.error.HTTPError as error:
    assert error.code == 403, error.code
else:
    raise AssertionError('Untrusted peer was admitted')
"""

PERMISSIONS = """
from pathlib import Path
from zoneinfo import ZoneInfo

workers = []
for proc in Path('/proc').iterdir():
    if not proc.name.isdigit():
        continue
    try:
        arguments = (proc / 'cmdline').read_bytes().split(bytes(1))
    except (FileNotFoundError, PermissionError):
        continue
    if b'app.server' in arguments:
        workers.append(proc)
assert len(workers) == 1, workers
worker = workers[0]
assert (worker / 'status').read_text().split('Uid:')[1].split()[0] == '10001'
assert Path('/data/app').stat().st_uid == 10001
assert Path('/data/app').stat().st_mode & 0o777 == 0o700
assert (worker / 'environ').read_bytes().find(b'SUPERVISOR_TOKEN=') < 0
assert ZoneInfo('Europe/London').key == 'Europe/London'
"""


def run(image, platform):
    identifier = "coach-smoke-" + uuid.uuid4().hex[:12]
    service = identifier + "-service"
    network = identifier + "-network"
    volume = identifier + "-volume"
    platform_flags = ["--platform", platform] if platform else []
    try:
        docker("network", "create", "--internal", "--subnet", "172.30.32.0/24", network)
        docker("volume", "create", volume)
        docker(
            "run", "--detach", "--init", "--name", service, *platform_flags,
            "--network", network, "--ip", "172.30.32.3",
            "--mount", f"type=volume,source={volume},target=/data",
            "--env", "SUPERVISOR_TOKEN=synthetic-must-not-reach-worker",
            image,
        )
        ports = docker(
            "inspect", "--format", "{{json .HostConfig.PortBindings}}", service,
            capture_output=True, text=True,
        )
        assert ports.stdout.strip() in ("{}", "null"), ports.stdout
        for ip, probe in (("172.30.32.2", TRUSTED), ("172.30.32.4", UNTRUSTED)):
            docker(
                "run", "--rm", *platform_flags, "--network", network, "--ip", ip,
                "--entrypoint", "python", image, "-c", probe, "create",
            )
        docker("exec", "--user", "10001:10001", service, "python", "-c", PERMISSIONS)
        docker("restart", service)
        docker(
            "run", "--rm", *platform_flags, "--network", network, "--ip", "172.30.32.2",
            "--entrypoint", "python", image, "-c", TRUSTED, "verify",
        )
        print(f"Container smoke passed for {platform or 'native architecture'}")
    except Exception:
        subprocess.run(["docker", "logs", service], check=False)
        raise
    finally:
        cleanup("rm", "-f", service)
        cleanup("volume", "rm", volume)
        cleanup("network", "rm", network)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image")
    parser.add_argument("--platform", choices=("linux/amd64", "linux/arm64"))
    arguments = parser.parse_args()
    run(arguments.image, arguments.platform)
