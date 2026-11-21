"""Portable native launcher with data-free build diagnostics."""

import json
from pathlib import Path
import sys


def main(argv=None):
    arguments = sys.argv[1:] if argv is None else list(argv)
    if "--check-workflow" in arguments:
        if len(arguments) != 3 or arguments[:2] != ["--check-workflow", "--output"] or not arguments[2] or arguments[2].startswith("--"):
            raise ValueError("Use only --check-workflow --output NEW.json; no database, script, scenario or credentials are accepted.")
        output = Path(arguments[2])
        if output.exists() or output.is_symlink():
            raise FileExistsError("Workflow evidence requires a new output file.")
        from bifma.diagnostics import run_workflow
        value = run_workflow()  # Cleanup must finish before any success evidence.
        with output.open("x", encoding="utf-8") as stream:
            stream.write(json.dumps(value, sort_keys=True) + "\n")
        if sys.stdout is not None:
            print(json.dumps(value, sort_keys=True))
        return 0
    output_path = None
    if len(arguments) == 3 and arguments[0] in {"--check-runtime", "--smoke-welcome"} and arguments[1] == "--output":
        output_path = Path(arguments[2])
        arguments = arguments[:1]
    def report(value):
        text = json.dumps(value, sort_keys=True)
        if output_path is not None:
            with output_path.open("x", encoding="utf-8") as stream:
                stream.write(text + "\n")
        if sys.stdout is not None:
            print(text)
    if arguments == ["--check-runtime"]:
        from runtime_probe import check_runtime
        report(check_runtime())
        return 0
    if arguments == ["--smoke-welcome"]:
        from bifma.native_ui import NativeApp
        app = NativeApp()
        try:
            app.update()
            if app.path is not None or app.actor is not None or app.future is not None:
                raise RuntimeError("The native welcome unexpectedly opened data.")
            pending, buttons = [app], []
            while pending:
                widget = pending.pop()
                pending.extend(widget.winfo_children())
                if widget.winfo_class() == "TButton":
                    buttons.append(widget.cget("text"))
            if not {"Open database", "Create new database"}.issubset(buttons):
                raise RuntimeError("The native welcome controls did not render.")
            report({"native_welcome": "passed", "database_opened": False})
        finally:
            app.close()
        return 0
    from bifma.native_ui import main as native_main
    return native_main(arguments)


if __name__ == "__main__":
    raise SystemExit(main())
