"""Original vouchers print through the authorized register on disposable data."""

import hashlib
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch

from bifma.baseline import BaselineError, ensure_baseline
from bifma.database import publish_new_file, session
from bifma.document_export import OriginalDocumentExporter, _render_original
from bifma.document_register import DocumentRegister
from bifma.native_schema import native_session, native_transaction
from bifma.security import AuthorizationError, SecurityService
from bifma.transactions import MODULES, TransactionError
from tests.test_document_export import ParsedDocument
from tests.test_document_register import all_original_modules


class OriginalDocumentExportTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)
        self.source, self.path = self.directory / "original.sqlite3", self.directory / "working.sqlite3"
        all_original_modules(self.source)
        self.label = '<script>sample</script> & نمونہ'
        with session(self.source, readonly=False) as connection:
            connection.execute("UPDATE CONFIG SET CMPNAME=? WHERE CMP_ID='1'", (self.label,))
            connection.execute("UPDATE SMST SET BILLNO='000012',REMARKS=? WHERE TRNID='1'", (self.label,))
            connection.execute("UPDATE JDTL SET NARRATION=?", (self.label,))
        ensure_baseline(self.source, self.path)
        self.security = SecurityService(self.path)
        self.actor = self.security.bootstrap_admin("owner", "Copper orchards welcome rivers 42")
        self.register = DocumentRegister(self.path, self.source, security=self.security)
        self.exporter = OriginalDocumentExporter(self.path, register=self.register)
        with native_session(self.path) as connection:
            self.company = connection.execute("SELECT id FROM companies WHERE code='1'").fetchone()[0]
            self.other = connection.execute("SELECT id FROM companies WHERE code='2'").fetchone()[0]
        self.before = hashlib.sha256(self.source.read_bytes()).hexdigest()

    def export(self, *, actor=None, company=None, kind="sale", identifier="1", name="voucher.html"):
        target = self.directory / name
        self.exporter.export_html(actor or self.actor, company or self.company, kind, identifier, target)
        return target

    def reader(self):
        user = self.security.create_user(self.actor, "reader", "Violet mountains circle sunrise 75")
        self.security.set_company_access(self.actor, user.id, [self.company])
        self.security.set_permissions(self.actor, user.id, "sales", ["read"])
        return user, self.security.authenticate("reader", "Violet mountains circle sunrise 75")

    def test_every_recorded_kind_and_cancelled_unknown_values_print_without_business_writes(self):
        with native_transaction(self.path) as connection:
            connection.execute("UPDATE companies SET name='Renamed working company' WHERE id=?", (self.company,))
        count = 0
        for kind in MODULES:
            for row in self.register.list_documents(self.actor, self.company, kind):
                with self.subTest(kind=kind, identifier=row["id"]):
                    detail = self.register.get(self.actor, self.company, kind, row["id"], source="original")
                    target = self.export(kind=kind, identifier=row["id"], name=f"{kind}-{row['id']}.html")
                    content = target.read_text()
                    parsed = ParsedDocument(content)
                    self.assertNotIn("Renamed working company", content)
                    self.assertIn(self.label, parsed.text)
                    for _, value in detail["fields"]:
                        if value == "":
                            self.assertIn('<bdi dir="auto"></bdi>', content)
                        else:
                            self.assertIn("—" if value is None else str(value), parsed.text)
                    for line in detail["rows"]:
                        for key, _ in detail["columns"]:
                            if line.get(key) == "":
                                self.assertIn('<bdi dir="auto"></bdi>', content)
                            else:
                                self.assertIn("—" if line.get(key) is None else str(line[key]), parsed.text)
                    if detail["status"] == "cancelled":
                        self.assertIn("Cancelled in original records", content)
                    self.assertIn("Content-Security-Policy", content)
                    self.assertFalse(any(tag in {"script", "img", "iframe", "link"} for tag, _ in parsed.elements))
                    if os.name == "posix":
                        self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o600)
                    count += 1
        self.assertEqual(count, 20)
        with native_session(self.path) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM documents").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM stock_movements").fetchone()[0], 0)
        self.assertEqual(hashlib.sha256(self.source.read_bytes()).hexdigest(), self.before)
        self.assertFalse(list(self.directory.glob(".bifma-original-*")))

    def test_company_kind_permissions_and_original_lineage_are_checked(self):
        _, reader = self.reader()
        self.export(actor=reader)
        with self.assertRaises(AuthorizationError):
            self.export(actor=reader, company=self.other, name="other.html")
        with self.assertRaises(AuthorizationError):
            self.export(actor=reader, kind="cash_receipt", name="cash.html")
        with self.assertRaises(TransactionError):
            self.export(kind="sale", identifier="2", name="wrong-kind.html")
        with self.source.open("ab") as stream:
            stream.write(b"changed original")
        with self.assertRaises(BaselineError):
            self.export(name="changed.html")
        self.assertFalse((self.directory / "changed.html").exists())

    def test_original_sale_and_breakage_bills_use_urdu_labels_and_recorded_totals(self):
        for kind in ("sale", "breakage_receive"):
            detail = self.register.get(self.actor, self.company, kind, "1", source="original")
            content = self.export(kind=kind, name=kind + "-bill.html").read_text()
            printable = content.split('<details class="record-details">', 1)[0]
            self.assertIn('class="bt">BT', printable)
            self.assertIn('class="bill-lines"', printable)
            self.assertIn("آئٹم", printable)
            self.assertIn("Recorded party amount", printable)
            self.assertIn('>' + detail["total_amount"] + '</bdi>', printable)
            self.assertNotIn("DC #", printable)
            self.assertNotIn("Original line items", printable)
            self.assertIn(self.label, ParsedDocument(content).text)
        self.assertEqual(hashlib.sha256(self.source.read_bytes()).hexdigest(), self.before)

    def test_original_bill_copies_recorded_packing_and_only_an_actual_dc_link(self):
        source, path = self.directory / "packing-original.sqlite3", self.directory / "packing-working.sqlite3"
        all_original_modules(source)
        with session(source, readonly=False) as connection:
            connection.execute("UPDATE SDTL SET QTY_LARGE_UOM='1',QTY_SMALL_UOM='3',VNO_DCDTL='41' WHERE TRNID='1'")
            connection.execute("INSERT INTO DCMST(TRNID,DCNO,DCDATE,CHCODE,CMP_ID) VALUES('41','000041','2026-01-02','0002','1')")
            connection.execute("INSERT INTO DCDTL(TRNID,VNO,LINE_NUM,STORE_ID,ICODE,QTY_LARGE_UOM,UOM_ID_SMALL,QTY_SMALL_UOM,QTY,RATE) VALUES('41','41','1','1','A','1','1','3','3','10')")
        before = hashlib.sha256(source.read_bytes()).hexdigest()
        ensure_baseline(source, path)
        security = SecurityService(path)
        actor = security.bootstrap_admin("owner", "Copper orchards welcome rivers 42")
        with native_session(path) as connection:
            company = connection.execute("SELECT id FROM companies WHERE code='1'").fetchone()[0]
        exporter = OriginalDocumentExporter(path, archive_path=source, security=security)
        destination = self.directory / "packing-bill.html"
        exporter.export_html(actor, company, "sale", "1", destination)
        printable = destination.read_text().split('<details class="record-details">', 1)[0]
        self.assertIn("DC #", printable)
        self.assertIn('>000041</bdi>', printable)
        self.assertIn('<bdi dir="ltr"><bdi dir="ltr">1</bdi> × <bdi dir="ltr">3</bdi></bdi>', printable)
        self.assertIn("آئٹم", printable)
        self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), before)

    def test_revoked_access_or_changed_archive_during_render_prevents_publication(self):
        user, reader = self.reader()
        def revoke(document, company):
            rendered = _render_original(document, company)
            self.security.set_permissions(self.actor, user.id, "sales", [])
            return rendered
        with patch("bifma.document_export._render_original", side_effect=revoke), self.assertRaises(AuthorizationError):
            self.export(actor=reader, name="revoked.html")
        def alter(document, company):
            rendered = _render_original(document, company)
            with self.source.open("ab") as stream:
                stream.write(b"changed during rendering")
            return rendered
        with patch("bifma.document_export._render_original", side_effect=alter), self.assertRaises(BaselineError):
            self.export(name="changed.html")
        self.assertFalse((self.directory / "revoked.html").exists())
        self.assertFalse((self.directory / "changed.html").exists())
        self.assertFalse(list(self.directory.glob(".bifma-original-*")))

    def test_existing_files_symlinks_races_and_failed_publication_preserve_other_files(self):
        target = self.directory / "voucher.html"
        target.write_text("Keep existing")
        with self.assertRaises(FileExistsError):
            self.export()
        self.assertEqual(target.read_text(), "Keep existing")
        if hasattr(os, "symlink"):
            alias = self.directory / "alias.html"
            alias.symlink_to(target)
            with self.assertRaises(FileExistsError):
                self.export(name="alias.html")
        def race(staged, destination):
            destination.write_text("Concurrent file")
            publish_new_file(staged, destination)
        with patch("bifma.document_export.publish_new_file", side_effect=race), self.assertRaises(FileExistsError):
            self.export(name="race.html")
        self.assertEqual((self.directory / "race.html").read_text(), "Concurrent file")
        def failed(staged, destination):
            publish_new_file(staged, destination)
            raise OSError("Synthetic sync failure")
        with patch("bifma.document_export.publish_new_file", side_effect=failed), self.assertRaises(OSError):
            self.export(name="failed.html")
        self.assertFalse((self.directory / "failed.html").exists())
        self.assertFalse(list(self.directory.glob(".bifma-original-*")))


if __name__ == "__main__":
    unittest.main()
