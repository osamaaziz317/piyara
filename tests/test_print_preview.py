"""Private browser previews use authorized exporters and the workspace lifetime."""

import os
from pathlib import Path
import stat
import unittest
from unittest.mock import patch

from bifma.print_preview import PreviewFiles
from tests.test_preview_lifecycle import _pump
from tests.test_transactions import TransactionFixture


class PreviewFilesTests(unittest.TestCase):
    def test_unique_private_destinations_are_removed_and_recreated_after_close(self):
        previews = PreviewFiles()
        self.addCleanup(previews.close)
        self.assertIsNone(previews._directory)
        first, second = previews.destination(), previews.destination()
        self.assertNotEqual(first, second)
        self.assertEqual(first.parent, second.parent)
        self.assertFalse(first.exists())
        if os.name == "posix":
            self.assertEqual(stat.S_IMODE(first.parent.stat().st_mode), 0o700)
        first.write_text("Synthetic printable page", encoding="utf-8")
        previews.close()
        previews.close()
        self.assertFalse(first.parent.exists())
        third = previews.destination()
        self.assertNotEqual(first.parent, third.parent)
        self.assertTrue(third.parent.is_dir())


@unittest.skipIf(os.name == "posix" and not os.environ.get("DISPLAY"), "Requires a display")
class PrintPreviewUITests(TransactionFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        from bifma.native_ui import NativeApp
        from bifma.unified_reports import UnifiedReports
        self.errors = []
        self.messages = patch("bifma.native_ui.messagebox.showerror",
                              side_effect=lambda *args, **kwargs: self.errors.append(args))
        self.messages.start()
        self.addCleanup(self.messages.stop)
        self.browser = patch("webbrowser.open", return_value=True)
        self.open_browser = self.browser.start()
        self.addCleanup(self.browser.stop)
        self.app = NativeApp()
        self.addCleanup(self._close)
        self.app.report_callback_exception = lambda kind, value, tb: self.fail(str(value))
        self.app.path, self.app.security = self.path, self.security
        self.app.actor, self.app.company_id = self.actor, self.company
        self.app.reports = UnifiedReports(self.path, security=self.security)
        self.app.register = self.app.reports.register
        self.document = self.post(self.save("purchase", self.trade_values("purchase", rate="17.25")))
        self.app.update()

    def _close(self):
        if not self.app._closed:
            self.app.close()
            _pump(self.app, lambda: self.app._closed)

    def drain(self):
        _pump(self.app, lambda: self.app.future is None)
        self.app.update()

    def preview_path(self):
        from urllib.parse import urlsplit
        from urllib.request import url2pathname
        uri = self.open_browser.call_args.args[0]
        self.assertEqual(urlsplit(uri).scheme, "file")
        return Path(url2pathname(urlsplit(uri).path))

    def test_native_page_is_private_and_removed_on_logout_and_shutdown(self):
        with patch("bifma.document_export.OriginalDocumentExporter") as original, \
                patch("bifma.native_ui.filedialog.asksaveasfilename") as chooser:
            self.app.preview_document(self.document)
            self.drain()
            original.assert_not_called()
            chooser.assert_not_called()
        page = self.preview_path()
        self.assertIn(self.document["document_number"], page.read_text(encoding="utf-8"))
        self.assertIn("Synthetic product", page.read_text(encoding="utf-8"))
        if os.name == "posix":
            self.assertEqual(stat.S_IMODE(page.stat().st_mode), 0o600)
        self.assertEqual(self.open_browser.call_args.kwargs, {"new": 2})
        self.app.logout()
        self.drain()
        self.assertIsNone(self.app.actor)
        self.assertFalse(page.parent.exists())
        self.app.actor = self.security.authenticate("administrator", "Silver lanterns orbit quietly 72")
        self.app.preview_document(self.document)
        self.drain()
        next_page = self.preview_path()
        self.assertTrue(next_page.exists())
        self.assertNotEqual(page.parent, next_page.parent)
        self._close()
        self.assertFalse(next_page.parent.exists())
        self.assertFalse(self.errors)

    def test_original_route_retains_register_and_report_uses_selected_company(self):
        original_document = {"source": "original", "company_id": self.other_company,
                             "kind": "sale", "id": "original-record"}
        def publish(actor, company, kind, identifier, destination):
            destination.write_text("Synthetic original preview", encoding="utf-8")
            return destination
        with patch("bifma.document_export.OriginalDocumentExporter") as original, \
                patch("bifma.document_export.DocumentExporter") as native:
            original.return_value.export_html.side_effect = publish
            self.app.preview_document(original_document)
            self.drain()
            original.assert_called_once_with(self.path, register=self.app.register)
            native.assert_not_called()
            arguments = original.return_value.export_html.call_args.args
            self.assertEqual(arguments[:4], (self.actor, self.other_company, "sale", "original-record"))
            self.assertEqual(arguments[4], self.preview_path())
        self.open_browser.reset_mock()
        filters = {"date_to": "2026-12-31"}
        self.app.preview_report("sub_trial_balance", filters, company_id=self.other_company)
        self.drain()
        page = self.preview_path().read_text(encoding="utf-8")
        self.assertIn("Synthetic C2", page)
        self.assertNotIn("Synthetic C1", page)
        self.assertEqual(filters, {"date_to": "2026-12-31"})
        self.assertFalse(self.errors)

    def test_busy_close_and_revoked_read_do_not_launch_printable_pages(self):
        with patch.object(self.app._print_previews, "destination") as destination, \
                patch.object(self.app, "run") as run:
            for flag, value in (("future", object()), ("close_pending", True), ("_closed", True)):
                previous = getattr(self.app, flag)
                setattr(self.app, flag, value)
                try:
                    self.app.preview_document(self.document)
                    self.app.preview_report("trial_balance", {}, company_id=self.other_company)
                finally:
                    setattr(self.app, flag, previous)
            destination.assert_not_called()
            run.assert_not_called()
        user = self.security.create_user(self.actor, "preview_reader", "Cobalt bridges welcome sunrise 83")
        self.security.set_company_access(self.actor, user.id, [self.company])
        self.security.set_permissions(self.actor, user.id, "purchases", ["read"])
        self.app.actor = self.security.authenticate("preview_reader", "Cobalt bridges welcome sunrise 83")
        self.security.set_permissions(self.actor, user.id, "purchases", [])
        self.app.preview_document(self.document)
        self.drain()
        self.open_browser.assert_not_called()
        self.assertEqual(len(self.errors), 1)
        directory = Path(self.app._print_previews._directory.name)
        self.assertEqual(list(directory.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
