"""Allowlisted staging and artifact checks for the Windows build recipe.

No business file is a build input. This module has no third-party imports; only
the Windows build environment needs PyInstaller. Do not make packaging/ a Python
package: that would shadow PyInstaller's third-party packaging dependency.
"""

from __future__ import annotations

import argparse
import ast
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path, PurePosixPath
import platform
import re
import shutil
import stat
import subprocess
import sys
import sysconfig
import zipfile


class ReleaseError(ValueError):
    pass


_FORBIDDEN_SUFFIXES = {".db", ".sqlite", ".sqlite3", ".dmp", ".dump", ".bak", ".backup", ".wal", ".shm", ".log", ".par", ".env"}
_FORBIDDEN_NAMES = {"bt-270926.exe", "bifma_traders_v2.py", "credentials.json", "oracle.env", "state.json", ".env", "data", "backups", "credentials", "recoverylogs"}
_ROOT_FILES = {"BIFMA_TRADERS.exe", "BIFMA_HISTORY.exe", "release-manifest.json"}


def digest(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def _json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def _link(path):
    information = path.lstat()
    return path.is_symlink() or bool(getattr(information, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))


def _source_path(root, relative):
    if not isinstance(relative, str) or "\\" in relative:
        raise ReleaseError("Manifest paths must be relative POSIX paths.")
    name = PurePosixPath(relative)
    if name.is_absolute() or not name.parts or any(part in {".", ".."} or ":" in part for part in name.parts) or name.as_posix() != relative:
        raise ReleaseError("Unsafe source manifest path.")
    path = root.joinpath(*name.parts)
    for candidate in (path, *path.parents):
        if candidate == root:
            break
        if _link(candidate):
            raise ReleaseError("Build inputs cannot use symlinks or junctions.")
    if not path.is_file() or not path.resolve().is_relative_to(root):
        raise ReleaseError("A manifest input is missing or outside the source root.")
    return path


def manifest(root):
    value = _json(root / "packaging" / "source_manifest.json")
    if set(value) != {"format_version", "application_files", "build_files", "review_files"} or value["format_version"] != 1:
        raise ReleaseError("Unsupported packaging manifest.")
    names = []
    for field in ("application_files", "build_files", "review_files"):
        if not isinstance(value[field], list) or not value[field]:
            raise ReleaseError("A manifest file list is empty or invalid.")
        for name in value[field]:
            _source_path(root, name)
            if field == "application_files" and not (
                name in {"BIFMA_TRADERS.py", "BIFMA_TRADERS_V3.py", "packaging/history_entry.py", "packaging/native_entry.py", "packaging/runtime_probe.py"}
                or re.fullmatch(r"bifma/[a-z_][a-z0-9_]*\.py", name)
            ):
                raise ReleaseError("Only reviewed application Python code can enter the bundle.")
            names.append(name)
    if len(names) != len(set(names)):
        raise ReleaseError("The manifest contains duplicate input paths.")
    actual = {path.relative_to(root).as_posix() for path in (root / "bifma").rglob("*.py")}
    listed = {name for name in value["application_files"] if name.startswith("bifma/")}
    if actual != listed:
        raise ReleaseError("The bifma module set changed; review the explicit application allowlist.")
    return value


def application_version(root):
    tree = ast.parse((root / "bifma" / "__init__.py").read_text(encoding="utf-8"))
    values = [ast.literal_eval(node.value) for node in tree.body if isinstance(node, ast.Assign)
              and any(isinstance(target, ast.Name) and target.id == "__version__" for target in node.targets)]
    if len(values) != 1 or not isinstance(values[0], str) or not re.fullmatch(r"\d+\.\d+\.\d+(?:\.dev\d+)?", values[0]):
        raise ReleaseError("Use a literal major.minor.patch[.devN] application version.")
    parts = tuple(int(value) for value in re.findall(r"\d+", values[0]))
    if any(value > 65535 for value in parts):
        raise ReleaseError("Windows version components must fit 16 bits.")
    return values[0], parts if len(parts) == 4 else (*parts, 0)


def source_inventory(root):
    root = Path(root).resolve(strict=True)
    definition = manifest(root)
    rows = []
    for category in ("application_files", "build_files", "review_files"):
        for relative in definition[category]:
            path = _source_path(root, relative)
            if path.suffix in {".py", ".spec"}:
                tree = ast.parse(path.read_text(encoding="utf-8"), filename=relative)
                compile(tree, relative, "exec")  # Syntax check without imports or .pyc writes.
                if category == "application_files":
                    for node in ast.walk(tree):
                        modules = [alias.name for alias in node.names] if isinstance(node, ast.Import) else (
                            [node.module] if isinstance(node, ast.ImportFrom) and node.module else [])
                        for module in modules:
                            first = module.split(".", 1)[0]
                            if first not in sys.stdlib_module_names | {"bifma", "runtime_probe"}:
                                raise ReleaseError("The application imports an unreviewed non-stdlib module: " + first)
            rows.append({"path": relative, "kind": category, "sha256": digest(path), "bytes": path.stat().st_size})
    requirement = (root / "requirements-build.txt").read_text(encoding="utf-8")
    pins = [line.strip() for line in requirement.splitlines() if line.strip() and not line.lstrip().startswith("#")]
    if len(pins) != 1 or not re.fullmatch(r"PyInstaller==\d+\.\d+\.\d+", pins[0]):
        raise ReleaseError("Build tooling must contain one exact PyInstaller version pin.")
    version, _ = application_version(root)
    return {"format_version": 1, "application_version": version,
            "pyinstaller_required": pins[0].split("==")[1], "files": sorted(rows, key=lambda row: row["path"])}


def runtime_report(*, windows=False, toolchain=False):
    if windows and (sys.platform != "win32" or platform.machine().lower() not in {"amd64", "x86_64"}):
        raise ReleaseError("This recipe requires native Windows x64; it does not cross-compile.")
    if platform.python_implementation() != "CPython" or sysconfig.get_config_var("Py_GIL_DISABLED"):
        raise ReleaseError("Use a regular CPython build; free-threaded builds are not qualified.")
    from runtime_probe import check_runtime
    report = check_runtime()
    if windows and report["bits"] != 64:
        raise ReleaseError("Windows builds require a 64-bit Python interpreter.")
    if toolchain:
        if sys.prefix == sys.base_prefix:
            raise ReleaseError("Build tooling must run inside the fresh virtual environment.")
        report["pyinstaller"] = importlib.metadata.version("PyInstaller")
        report["build_packages"] = sorted(
            [{"name": package.metadata["Name"], "version": package.version}
            for package in importlib.metadata.distributions()
            ], key=lambda item: item["name"].casefold())
    return report


def _write_new_json(path, value):
    with path.open("x", encoding="utf-8", newline="\n") as output:
        json.dump(value, output, indent=2, ensure_ascii=True)
        output.write("\n")


def _version_text(version, numeric, name, description):
    entries = {"CompanyName": "BIFMA TRADERS", "FileDescription": description,
               "FileVersion": version, "InternalName": name,
               "OriginalFilename": name + ".exe", "ProductName": "BIFMA TRADERS",
               "ProductVersion": version}
    strings = ",\n".join("StringStruct(" + repr(key) + ", " + repr(value) + ")" for key, value in entries.items())
    return (
        "VSVersionInfo(ffi=FixedFileInfo("
        f"filevers={numeric!r}, prodvers={numeric!r}, mask=0x3f, flags={2 if '.dev' in version else 0}, "
        "OS=0x40004, fileType=1, subtype=0, date=(0,0)), "
        "kids=[StringFileInfo([StringTable('040904B0',[" + strings + "])]), "
        "VarFileInfo([VarStruct('Translation',[1033,1200])])])\n"
    )


def stage_source(root, destination, receipt):
    root, destination = Path(root).resolve(), Path(destination).absolute()
    inventory = source_inventory(root)
    if receipt.get("source") != inventory:
        raise ReleaseError("Source/recipe files changed after release checks; rerun the checks.")
    if destination.exists() or destination.is_symlink():
        raise FileExistsError("Staging must use a new directory.")
    destination.mkdir()
    try:
        definition = manifest(root)
        for relative in definition["application_files"] + definition["build_files"]:
            source = _source_path(root, relative)
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("xb") as output:
                output.write(source.read_bytes())
        version, numeric = application_version(root)
        for stem, name, description in (
            ("native", "BIFMA_TRADERS", "BIFMA TRADERS native workspace"),
            ("history", "BIFMA_HISTORY", "BIFMA TRADERS historical preview"),
        ):
            (destination / "packaging" / (stem + "-version.txt")).write_text(
                _version_text(version, numeric, name, description), encoding="utf-8")
        _write_new_json(destination / ".release-stage.json", receipt)
        verify_staged_source(destination)
    except BaseException:
        shutil.rmtree(destination)
        raise
    return destination


def verify_staged_source(root):
    receipt = _json(root / ".release-stage.json")
    definition = _json(root / "packaging" / "source_manifest.json")
    copied = set(definition["application_files"] + definition["build_files"])
    expected = {row["path"]: row["sha256"] for row in receipt["source"]["files"]}
    for relative in copied:
        if digest(_source_path(root, relative)) != expected.get(relative):
            raise ReleaseError("A staged source file differs from the tested source.")
    version, numeric = application_version(root)
    if version != receipt["source"]["application_version"]:
        raise ReleaseError("Staged version does not match its source receipt.")
    for stem, name, description in (
        ("native", "BIFMA_TRADERS", "BIFMA TRADERS native workspace"),
        ("history", "BIFMA_HISTORY", "BIFMA TRADERS historical preview"),
    ):
        path = root / "packaging" / (stem + "-version.txt")
        if path.read_text(encoding="utf-8") != _version_text(version, numeric, name, description):
            raise ReleaseError("A Windows version resource differs from the source version.")
    allowed = copied | {".release-stage.json", "packaging/native-version.txt", "packaging/history-version.txt"}
    for path in _files(root):
        relative = path.relative_to(root).as_posix()
        if relative not in allowed:
            raise ReleaseError("The staging tree contains an unreviewed file.")
    return receipt


def _files(root):
    if _link(root):
        raise ReleaseError("Artifact directories cannot be symlinks or junctions.")
    for directory, folders, files in os.walk(root, followlinks=False):
        for name in sorted(folders + files):
            path = Path(directory) / name
            if _link(path):
                raise ReleaseError("Artifact files cannot be symlinks or junctions.")
        for name in sorted(files):
            yield Path(directory) / name


def _safe_name(name):
    path = PurePosixPath(name.replace("\\", "/"))
    if path.is_absolute() or ".." in path.parts or any(":" in part for part in path.parts) or any(
        part.casefold() in _FORBIDDEN_NAMES or Path(part).suffix.casefold() in _FORBIDDEN_SUFFIXES
        or part.casefold().endswith(("-wal", "-shm", "-journal")) for part in path.parts
    ):
        raise ReleaseError("A forbidden data/log/credential path entered the artifact.")


def _safe_header(value):
    if value.startswith(b"SQLite format 3\0") or b"EXPORT:V" in value[:64]:
        raise ReleaseError("A database/export payload entered the artifact.")


def guard_analysis(analysis, root, *, runtime_roots=()):
    """Check PyInstaller's inputs before it embeds any application data."""
    approved = set(_json(root / "packaging" / "source_manifest.json")["application_files"])
    runtime_roots = tuple(Path(path).resolve() for path in runtime_roots)
    for category in ("scripts", "pure", "binaries", "datas"):
        for destination, source, kind in getattr(analysis, category):
            # PyInstaller pure-module labels are dotted Python names, not file
            # names. In particular sqlite3.dump is stdlib code, not a .dump
            # payload. Collected binaries/data keep the strict path check.
            if category == "pure" and (not isinstance(destination, str) or not all(part.isidentifier() for part in destination.split("."))):
                raise ReleaseError("PyInstaller pure modules must use nonempty dotted Python names.")
            _safe_name(destination.replace(".", "/") + ".py" if category == "pure" else destination)
            if not source:
                continue
            source = Path(source).resolve()
            inside = source.is_relative_to(root)
            if inside and (category not in {"scripts", "pure"} or source.relative_to(root).as_posix() not in approved):
                raise ReleaseError("PyInstaller tried to collect non-application project data.")
            if category == "pure" and (destination == "bifma" or destination.startswith("bifma.")) and not inside:
                raise ReleaseError("A bifma module was imported outside the reviewed staging tree.")
            if not inside and not any(source.is_relative_to(path) for path in runtime_roots):
                raise ReleaseError("PyInstaller input is outside the approved runtime locations.")


def audit_bundle(root):
    root = Path(root).absolute()
    if _link(root):
        raise ReleaseError("The bundle root cannot be a symlink or junction.")
    root = root.resolve(strict=True)
    if {path.name for path in root.iterdir()} - (_ROOT_FILES | {"_internal"}):
        raise ReleaseError("Unexpected top-level portable artifact content.")
    for name in ("BIFMA_TRADERS.exe", "BIFMA_HISTORY.exe"):
        with (root / name).open("rb") as executable:
            if executable.read(2) != b"MZ":
                raise ReleaseError("The artifact is missing a Windows executable.")
    rows = []
    for path in _files(root):
        relative = path.relative_to(root).as_posix()
        _safe_name(relative)
        with path.open("rb") as source:
            header = source.read(64)
        _safe_header(header)
        if path.suffix.casefold() == ".zip":
            with zipfile.ZipFile(path) as archive:
                if sum(entry.file_size for entry in archive.infolist()) > 256 * 1024**2:
                    raise ReleaseError("A runtime ZIP exceeds the review size bound.")
                for entry in archive.infolist():
                    _safe_name(entry.filename)
                    if not entry.is_dir():
                        with archive.open(entry) as value:
                            _safe_header(value.read(64))
        rows.append({"path": relative, "sha256": digest(path), "bytes": path.stat().st_size})
    names = {Path(row["path"]).name.casefold() for row in rows}
    for required in ("_tkinter.pyd", "_sqlite3.pyd", "sqlite3.dll"):
        if required not in names:
            raise ReleaseError("Required bundled runtime file is missing: " + required)
    if not any(name.startswith("tk") and name.endswith(".dll") for name in names) or not any(name.startswith("tcl") and name.endswith(".dll") for name in names):
        raise ReleaseError("Bundled Tcl/Tk libraries are missing.")
    return sorted(rows, key=lambda row: row["path"])


def run_required_checks(root, log_directory):
    specifications = [
        ("application-tests", ["-m", "unittest", "discover", "-s", "tests", "-v"]),
        ("packaging-tests", ["-m", "unittest", "discover", "-s", "packaging", "-p", "test_release.py", "-v"]),
        ("frozen-packaging-tests", ["-m", "unittest", "discover", "-s", "packaging", "-p", "test_frozen_checks.py", "-v"]),
        ("native-gui-smoke", ["-m", "tools.smoke_native_ui"]),
    ]
    results = []
    for name, arguments in specifications:
        completed = subprocess.run([sys.executable, "-B", "-E", "-s", *arguments], cwd=root,
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace")
        with (log_directory / (name + ".log")).open("x", encoding="utf-8") as output:
            output.write(completed.stdout)
        print(completed.stdout, end="")
        count = re.search(r"Ran (\d+) tests?", completed.stdout)
        if completed.returncode or (name.endswith("tests") and (count is None or int(count[1]) == 0)):
            raise ReleaseError("Required release check failed: " + name)
        results.append({"check": name, "passed": True, "tests_run": int(count[1]) if count else None})
    extra = results.pop(2)
    results[1]["additional_checks"] = [extra]
    results[1]["tests_run"] += extra["tests_run"]
    return results


def finish_release(bundle, staged, destination, frozen_report, *, archive_tag=""):
    if archive_tag not in {"", "-DEVELOPMENT-WINE"}:
        raise ReleaseError("Unsupported release archive tag.")
    if destination.exists() or destination.is_symlink():
        raise FileExistsError("Release output must use a new directory.")
    receipt = verify_staged_source(staged)
    frozen = _json(frozen_report)
    from frozen_checks import validate_report
    for runtime in (frozen.get("native_runtime", {}), frozen.get("history_runtime", {})):
        for key in ("application_version", "python_version", "sqlite_version", "tk_version", "platform", "bits"):
            if runtime.get(key) != receipt["runtime"].get(key):
                raise ReleaseError("Frozen runtime does not match the checked build interpreter: " + key)
    validate_report(frozen, receipt["source"]["application_version"])
    if frozen["history_runtime"]["platform"] != "win32" or frozen["history_runtime"]["bits"] != 64:
        raise ReleaseError("Only a checked Windows x64 bundle can be archived.")
    if {item["check"] for item in receipt.get("checks", []) if item["passed"]} != {"application-tests", "packaging-tests", "native-gui-smoke"}:
        raise ReleaseError("The required release checks are incomplete.")
    inventory = audit_bundle(bundle)
    version = receipt["source"]["application_version"]
    report = {"format_version": 1, "application_version": version, "target": "windows-x64",
              "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
              "production_ready": False, "target_acceptance": "pending",
              "source": receipt["source"], "build_runtime": receipt["runtime"],
              "checks": receipt["checks"], "frozen_runtime": frozen, "bundle_files": inventory}
    report.update(release_channel="DEVELOPMENT", native_windows_qualified=False,
                  built_under_wine=receipt.get("built_under_wine", False),
                  verification_inputs=receipt.get("verification_inputs", []),
                  toolchain_wheels=receipt.get("toolchain_wheels", []),
                  build_provenance=receipt.get("build_provenance", {}))
    _write_new_json(bundle / "release-manifest.json", report)
    audit_bundle(bundle)
    destination.mkdir()
    archive_path = destination / ("BIFMA_TRADERS-" + version + "-windows-x64" + archive_tag + ".zip")
    try:
        with zipfile.ZipFile(archive_path, "x", compression=zipfile.ZIP_DEFLATED) as archive:
            for path in _files(bundle):
                archive.write(path, "BIFMA_TRADERS/" + path.relative_to(bundle).as_posix())
        checksum = digest(archive_path)
        with (destination / (archive_path.name + ".sha256")).open("x", encoding="ascii") as output:
            output.write(checksum + "  " + archive_path.name + "\n")
    except BaseException:
        shutil.rmtree(destination)
        raise
    return {"archive": str(archive_path), "sha256": checksum, "production_ready": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    operations = parser.add_subparsers(dest="operation", required=True)
    stage = operations.add_parser("stage")
    stage.add_argument("--root", type=Path, required=True)
    stage.add_argument("--destination", type=Path, required=True)
    stage.add_argument("--receipt", type=Path, required=True)
    finish = operations.add_parser("finish")
    finish.add_argument("--bundle", type=Path, required=True)
    finish.add_argument("--staged", type=Path, required=True)
    finish.add_argument("--destination", type=Path, required=True)
    finish.add_argument("--frozen-report", type=Path, required=True)
    args = parser.parse_args()
    if sys.platform != "win32":
        raise ReleaseError("Release staging/build commands run only on Windows.")
    if args.operation == "stage":
        receipt = _json(args.receipt)
        if receipt["source"] != source_inventory(args.root):
            raise ReleaseError("Source changed since preflight.")
        if receipt["runtime"]["pyinstaller"] != receipt["source"]["pyinstaller_required"]:
            raise ReleaseError("The installed PyInstaller version does not match its exact pin.")
        from wine_release import verification_inventory
        receipt["verification_inputs"] = verification_inventory(args.root)
        receipt["checks"] = run_required_checks(args.root, args.destination.parent)
        if receipt["verification_inputs"] != verification_inventory(args.root):
            raise ReleaseError("Verification inputs changed during the Windows source checks.")
        stage_source(args.root, args.destination, receipt)
        print(json.dumps({"stage": str(args.destination), "application_version": receipt["source"]["application_version"]}))
    else:
        print(json.dumps(finish_release(args.bundle, args.staged, args.destination, args.frozen_report), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
