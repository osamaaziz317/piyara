"""Portable history launcher: an external archive path is always explicit."""

import argparse
import json
from pathlib import Path


def main(argv=None):
    parser = argparse.ArgumentParser(description="BIFMA TRADERS historical preview (development build)")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--database", type=Path, help="Path to an external recovered SQLite archive")
    mode.add_argument("--check-runtime", action="store_true", help="Check bundled Tk/SQLite without opening business data")
    parser.add_argument("--smoke-test", action="store_true", help="Visit preview screens using the explicitly selected archive")
    args = parser.parse_args(argv)
    if args.check_runtime:
        if args.smoke_test:
            parser.error("--smoke-test requires --database")
        from runtime_probe import check_runtime
        print(json.dumps(check_runtime(), sort_keys=True))
        return 0
    from bifma.preview_ui import main as preview_main
    arguments = ["--database", str(args.database)]
    if args.smoke_test:
        arguments.append("--smoke-test")
    return preview_main(arguments)


if __name__ == "__main__":
    raise SystemExit(main())
