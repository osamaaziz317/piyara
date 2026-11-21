#!/usr/bin/env python3
"""Build a Linux x86_64 DEVELOPMENT bundle using a fresh local virtualenv."""

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
from uuid import uuid4

import linux_release as linux
import release_support as shared


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))  # Unfrozen verification only; PyInstaller uses its isolated staging cwd.


def _new_name(prefix):
    return prefix + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:12]


def _environment(run):
    environment = dict(os.environ)
    for key in ("PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP", "LD_LIBRARY_PATH", "LD_LIBRARY_PATH_ORIG"):
        environment.pop(key, None)
    environment.update(PIP_CACHE_DIR=str(run / "pip-cache"), PYINSTALLER_CONFIG_DIR=str(run / "pyinstaller-cache"),
                       TMPDIR=str(run / "temporary"), PYTHONDONTWRITEBYTECODE="1")
    (run / "temporary").mkdir(exist_ok=True)
    return environment


def _logged(command, log, *, cwd, environment, timeout=600, expected=0):
    with log.open("x", encoding="utf-8") as stream:
        completed = subprocess.run(command, cwd=cwd, env=environment, stdout=stream, stderr=subprocess.STDOUT,
                                   text=True, timeout=timeout)
    if completed.returncode != expected:
        raise shared.ReleaseError("Build command failed; see " + str(log))
    return log.read_text(encoding="utf-8")


def prepare(output):
    output.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(output).free < 2 * 1024**3:
        raise shared.ReleaseError("At least 2 GiB of free space is required for this isolated build.")
    run = output / _new_name("run-")
    run.mkdir(mode=0o700)
    environment = _environment(run)
    python = run / "venv/bin/python"
    _logged([sys.executable, "-B", "-E", "-s", "-m", "venv", str(run / "venv")],
            run / "venv-create.log", cwd=ROOT, environment=environment)
    wheelhouse = run / "wheels"
    wheelhouse.mkdir()
    _logged([str(python), "-B", "-E", "-s", "-m", "pip", "--isolated", "--cache-dir", str(run / "pip-cache"), "download", "--disable-pip-version-check",
             "--no-input", "--only-binary=:all:", "--index-url", "https://pypi.org/simple", "--dest", str(wheelhouse),
             "-r", str(ROOT / "requirements-build.txt")], run / "toolchain-download.log", cwd=ROOT, environment=environment)
    _logged([str(python), "-B", "-E", "-s", "-m", "pip", "--isolated", "--cache-dir", str(run / "pip-cache"), "install", "--disable-pip-version-check",
             "--no-input", "--require-virtualenv", "--no-index", "--find-links", str(wheelhouse),
             "-r", str(ROOT / "requirements-build.txt")], run / "toolchain-install.log", cwd=ROOT, environment=environment)
    wheels = [{"filename": path.name, "sha256": shared.digest(path), "bytes": path.stat().st_size} for path in sorted(wheelhouse.iterdir())]
    shared._write_new_json(run / "prepared.json", {"requirements_sha256": shared.digest(ROOT / "requirements-build.txt"),
                                                  "index": "https://pypi.org/simple", "wheels": wheels})
    print(json.dumps({"prepared_run": str(run), "development_only": True}), flush=True)
    return run


def _prepared(run, output):
    if run.is_symlink() or not run.resolve().is_relative_to(output.resolve()):
        raise shared.ReleaseError("Prepared builds must be inside the selected Linux output directory.")
    run = run.resolve(strict=True)
    metadata = shared._json(run / "prepared.json")
    if metadata["requirements_sha256"] != shared.digest(ROOT / "requirements-build.txt"):
        raise shared.ReleaseError("Build requirements changed since virtualenv preparation.")
    for wheel in metadata["wheels"]:
        if Path(wheel["filename"]).name != wheel["filename"] or shared.digest(run / "wheels" / wheel["filename"]) != wheel["sha256"]:
            raise shared.ReleaseError("Prepared toolchain wheel integrity differs.")
    return run


def _frozen_checks(bundle, attempt, environment):
    import frozen_checks
    def execute(name, arguments, cwd, expected):
        return _logged(arguments, attempt / ("frozen-" + name + ".log"), cwd=cwd,
                       environment=environment, timeout=180, expected=expected)
    return frozen_checks.run_checks(bundle, attempt, ROOT / "docs/legacy_schema.json",
                                    execute=execute, audit_bundle=linux.audit_bundle, windows=False)


def build(run):
    if Path(sys.prefix).resolve() != (run / "venv").resolve() or sys.prefix == sys.base_prefix:
        raise shared.ReleaseError("Build checks must run in this run's isolated virtualenv.")
    environment = _environment(run)
    attempt = run / _new_name("attempt-")
    attempt.mkdir(mode=0o700)
    print(json.dumps({"build_attempt": str(attempt)}), flush=True)
    receipt = {"source": shared.source_inventory(ROOT), "runtime": linux.runtime_report(toolchain=True),
               "verification_inputs": linux.verification_inventory(ROOT),
               "toolchain_wheels": shared._json(run / "prepared.json")["wheels"]}
    if receipt["runtime"]["pyinstaller"] != receipt["source"]["pyinstaller_required"]:
        raise shared.ReleaseError("Installed PyInstaller differs from the exact build pin.")
    receipt["checks"] = linux.run_checks(ROOT, attempt)
    if receipt["source"] != shared.source_inventory(ROOT) or receipt["verification_inputs"] != linux.verification_inventory(ROOT):
        raise shared.ReleaseError("Source or verification inputs changed while checks ran. Start a fresh build attempt.")
    shared._write_new_json(attempt / "preflight.json", receipt)
    staged = shared.stage_source(ROOT, attempt / "source", receipt)
    _logged([sys.executable, "-B", "-I", "-m", "PyInstaller", "--clean", "--distpath", str(attempt / "dist"),
             "--workpath", str(attempt / "pyinstaller-work"), str(staged / "packaging/BIFMA_TRADERS_LINUX.spec")],
            attempt / "pyinstaller.log", cwd=staged, environment=environment)
    bundle = attempt / "dist/BIFMA_TRADERS"
    linux.audit_bundle(bundle)
    frozen = _frozen_checks(bundle, attempt, environment)
    shared._write_new_json(attempt / "frozen-runtime.json", frozen)
    result = linux.finish_release(bundle, staged, attempt / "release", attempt / "frozen-runtime.json")
    print(json.dumps(result, sort_keys=True), flush=True)
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-directory", type=Path, default=ROOT / "build/linux")
    parser.add_argument("--prepare-only", action="store_true", help="Prepare the local venv/wheelhouse before source is frozen")
    parser.add_argument("--prepared-run", type=Path, help="Reuse this recipe's prepared local toolchain; checks always rerun")
    parser.add_argument("--worker-run", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if sys.platform != "linux" or platform.machine().lower() != "x86_64":
        raise shared.ReleaseError("Build this package on native Linux x86_64.")
    os.umask(0o077)
    output = args.output_directory.resolve()
    if args.worker_run:
        return build(_prepared(args.worker_run, output))
    run = _prepared(args.prepared_run, output) if args.prepared_run else prepare(output)
    if args.prepare_only:
        return 0
    return subprocess.run([str(run / "venv/bin/python"), "-B", "-E", "-s", str(Path(__file__).resolve()),
                           "--worker-run", str(run), "--output-directory", str(output)],
                          cwd=ROOT, env=_environment(run)).returncode


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as error:
        print("Linux development build failed: " + str(error), file=sys.stderr)
        raise SystemExit(1)
