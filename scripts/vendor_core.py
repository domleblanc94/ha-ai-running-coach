"""Verify the vendored release or stage an explicitly selected upstream Git commit."""

import argparse
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import zipfile

APP = Path(__file__).resolve().parents[1] / "ai_running_coach"
UPSTREAM = "https://github.com/domleblanc94/ai-running-coach"
MANIFEST = "release-manifest.json"


def sha256(content):
    return hashlib.sha256(content).hexdigest()


def verify(app):
    core = app / "core"
    pin = json.loads((app / "core-pin.json").read_text(encoding="utf-8"))
    if pin["repository"] != UPSTREAM or not all(
        re.fullmatch(r"[0-9a-f]{40}", pin[field]) for field in ("commit", "tree")
    ):
        raise ValueError("Invalid upstream pin")
    manifest_bytes = (core / MANIFEST).read_bytes()
    if sha256(manifest_bytes) != pin["manifest_sha256"]:
        raise ValueError("Vendored manifest digest differs from upstream pin")
    manifest = json.loads(manifest_bytes)
    expected = {entry["path"]: entry["sha256"] for entry in manifest["files"]}
    paths = sorted(core.rglob("*"))
    if any(path.is_symlink() for path in paths):
        raise ValueError("Vendored tree contains a symlink")
    actual = {path.relative_to(core).as_posix() for path in paths if path.is_file()}
    if actual != set(expected) | {MANIFEST}:
        raise ValueError("Vendored tree paths differ from upstream release manifest")
    for name, digest in expected.items():
        if sha256((core / name).read_bytes()) != digest:
            raise ValueError(f"Vendored file digest differs: {name}")
    print(f"Verified {len(actual)} upstream files at {pin['commit']}")


def git(checkout, *args):
    return subprocess.check_output(["git", "-C", str(checkout), *args])


def stage(checkout, commit, output):
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError("Provide a full upstream Git commit SHA")
    tree = git(checkout, "rev-parse", f"{commit}^{{tree}}").decode().strip()
    archive_bytes = git(checkout, "archive", "--format=zip", commit)
    output.mkdir(parents=True, exist_ok=False)
    core = output / "core"
    core.mkdir()
    with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
        for member in archive.infolist():
            name = PurePosixPath(member.filename)
            mode = member.external_attr >> 16
            if name.is_absolute() or ".." in name.parts or chr(92) in member.filename or stat.S_ISLNK(mode):
                raise ValueError("Unexpected upstream archive path")
            if member.is_dir():
                continue
            target = core.joinpath(*name.parts)
            if not target.resolve().is_relative_to(core.resolve()):
                raise ValueError("Upstream archive entry escapes staging directory")
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("xb") as destination:
                destination.write(archive.read(member))
    pin = {
        "repository": UPSTREAM,
        "commit": commit,
        "tree": tree,
        "manifest_sha256": sha256((core / MANIFEST).read_bytes()),
    }
    (output / "core-pin.json").write_text(json.dumps(pin, indent=2) + chr(10), encoding="utf-8")
    verify(output)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--upstream-checkout", type=Path)
    parser.add_argument("--commit")
    parser.add_argument("--stage", type=Path)
    args = parser.parse_args()
    if args.upstream_checkout and args.commit and args.stage:
        stage(args.upstream_checkout, args.commit, args.stage)
    elif args.upstream_checkout or args.commit or args.stage:
        parser.error("Staging requires --upstream-checkout, --commit and --stage")
    else:
        verify(APP)


if __name__ == "__main__":
    main()
