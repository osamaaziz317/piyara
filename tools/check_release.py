#!/usr/bin/env python3
"""Read-only source/artifact checks; runtime probing uses only in-memory SQLite."""

import argparse
import json
from pathlib import Path
import sys


sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packaging"))
sys.path.insert(0, str(ROOT))
from release_support import ReleaseError, audit_bundle, runtime_report, source_inventory


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--runtime", action="store_true", help="Also open/close Tk and check in-memory SQLite")
    parser.add_argument("--windows", action="store_true", help="Require native Windows x64")
    parser.add_argument("--linux", action="store_true", help="Require native Linux x86_64, or inspect a Linux bundle")
    parser.add_argument("--toolchain", action="store_true", help="Require an isolated build venv and the pinned PyInstaller")
    parser.add_argument("--bundle", type=Path, help="Inspect a completed portable folder without running its executables")
    args = parser.parse_args(argv)
    if args.windows and args.linux:
        parser.error("Select one target platform")
    try:
        if args.bundle:
            if args.runtime or args.windows or args.toolchain:
                parser.error("--bundle cannot be combined with runtime/toolchain checks")
            if args.linux:
                from linux_release import audit_bundle as audit_linux_bundle
                result = {"bundle_files": audit_linux_bundle(args.bundle), "production_ready": False}
            else:
                result = {"bundle_files": audit_bundle(args.bundle), "production_ready": False}
        else:
            result = {"source": source_inventory(args.root)}
            if args.runtime or args.windows or args.linux or args.toolchain:
                if args.linux:
                    from linux_release import runtime_report as linux_runtime_report
                    result["runtime"] = linux_runtime_report(toolchain=args.toolchain)
                else:
                    result["runtime"] = runtime_report(windows=args.windows, toolchain=args.toolchain)
                if result["runtime"]["application_version"] != result["source"]["application_version"]:
                    raise ReleaseError("The runtime source version differs from the reviewed source tree.")
                if args.toolchain and result["runtime"]["pyinstaller"] != result["source"]["pyinstaller_required"]:
                    raise ReleaseError("The installed PyInstaller version differs from the exact build pin.")
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    except (OSError, ValueError, RuntimeError) as error:
        print("Release check failed: " + str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
