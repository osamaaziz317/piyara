"""Mandatory frozen gate and fixed-command contracts; no compiled fixture needed."""

from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import frozen_checks as gates
import native_entry
import release_support as shared


def evidence(version="0.3.0.dev5"):
    return {"schema_version": 1, "scenario": "native-workflow-v2", "application_version": version,
            "frozen": True, "passed": True, "temporary_data_removed": True,
            "steps": [{"step": step, "passed": True} for step in gates.WORKFLOW_STEPS],
            "effects": dict(gates.WORKFLOW_EFFECTS)}


class FrozenGateTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def test_workflow_command_rejects_every_external_input_before_execution(self):
        cases = (["--check-workflow"], ["--check-workflow", "--database", "private.sqlite3"],
                 ["--check-workflow", "--output", "result.json", "--scenario", "anything"],
                 ["--check-workflow", "--output", "result.json", "--script", "external.py"],
                 ["--check-workflow", "--output", "result.json", "--password", "synthetic"],
                 ["--output", "result.json", "--check-workflow"],
                 ["--check-workflow", "--output", "--database"])
        with patch("bifma.diagnostics.run_workflow") as run:
            for arguments in cases:
                with self.subTest(arguments=arguments), self.assertRaises(ValueError):
                    native_entry.main(arguments)
            run.assert_not_called()

    def test_output_is_new_and_not_written_until_successful_cleanup(self):
        output = self.root / "result.json"
        def complete():
            self.assertFalse(output.exists())
            return evidence()
        with patch("bifma.diagnostics.run_workflow", side_effect=complete) as run, redirect_stdout(io.StringIO()):
            self.assertEqual(native_entry.main(["--check-workflow", "--output", str(output)]), 0)
            with self.assertRaises(FileExistsError):
                native_entry.main(["--check-workflow", "--output", str(output)])
            self.assertEqual(run.call_count, 1)
        self.assertEqual(json.loads(output.read_text()), evidence())
        failed = self.root / "failed.json"
        with patch("bifma.diagnostics.run_workflow", side_effect=RuntimeError("cleanup failed")):
            with self.assertRaisesRegex(RuntimeError, "cleanup failed"):
                native_entry.main(["--check-workflow", "--output", str(failed)])
        self.assertFalse(failed.exists())

    def test_dangling_output_symlink_is_not_followed(self):
        output = self.root / "result.json"
        try:
            output.symlink_to(self.root / "absent.json")
        except OSError:
            self.skipTest("This platform does not permit creating symlinks")
        with patch("bifma.diagnostics.run_workflow") as run, self.assertRaises(FileExistsError):
            native_entry.main(["--check-workflow", "--output", str(output)])
        run.assert_not_called()
        self.assertFalse((self.root / "absent.json").exists())

    def test_wrong_effects_steps_cleanup_or_unfrozen_process_block_publication(self):
        good = evidence()
        gates.validate_workflow(good, "0.3.0.dev5")
        variants = ({"frozen": False}, {"temporary_data_removed": False}, {"scenario": "native-workflow-v1"}, {"schema_version": 2},
                    {"steps": good["steps"][:-1]}, {"application_version": "old"},
                    {"steps": [{"step": step, "passed": 1} for step in gates.WORKFLOW_STEPS]},
                    {"schema_version": True},
                    {"steps": good["steps"][1:]},
                    {"effects": {**good["effects"], "widget_variable_lifecycle": False}},
                    {"effects": {**good["effects"], "widget_variable_lifecycle": 1}},
                    {"effects": {**good["effects"], "cash_extra_vouchers": False}},
                    {"effects": {**good["effects"], "cash_extra_vouchers": 1}},
                    {"effects": {**good["effects"], "sale_total": "34.0"}},
                    {"effects": {**good["effects"], "city_closing_debit": "0", "city_closing_credit": "0"}})
        for change in variants:
            with self.subTest(change=change), self.assertRaises(shared.ReleaseError):
                gates.validate_workflow({**good, **change}, "0.3.0.dev5")
        report = {"checks": [{"check": step, "passed": True} for step in gates.FROZEN_CHECKS], "workflow": good}
        gates.validate_report(report, "0.3.0.dev5")
        report["checks"] = [row for row in report["checks"] if row["check"] != "native-workflow"]
        with self.assertRaisesRegex(shared.ReleaseError, "incomplete"):
            gates.validate_report(report, "0.3.0.dev5")

    def test_both_platforms_run_workflow_only_from_relocated_launcher(self):
        for windows in (False, True):
            with self.subTest(windows=windows):
                run = self.root / str(windows)
                run.mkdir()
                bundle = run / "bundle"
                bundle.mkdir()
                (bundle / "synthetic-code.txt").write_text("code-only fixture")
                schema = run / "schema.json"
                schema.write_text(json.dumps({"tables": [{"name": "CONFIG", "columns": [{"name": "CMP_ID"}, {"name": "CMPNAME"}]}]}))
                calls, audits = [], []
                def execute(name, arguments, cwd, expected):
                    calls.append((name, arguments, expected))
                    self.assertEqual(cwd, run / "empty-working-directory")
                    if name in {"native-welcome", "relocated-welcome"}:
                        Path(arguments[-1]).write_text(json.dumps({"native_welcome": "passed", "database_opened": False}))
                    elif name == "native-runtime":
                        Path(arguments[-1]).write_text(json.dumps({"application_version": "0.3.0.dev5"}))
                    elif name == "native-workflow":
                        self.assertIn("relocation with spaces", arguments[0])
                        self.assertEqual(arguments[1:3], ["--check-workflow", "--output"])
                        Path(arguments[-1]).write_text(json.dumps(evidence()))
                    elif name == "history-runtime":
                        return json.dumps({"application_version": "0.3.0.dev5"})
                    elif name == "history-preview":
                        return "Preview smoke test passed: synthetic"
                    elif name == "history-explicit-database":
                        self.assertEqual(expected, 2)
                    return ""
                result = gates.run_checks(bundle, run, schema, execute=execute, audit_bundle=audits.append, windows=windows)
                self.assertEqual({name for name, _, _ in calls}, gates.FROZEN_CHECKS)
                self.assertEqual(len(calls), 7)
                self.assertEqual(result["workflow"], evidence())
                self.assertEqual(audits, [bundle, run / "relocation with spaces/BIFMA_TRADERS"])
                self.assertFalse(list(run.glob("synthetic-history-*")))

    def test_every_recipe_uses_the_shared_frozen_gate(self):
        root = Path(__file__).resolve().parent
        for filename in ("build_linux.py", "build_windows_wine.py"):
            self.assertIn(".run_checks(bundle,", (root / filename).read_text())
        text = (root / "build_windows.ps1").read_text()
        self.assertIn("packaging\\frozen_checks.py", text)
        self.assertNotIn("$HistoryExecutable --check-runtime", text)


if __name__ == "__main__":
    unittest.main()
