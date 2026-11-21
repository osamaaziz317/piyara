"""Fixed diagnostic boundaries; all fixtures are synthetic and disposable."""

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from bifma import diagnostics


class DiagnosticTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def test_success_is_created_after_private_data_and_worker_cleanup(self):
        seen = []
        def exercise(root):
            seen.append(root)
            (root / "synthetic.sqlite3").write_text("synthetic fixture")
            return [{"step": step, "passed": True} for step in diagnostics.STEPS], {"synthetic": True}
        with patch.object(diagnostics, "_exercise", side_effect=exercise):
            report = diagnostics.run_workflow()
        self.assertFalse(seen[0].exists())
        self.assertTrue(report["temporary_data_removed"])
        self.assertTrue(report["passed"])
        self.assertFalse(report["frozen"])
        self.assertNotIn(str(seen[0]), json.dumps(report))

    def test_failure_cleans_temporary_database_and_returns_no_evidence(self):
        seen = []
        def fail(root):
            seen.append(root)
            (root / "synthetic.sqlite3").write_text("synthetic fixture")
            raise diagnostics.DiagnosticError("fixed step failed")
        with patch.object(diagnostics, "_exercise", side_effect=fail):
            with self.assertRaisesRegex(diagnostics.DiagnosticError, "fixed step failed"):
                diagnostics.run_workflow()
        self.assertFalse(seen[0].exists())

    def test_incomplete_steps_never_produce_success(self):
        with patch.object(diagnostics, "_exercise", return_value=([], {})):
            with self.assertRaisesRegex(diagnostics.DiagnosticError, "every step"):
                diagnostics.run_workflow()

    def test_worker_unraisable_error_is_forwarded_and_blocks_success_after_cleanup(self):
        from concurrent.futures import ThreadPoolExecutor
        import gc
        import sys
        import threading
        import weakref
        seen, forwarded, references = [], [], []
        class BrokenFinalizer:
            def __del__(self):
                raise RuntimeError("synthetic ignored cleanup failure")
        def prior_hook(info):
            forwarded.append((info.exc_type.__name__, str(info.exc_value), threading.get_ident()))
        def exercise(root):
            seen.append(root)
            (root / "synthetic.sqlite3").write_text("synthetic fixture")
            value = BrokenFinalizer()
            value.cycle = value
            references.append(weakref.ref(value))
            retained = [value]
            del value
            def collect():
                retained.clear()
                gc.collect()
            with ThreadPoolExecutor(max_workers=1) as worker:
                worker.submit(collect).result(timeout=5)
            return [{"step": step, "passed": True} for step in diagnostics.STEPS], {}
        with patch.object(sys, "unraisablehook", prior_hook), patch.object(diagnostics, "_exercise", side_effect=exercise):
            with self.assertRaisesRegex(diagnostics.DiagnosticError, "unraisable cleanup error"):
                diagnostics.run_workflow()
            self.assertIs(sys.unraisablehook, prior_hook)
        self.assertFalse(seen[0].exists())
        self.assertEqual(len(forwarded), 1)
        self.assertEqual(forwarded[0][:2], ("RuntimeError", "synthetic ignored cleanup failure"))
        self.assertNotEqual(forwarded[0][2], threading.get_ident())
        self.assertIsNone(references[0]())

    def test_unraisable_hook_restores_when_the_workflow_raises(self):
        import sys
        original = sys.unraisablehook
        with patch.object(diagnostics, "_exercise", side_effect=RuntimeError("synthetic operation failure")):
            with self.assertRaisesRegex(RuntimeError, "synthetic operation failure"):
                diagnostics.run_workflow()
        self.assertIs(sys.unraisablehook, original)

    def test_file_dialog_responses_are_confined_ordered_and_complete(self):
        dialogs = diagnostics._Dialogs(self.root)
        with self.assertRaisesRegex(diagnostics.DiagnosticError, "private directory"):
            dialogs.expect("save", "fixed title", self.root.parent / "outside.sqlite3")
        dialogs.expect("save", "fixed title", self.root / "fixture.sqlite3")
        self.assertEqual(dialogs.choose("save", title="fixed title"), str(self.root / "fixture.sqlite3"))
        with self.assertRaisesRegex(diagnostics.DiagnosticError, "Unexpected file"):
            dialogs.choose("open", title="unexpected")
        dialogs.expect("save", "fixed title", self.root / "other.sqlite3")
        with self.assertRaisesRegex(diagnostics.DiagnosticError, "Unexpected diagnostic"):
            dialogs.choose("open", title="fixed title")

    def test_dialog_functions_restore_even_if_workflow_fails(self):
        from bifma.native_ui import filedialog, messagebox
        original_save, original_error = filedialog.asksaveasfilename, messagebox.showerror
        with self.assertRaisesRegex(RuntimeError, "synthetic failure"):
            with diagnostics._Dialogs(self.root).installed():
                self.assertIsNot(filedialog.asksaveasfilename, original_save)
                raise RuntimeError("synthetic failure")
        self.assertIs(filedialog.asksaveasfilename, original_save)
        self.assertIs(messagebox.showerror, original_error)

    def test_worker_is_joined_before_destroy_even_on_close_failure(self):
        from types import SimpleNamespace
        order = []
        def close():
            order.append("close-request")
            raise RuntimeError("synthetic close failure")
        worker = SimpleNamespace(shutdown=lambda **kwargs: order.append(("join", kwargs)))
        app = SimpleNamespace(close=close, worker=worker, future=object(), _finish_close=lambda: order.append("destroy"))
        with self.assertRaisesRegex(RuntimeError, "synthetic close failure"):
            diagnostics._close_app(app)
        self.assertEqual(order, ["close-request", ("join", {"wait": True, "cancel_futures": True}), "destroy"])

    def test_printed_totals_use_named_table_cells_and_exact_sides(self):
        html = '<table class="report"><tr><td>999</td></tr></table><table class="totals"><tbody>'
        html += '<tr><th scope="row">Closing debit balances</th><td><bdi>54</bdi></td></tr>'
        html += '<tr><th scope="row">Closing credit balances</th><td><bdi>54</bdi></td></tr></tbody></table>'
        expected = {"Closing debit balances": "54", "Closing credit balances": "54"}
        diagnostics._verify_printed_totals(html, expected)
        with self.assertRaisesRegex(diagnostics.DiagnosticError, "Printable report totals"):
            diagnostics._verify_printed_totals(html.replace('<bdi>54</bdi>', '<bdi>0</bdi>', 1), expected)
        with self.assertRaisesRegex(diagnostics.DiagnosticError, "Printable report totals"):
            diagnostics._verify_printed_totals(html + '<table class="totals"><tr><th>Extra total</th><td>0</td></tr></table>', expected)

    @staticmethod
    def city_fixture(factor="1"):
        from decimal import Context, Decimal, localcontext
        with localcontext(Context(prec=256)):
            rows = []
            for debit, credit in ((60, 6), (10, 30), (36, 70)):
                debit, credit = Decimal(debit) * Decimal(factor), Decimal(credit) * Decimal(factor)
                closing = debit - credit
                rows.append({"opening": Decimal(0), "debit": debit, "credit": credit, "closing": closing,
                             "closing_debit": max(closing, Decimal(0)), "closing_credit": max(-closing, Decimal(0))})
            fields = tuple(rows[0])
            totals = {key: sum((row[key] for row in rows), Decimal(0)) for key in fields}
            totals["account_count"] = 3
            groups = [{**{key: sum((row[key] for row in part), Decimal(0)) for key in fields}, "account_count": len(part)}
                      for part in (rows[:2], rows[2:])]
            report = {"rows": rows, "total": 3, "groups": groups, "totals": totals}
            trial = {"rows": rows, "totals": {key: totals[key] for key in fields[:4]}}
            return report, trial

    def test_city_comparison_rejects_netting_within_group_or_grand_total(self):
        report, trial = self.city_fixture()
        diagnostics._verify_city_balances(report, trial)
        report["groups"][0].update(closing_debit=34, closing_credit=0)
        with self.assertRaisesRegex(diagnostics.DiagnosticError, "City group totals"):
            diagnostics._verify_city_balances(report, trial)
        report, trial = self.city_fixture()
        report["totals"].update(closing_debit=0, closing_credit=0)
        with self.assertRaisesRegex(diagnostics.DiagnosticError, "direct trial balance"):
            diagnostics._verify_city_balances(report, trial)

    def test_city_comparison_has_exact_independent_sums_under_low_precision(self):
        from decimal import localcontext
        report, trial = self.city_fixture("12345678901234567890123456789012345678901234567890123456789")
        with localcontext() as context:
            context.prec = 3
            diagnostics._verify_city_balances(report, trial)

    def test_core_checks_cannot_be_disabled_by_optimized_python(self):
        import ast
        source = Path(diagnostics.__file__).read_text(encoding="utf-8")
        self.assertFalse(any(isinstance(node, ast.Assert) for node in ast.walk(ast.parse(source))))
        self.assertNotIn("unittest.mock", source)
        self.assertNotIn("PIL", source)


if __name__ == "__main__":
    unittest.main()
