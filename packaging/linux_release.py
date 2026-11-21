"""Linux-specific development artifact guards; Windows rules remain separate."""

from datetime import datetime, timezone
import json
import os
from pathlib import Path, PurePosixPath
import platform
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import time
from types import SimpleNamespace
import zipfile

import release_support as shared


ReleaseError = shared.ReleaseError
REQUIRED_CHECKS = {"application-tests", "packaging-tests", "native-gui-smoke"}
from frozen_checks import FROZEN_CHECKS, validate_report as validate_frozen_report


def runtime_report(*, toolchain=False):
    if sys.platform != "linux" or platform.machine().lower() != "x86_64":
        raise ReleaseError("This recipe requires native Linux x86_64; it does not cross-compile.")
    result = shared.runtime_report(toolchain=toolchain)
    libc = platform.libc_ver()
    if result["bits"] != 64 or libc[0] != "glibc" or not re.fullmatch(r"\d+\.\d+", libc[1]):
        raise ReleaseError("Use 64-bit CPython on a glibc-based Linux system.")
    result["libc"] = {"name": libc[0], "version": libc[1]}
    distribution = platform.freedesktop_os_release()
    result["distribution"] = {key: distribution.get(key) for key in ("ID", "VERSION_ID", "PRETTY_NAME")}
    return result


def guard_linux_analysis(analysis, root, *, runtime_roots=()):
    """Treat PyInstaller SYMLINK records as link metadata, not source paths."""
    filtered = {}
    for category in ("scripts", "pure", "binaries", "datas"):
        rows = []
        for destination, source, kind in getattr(analysis, category):
            if kind == "SYMLINK":
                shared._safe_name(destination)
                shared._safe_name(source)
                if PurePosixPath(source).is_absolute():
                    raise ReleaseError("PyInstaller requested an absolute runtime symlink.")
            else:
                rows.append((destination, source, kind))
        filtered[category] = rows
    shared.guard_analysis(SimpleNamespace(**filtered), root, runtime_roots=runtime_roots)


def _bundle_paths(root):
    if root.is_symlink():
        raise ReleaseError("The Linux bundle root cannot be a symlink.")
    for directory, folders, files in os.walk(root, followlinks=False):
        for name in sorted(folders):
            path = Path(directory) / name
            if path.is_symlink():
                raise ReleaseError("Linux bundle directory symlinks are not supported.")
        for name in sorted(files):
            path = Path(directory) / name
            if path.is_symlink():
                target = os.readlink(path)
                if Path(target).is_absolute():
                    raise ReleaseError("Linux runtime symlinks must be relative.")
                try:
                    resolved = path.resolve(strict=True)
                except (OSError, RuntimeError):
                    raise ReleaseError("A Linux runtime symlink is broken or cyclic.") from None
                if not path.is_relative_to(root / "_internal") or not resolved.is_relative_to(root / "_internal") or not resolved.is_file():
                    raise ReleaseError("A Linux runtime symlink escapes the internal bundle.")
            elif not path.is_file():
                raise ReleaseError("Only regular files and internal runtime symlinks are supported.")
            yield path


def _elf64_x86(path):
    with path.open("rb") as source:
        header = source.read(20)
    return len(header) == 20 and header[:6] == b"\x7fELF\x02\x01" and int.from_bytes(header[18:20], "little") == 62


def audit_bundle(root):
    root = Path(root).absolute()
    if root.is_symlink():
        raise ReleaseError("The Linux bundle root cannot be a symlink.")
    root = root.resolve(strict=True)
    allowed = {"BIFMA_TRADERS", "BIFMA_HISTORY", "_internal", "DEVELOPMENT.txt", "release-manifest.json"}
    if {path.name for path in root.iterdir()} - allowed:
        raise ReleaseError("Unexpected Linux portable artifact content.")
    if not (root / "_internal").is_dir():
        raise ReleaseError("The internal Linux runtime directory is missing.")
    for name in ("BIFMA_TRADERS", "BIFMA_HISTORY"):
        path = root / name
        if path.is_symlink() or not _elf64_x86(path) or not (path.stat().st_mode & 0o111):
            raise ReleaseError("Expected an executable Linux x86_64 ELF launcher.")
    inventory = []
    for path in _bundle_paths(root):
        relative = path.relative_to(root).as_posix()
        shared._safe_name(relative)
        with path.open("rb") as source:
            shared._safe_header(source.read(64))
        if path.suffix.casefold() == ".zip":
            with zipfile.ZipFile(path) as archive:
                if sum(item.file_size for item in archive.infolist()) > 256 * 1024**2:
                    raise ReleaseError("A runtime ZIP exceeds the review size bound.")
                for item in archive.infolist():
                    shared._safe_name(item.filename)
                    if not item.is_dir():
                        with archive.open(item) as content:
                            shared._safe_header(content.read(64))
        row = {"path": relative, "sha256": shared.digest(path), "bytes": path.stat().st_size,
               "mode": oct(stat.S_IMODE(path.lstat().st_mode)), "kind": "symlink" if path.is_symlink() else "file"}
        if path.is_symlink():
            row["target"] = os.readlink(path)
        inventory.append(row)
    names = {Path(row["path"]).name for row in inventory}
    for prefix in ("_tkinter.", "_sqlite3.", "libsqlite3.so", "libtk8", "libtcl8"):
        if not any(name.startswith(prefix) and ".so" in name for name in names):
            raise ReleaseError("Required Linux runtime library is missing: " + prefix)
    return sorted(inventory, key=lambda row: row["path"])


def binary_baseline(bundle):
    if shutil.which("readelf") is None:
        raise ReleaseError("readelf is required to record the Linux binary baseline.")
    versions, count = set(), 0
    for path in _bundle_paths(bundle):
        if path.is_symlink() or not _elf64_x86(path):
            continue
        result = subprocess.run(["readelf", "--version-info", "--wide", str(path)], capture_output=True, text=True, timeout=30)
        if result.returncode:
            raise ReleaseError("ELF version inspection failed.")
        count += 1
        versions.update(re.findall(r"Name: GLIBC_(\d+\.\d+(?:\.\d+)?)\b", result.stdout))
    if not versions:
        raise ReleaseError("No GNU libc symbol requirements were found.")
    minimum = max(versions, key=lambda value: tuple(int(part) for part in value.split(".")))
    return {"elf_files_checked": count, "glibc_symbol_requirement": minimum,
            "scope": "symbol requirement only; target distribution, display, fonts and printers still require qualification"}


def verification_inventory(root):
    names = {path.relative_to(root).as_posix() for path in (root / "tests").rglob("*.py")}
    names |= {"tools/smoke_native_ui.py", "tools/recovery_oracle.py", "tools/stage_recovery.py",
              "docs/legacy_schema.json"}
    return [{"path": name, "sha256": shared.digest(shared._source_path(root, name))} for name in sorted(names)]


def run_checks(root, logs):
    specifications = [
        ("application-tests", ["-m", "unittest", "discover", "-s", "tests", "-v"]),
        ("packaging-tests", ["-m", "unittest", "discover", "-s", "packaging", "-p", "test_*.py", "-v"]),
        ("native-gui-smoke", ["-m", "tools.smoke_native_ui"]),
    ]
    results = []
    for name, arguments in specifications:
        started = time.monotonic()
        completed = subprocess.run([sys.executable, "-B", "-E", "-s", *arguments], cwd=root,
                                   capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600)
        output = completed.stdout + completed.stderr
        log = logs / (name + ".log")
        with log.open("x", encoding="utf-8") as stream:
            stream.write(output)
        count = re.search(r"Ran (\d+) tests?", output)
        if completed.returncode or (name.endswith("tests") and (count is None or int(count[1]) == 0)):
            raise ReleaseError("Required Linux release check failed: " + name + "; see its private build log.")
        result = {"check": name, "passed": True, "tests_run": int(count[1]) if count else None,
                  "seconds": round(time.monotonic() - started, 3), "log_sha256": shared.digest(log)}
        results.append(result)
        print(json.dumps(result), flush=True)
    return results


def finish_release(bundle, staged, destination, frozen_report):
    if destination.exists() or destination.is_symlink():
        raise FileExistsError("Linux release output must use a new directory.")
    receipt = shared.verify_staged_source(staged)
    frozen = shared._json(frozen_report)
    for runtime in (frozen.get("history_runtime", {}), frozen.get("native_runtime", {})):
        for key in ("application_version", "python_version", "sqlite_version", "tk_version", "platform", "machine", "bits"):
            if runtime.get(key) != receipt["runtime"].get(key):
                raise ReleaseError("Frozen Linux runtime differs from checked build: " + key)
    if receipt["runtime"]["platform"] != "linux" or receipt["runtime"]["machine"] != "x86_64" or receipt["runtime"]["bits"] != 64:
        raise ReleaseError("Only a checked Linux x86_64 bundle can be archived.")
    if {item["check"] for item in receipt.get("checks", []) if item.get("passed")} != REQUIRED_CHECKS:
        raise ReleaseError("Required Linux release checks are incomplete.")
    if {item["check"] for item in frozen.get("checks", []) if item.get("passed")} != FROZEN_CHECKS:
        raise ReleaseError("Required frozen Linux checks are incomplete.")
    version = receipt["source"]["application_version"]
    validate_frozen_report(frozen, version)
    if ".dev" not in version:
        raise ReleaseError("This recipe publishes development versions only.")
    audit_bundle(bundle)
    baseline = binary_baseline(bundle)
    warning = ("BIFMA TRADERS " + version + " — DEVELOPMENT BUILD\n\n"
               "Not production ready. Use synthetic/disposable data for evaluation.\n"
               "Linux x86_64, glibc-based distributions only. The build baseline and\n"
               "checks are in release-manifest.json. Original workflow/print parity,\n"
               "target fonts/printers, Windows execution and production sign-off remain pending.\n"
               "No business database, legacy export, password or backup is included.\n"
               "Run ./BIFMA_TRADERS for native setup; run ./BIFMA_HISTORY --database PATH\n"
               "only with an explicitly chosen external recovered archive.\n")
    with (bundle / "DEVELOPMENT.txt").open("x", encoding="utf-8") as stream:
        stream.write(warning)
    inventory = audit_bundle(bundle)
    report = {"format_version": 1, "application_version": version, "target": "linux-x86_64",
              "release_channel": "DEVELOPMENT", "production_ready": False, "target_acceptance": "pending",
              "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
              "source": receipt["source"], "verification_inputs": receipt.get("verification_inputs", []),
              "build_runtime": receipt["runtime"], "toolchain_wheels": receipt.get("toolchain_wheels", []),
              "checks": receipt["checks"], "frozen_checks": frozen, "linux_baseline": baseline, "bundle_files": inventory}
    shared._write_new_json(bundle / "release-manifest.json", report)
    audit_bundle(bundle)
    destination.mkdir(mode=0o700)
    archive_path = destination / ("BIFMA_TRADERS-" + version + "-linux-x86_64-DEVELOPMENT.tar.gz")
    def clean_metadata(info):
        info.uid = info.gid = 0
        info.uname = info.gname = ""
        return info
    try:
        with tarfile.open(archive_path, "x:gz", dereference=False) as archive:
            archive.add(bundle, arcname="BIFMA_TRADERS", recursive=False, filter=clean_metadata)
            for directory, folders, files in os.walk(bundle, followlinks=False):
                for name in sorted(folders + files):
                    path = Path(directory) / name
                    archive.add(path, arcname="BIFMA_TRADERS/" + path.relative_to(bundle).as_posix(), recursive=False, filter=clean_metadata)
        checksum = shared.digest(archive_path)
        with (destination / (archive_path.name + ".sha256")).open("x", encoding="ascii") as stream:
            stream.write(checksum + "  " + archive_path.name + "\n")
        shutil.copyfile(bundle / "release-manifest.json", destination / "release-manifest.json")
    except BaseException:
        shutil.rmtree(destination)
        raise
    return {"archive": str(archive_path), "sha256": checksum, "production_ready": False}
