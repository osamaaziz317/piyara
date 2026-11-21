"""Combined native trade reports use only synthetic, temporary business data."""

from copy import deepcopy
from contextlib import contextmanager
from decimal import Context, Decimal, localcontext
import json
from uuid import uuid4
import unittest
from unittest.mock import patch

from bifma import native_reports
from bifma.native_reports import NativeReports
from bifma.native_schema import native_session, native_transaction
from bifma.security import AuthenticationError, AuthorizationError
from tests.test_transactions import TransactionFixture


class TradeDetailTests(TransactionFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.reports = NativeReports(self.path, security=self.security)

    def report(self, family="purchases", actor=None, company=None, **filters):
        return self.reports.trade_detail(actor or self.actor, company or self.company, family, **filters)

    def invoice(self, kind="purchase", **values):
        return self.post(self.save(kind, self.trade_values(kind, **values)))

    def reader(self, modules):
        user = self.security.create_user(self.actor, "report_reader", "Cobalt evening silver trees 91")
        reader = self.security.authenticate("report_reader", "Cobalt evening silver trees 91")
        self.security.set_company_access(self.actor, user.id, [self.company])
        for module in modules:
            self.security.set_permissions(self.actor, user.id, module, ["read"])
        return user, reader

    def extra_item(self, *, unit=None, store=None, company=None):
        identifier, unit_id = str(uuid4()), unit or str(uuid4())
        with native_transaction(self.path) as connection:
            if unit is None:
                connection.execute("INSERT INTO units(id,code,name) VALUES (?,'BOX','Box')", (unit_id,))
            connection.execute("INSERT INTO products(id,code,name,small_unit_id,sale_account_id,purchase_account_id,inventory_account_id) VALUES (?,'B','Second item',?,?,?,?)",
                               (identifier, unit_id, self.accounts["sales"], self.accounts["purchases"], self.accounts["inventory"]))
            if store:
                connection.execute("INSERT INTO company_stores(company_id,store_id) VALUES (?,?)", (company or self.company, store))
        return identifier, unit_id

    def synthetic(self, connection, template, number, *, sequence=None, when="2026-02-01", snapshot=True, mutate=None):
        """Zero-value historical-native fixture, assembled before posting status."""
        identifier, line_id = str(uuid4()), str(uuid4())
        connection.execute("INSERT INTO documents(id,company_id,kind,document_number,business_date,account_id,total_amount,rounding_policy,posting_places,created_by) VALUES (?,?,'purchase',?,?,?,'0','cents',2,?)",
                           (identifier, self.company, number, when, self.accounts["supplier"], self.actor.user.id))
        connection.execute("INSERT INTO document_lines(id,company_id,document_id,line_number,product_id,store_id,quantity) VALUES (?,?,?,1,?,?,'1')",
                           (line_id, self.company, identifier, self.product, self.store))
        if snapshot:
            saved = deepcopy(template["snapshot"])
            saved["lines"][0]["line_id"] = line_id
            if mutate:
                mutate(saved)
            connection.execute("INSERT INTO document_metadata(document_id,company_id,input_json,snapshot_json,calculation_json) VALUES (?,?,'{}',?,'{}')",
                               (identifier, self.company, json.dumps(saved)))
        connection.execute("UPDATE documents SET status='posted',revision=revision+1,posted_by=?,posted_at=1,posting_sequence=? WHERE id=?", (self.actor.user.id, sequence, identifier))
        return identifier

    def test_sales_and_purchases_returns_and_both_reversal_signs_keep_own_dates(self):
        self.opening(quantity="20")
        for family, kind, returned in (("sales", "sale", "sale_return"), ("purchases", "purchase", "purchase_return")):
            with self.subTest(family=family):
                original = self.invoice(kind, quantity="2", rate="10", when="2026-01-02")
                credit = self.invoice(returned, quantity="2", rate="10", source=original["lines"][0]["id"], when="2026-01-03")
                reversed_return = self.reverse(credit, "2026-01-04")
                reversed_invoice = self.reverse(original, "2026-01-05")
                result = self.report(family)
                self.assertEqual([row["document_id"] for row in result["rows"]], [original["id"], credit["id"], reversed_return["id"], reversed_invoice["id"]])
                self.assertEqual([row["signed_net_amount"] for row in result["rows"]], [20, -20, 20, -20])
                self.assertEqual([row["signed_quantity"] for row in result["rows"]], [2, -2, 2, -2])
                self.assertEqual(result["totals"], {"invoice": 20, "return": -20, "reversal": 0, "net": 0})
                self.assertEqual(result["document_totals"], result["totals"])
                self.assertEqual(result["quantity_totals"][0]["net"], 0)
                self.assertEqual(self.report(family, date_to="2026-01-02")["totals"]["net"], 20)
                self.assertEqual(self.report(family, date_from="2026-01-04", date_to="2026-01-04")["totals"]["net"], 20)
                self.assertEqual(len(self.report(family, kind="invoice")["rows"]), 2)
                self.assertEqual(len(self.report(family, kind="return")["rows"]), 2)
                self.assertEqual(result["rows"][-1]["reverses_document_number"], original["document_number"])

    def test_header_charges_rounding_count_once_and_filtered_lines_have_no_allocated_total(self):
        other_product, other_unit = self.extra_item(store=self.other_store)
        values = self.trade_values("purchase", quantity="2", rate="10", whole=True, discount="1", breakage="0.5", freight="2")
        values["lines"][0]["discount_percent"] = "10"
        values["lines"].append({"product_id": other_product, "store_id": self.other_store, "quantity": "1", "rate": "3.45", "discount_percent": "0"})
        posted = self.post(self.save("purchase", values))
        result = self.report(limit=1)
        self.assertEqual((result["total"], result["document_count"]), (2, 1))
        self.assertEqual(result["totals"]["net"], Decimal("21.45"))
        self.assertEqual(result["document_totals"]["net"], Decimal(posted["total_amount"]))
        self.assertEqual(posted["total_amount"], "22")
        self.assertEqual({row["unit_id"] for row in result["quantity_totals"]}, {self.unit, other_unit})
        for filters, amount in (({"product_id": self.product}, "18"), ({"store_id": self.other_store}, "3.45"), ({"product_id": other_product, "store_id": self.other_store}, "3.45")):
            filtered = self.report(**filters)
            self.assertEqual(filtered["totals"]["net"], Decimal(amount))
            self.assertIsNone(filtered["document_totals"])
        self.assertEqual(self.report(account_id=self.accounts["supplier"])["document_totals"]["net"], 22)

    def test_paging_and_full_export_cross_document_batches_preserve_free_goods(self):
        template = self.invoice(rate="0")
        with native_transaction(self.path) as connection:
            for index in range(501):
                self.synthetic(connection, template, f"FREE-{index:03}", sequence=index + 10)
        first = self.report(search="FREE", limit=500)
        last = self.report(search="FREE", offset=500)
        empty = self.report(search="FREE", offset=900)
        self.assertEqual((len(first["rows"]), len(last["rows"]), len(empty["rows"])), (500, 1, 0))
        self.assertEqual(last["rows"][0]["document_number"], "FREE-500")
        for report in (first, last, empty):
            self.assertEqual((report["total"], report["document_count"]), (501, 501))
            self.assertEqual(report["totals"]["net"], 0)
            self.assertEqual(report["quantity_totals"][0]["net"], 501)
        snapshot = self.reports.export_snapshot(self.actor, self.company, "purchase_detail", search="FREE")
        self.assertEqual(len(snapshot["result"]["rows"]), 501)
        self.assertEqual(snapshot["required_modules"], ("purchases", "purchase_returns"))
        self.assertEqual(snapshot["filters"]["kind"], "all")
        with patch.object(native_reports, "MAX_EXPORT_ROWS", 500), self.assertRaisesRegex(ValueError, "exceeds"):
            self.reports.export_snapshot(self.actor, self.company, "purchase_detail", search="FREE")

    def test_saved_bilingual_labels_and_units_survive_current_master_renames_and_inactivation(self):
        with native_transaction(self.path) as connection:
            connection.execute("UPDATE products SET name_urdu='نمونہ' WHERE id=?", (self.product,))
        original = self.invoice()
        self.reverse(original)
        with native_transaction(self.path) as connection:
            for table, identifier in (("accounts", self.accounts["supplier"]), ("products", self.product), ("stores", self.store), ("units", self.unit)):
                connection.execute(f"UPDATE {table} SET name='Changed',is_active=0 WHERE id=?", (identifier,))
            connection.execute("UPDATE company_stores SET is_active=0 WHERE company_id=?", (self.company,))
        result = self.report(product_id=self.product, store_id=self.store, account_id=self.accounts["supplier"])
        for row in result["rows"]:
            self.assertEqual((row["product_name"], row["product_name_urdu"], row["account_name"], row["store_name"], row["unit_name"]), ("Synthetic product", "نمونہ", "Supplier", "S1", "Piece"))
        selectors = self.reports.trade_selectors(self.actor, self.company, "purchases")
        self.assertEqual(selectors["products"][0]["name"], "Changed")
        self.assertEqual(selectors["products"][0]["is_active"], 0)

    def test_export_cap_stops_row_materialization_at_first_excess_across_batches(self):
        template = self.invoice(rate="0")
        with native_transaction(self.path) as connection:
            for index in range(13):
                self.synthetic(connection, template, f"CAP-{index:02}", sequence=index + 10)
        materialized = []

        @contextmanager
        def traced_transaction(path):
            with native_transaction(path) as connection:
                original = connection.row_factory
                def track(cursor, values):
                    if any(column[0] == "line_number" for column in cursor.description):
                        materialized.append(values)
                    return original(cursor, values)
                connection.row_factory = track
                yield connection

        with patch.object(native_reports, "native_transaction", traced_transaction), patch.object(native_reports, "MAX_EXPORT_ROWS", 10):
            with self.assertRaisesRegex(ValueError, "exceeds"):
                self.reports.export_snapshot(self.actor, self.company, "purchase_detail", search="CAP-")
        # SQLite yields only the allowed ten lines plus the first excess row;
        # the rest of the next batch must not be retained before refusing.
        self.assertEqual(len(materialized), 11)

    def test_saved_unit_groups_remain_separate_and_sort_by_saved_labels(self):
        template = self.invoice(rate="0")
        old_unit = str(uuid4())
        def previous_unit(snapshot):
            product = snapshot["lines"][0]["product"]
            product["small_unit_id"] = old_unit
            product["small_unit"] = {"id": old_unit, "code": "BOX", "name": "Box", "name_urdu": None}
        with native_transaction(self.path) as connection:
            self.synthetic(connection, template, "OLD-UNIT", mutate=previous_unit)
        result = self.report(limit=1)
        self.assertEqual([(row["product_id"], row["unit_code"], row["net"]) for row in result["quantity_totals"]],
                         [(self.product, "BOX", 1), (self.product, "PC", 1)])
        self.assertEqual(result["total"], 2)

    def test_line_precision_is_exact_under_low_ambient_context_and_mixed_rounding(self):
        values = self.trade_values("purchase", quantity="9007199254740993.123456", rate="0.123456")
        values["lines"][0]["discount_percent"] = "0.000001"
        posted = self.post(self.save("purchase", values))
        with localcontext(Context(prec=256)):
            expected = Decimal("9007199254740993.123456") * (Decimal("0.123456") - Decimal("0.123456") * Decimal("0.000001") / 100)
        whole = self.invoice(rate="1.5", whole=True)
        with localcontext(Context(prec=3)):
            result = self.report()
        self.assertEqual(result["rows"][0]["net_amount"], expected)
        with localcontext(Context(prec=256)):
            self.assertEqual(result["totals"]["net"], expected + Decimal("1.5"))
            self.assertEqual(result["document_totals"]["net"], Decimal(posted["total_amount"]) + Decimal(whole["total_amount"]))

    def test_company_posted_kind_and_literal_search_boundaries(self):
        values = self.trade_values("purchase")
        values["document_number"] = "Invoice_%_Straße"
        original = self.post(self.save("purchase", values))
        self.invoice(rate="99")
        self.save("purchase", self.trade_values("purchase", rate="777"))
        self.opening()
        self.invoice("sale", rate="3")
        self.invoice("breakage_receive", rate="5")
        other_product, _ = self.extra_item(unit=self.unit)
        other = self.trade_values("purchase")
        other["lines"][0].update(product_id=other_product, store_id=self.other_store)
        draft = self.service.save_draft(self.actor, self.other_company, "purchase", other)
        self.service.post(self.actor, self.other_company, draft["id"], draft["revision"])
        for term in ("_%_", "STRASSE"):
            result = self.report(search=term)
            self.assertEqual([row["document_id"] for row in result["rows"]], [original["id"]])
        self.assertEqual(self.report()["total"], 2)
        self.assertNotIn(other_product, {row["id"] for row in self.reports.trade_selectors(self.actor, self.company, "purchases")["products"]})
        with self.assertRaises(ValueError):
            self.report(product_id=other_product)

    def test_combined_permission_is_and_and_single_kind_needs_no_stock_or_ledger_grant(self):
        original = self.invoice()
        returned = self.invoice("purchase_return", source=original["lines"][0]["id"])
        user, reader = self.reader(["purchases"])
        self.assertEqual(self.report(actor=reader, kind="invoice")["total"], 1)
        self.assertEqual(len(self.reports.trade_selectors(reader, self.company, "purchases", "invoice")["accounts"]), 1)
        for call in (lambda: self.report(actor=reader), lambda: self.reports.trade_selectors(reader, self.company, "purchases"), lambda: self.reports.export_snapshot(reader, self.company, "purchase_detail")):
            with self.assertRaises(AuthorizationError):
                call()
        self.security.set_permissions(self.actor, user.id, "purchase_returns", ["read"])
        self.assertEqual(self.report(actor=reader)["total"], 2)
        self.security.set_permissions(self.actor, user.id, "purchases", [])
        self.assertEqual(self.report(actor=reader, kind="return")["rows"][0]["document_id"], returned["id"])
        with self.assertRaises(AuthorizationError):
            self.report(actor=reader)
        with self.assertRaises(AuthorizationError):
            self.report(actor=reader, company=self.other_company, kind="return")
        self.security.set_company_access(self.actor, user.id, [])
        with self.assertRaises(AuthorizationError):
            self.report(actor=reader, kind="return")
        self.security.logout(reader)
        with self.assertRaises(AuthenticationError):
            self.report(actor=reader, kind="return")

    def test_missing_inconsistent_and_offpage_snapshots_fail_without_fallback(self):
        template = self.invoice(rate="0")
        mutations = [None, lambda value: value["company"].update(id=self.other_company),
                     lambda value: value["accounts"].clear(), lambda value: value["lines"].clear(),
                     lambda value: value["lines"][0]["product"].update(id=str(uuid4())),
                     lambda value: value["lines"][0]["store"].update(id=self.other_store),
                     lambda value: value["lines"][0]["product"]["small_unit"].update(id=str(uuid4())),
                     lambda value: value["lines"].append(deepcopy(value["lines"][0]))]
        for index, mutate in enumerate(mutations):
            number = f"BAD-{index}-END"
            with native_transaction(self.path) as connection:
                self.synthetic(connection, template, number, snapshot=index != 0, mutate=mutate)
            with self.subTest(index=index), self.assertRaisesRegex(ValueError, "saved|snapshot"):
                self.report(search=number)
        with self.assertRaises(ValueError):
            self.report(limit=1)  # The first displayed row is sound; later rows are not.
        self.assertEqual(self.report(date_to="2026-01-31")["total"], 1)

    def test_null_sequence_fallback_and_line_order_are_deterministic(self):
        template = self.invoice(rate="0")
        values = self.trade_values("purchase", rate="0")
        values["lines"].append({**values["lines"][0], "quantity": "2"})
        multiple = self.post(self.save("purchase", values))
        result = self.report(search=multiple["document_number"], offset=1, limit=1)
        self.assertEqual((result["total"], result["document_count"]), (2, 1))
        self.assertEqual((result["rows"][0]["line_number"], result["rows"][0]["quantity"]), (2, 2))
        self.assertEqual(result["quantity_totals"][0]["net"], 3)
        with native_transaction(self.path) as connection:
            old = self.synthetic(connection, template, "OLD", sequence=None)
            newer = self.synthetic(connection, template, "NEW", sequence=99)
        self.assertEqual([row["document_id"] for row in self.report(date_from="2026-02-01")["rows"]], [old, newer])

    def test_invalid_filters_missing_ids_and_export_pagination_are_rejected(self):
        invalid = [{"family": "sale"}, {"family": []}, {"kind": "reversal"}, {"kind": {}},
                   {"date_from": "2026-02-30"}, {"date_from": "2026-01-02", "date_to": "2026-01-01"},
                   {"offset": True}, {"offset": -1}, {"limit": 0}, {"limit": 501},
                   {"search": "x" * 201}, {"search": []}, {"account_id": ""}, {"product_id": 1},
                   {"store_id": []}, {"account_id": str(uuid4())}, {"product_id": str(uuid4())}, {"store_id": str(uuid4())}]
        for filters in invalid:
            with self.subTest(filters=filters), self.assertRaises(ValueError):
                self.report(**filters)
        for filters in ({"offset": 0}, {"limit": 1}, {"pool": "regular"}, {"tax": "0"}):
            with self.subTest(filters=filters), self.assertRaises(ValueError):
                self.reports.export_snapshot(self.actor, self.company, "purchase_detail", **filters)

    def test_empty_result_and_snapshot_defaults_are_explicit(self):
        result = self.report()
        self.assertEqual((result["rows"], result["total"], result["document_count"], result["quantity_totals"]), ([], 0, 0, []))
        self.assertEqual(result["totals"]["net"], 0)
        snapshot = self.reports.export_snapshot(self.actor, self.company, "sales_detail", kind="return")
        self.assertEqual(snapshot["required_modules"], ("sales_returns",))
        self.assertEqual(snapshot["module"], "sales_returns")
        self.assertEqual(snapshot["filters"]["search"], "")

    def test_reading_keeps_all_business_and_audit_rows_unchanged(self):
        self.invoice()
        def records():
            with native_session(self.path) as connection:
                return {name: [tuple(row) for row in connection.execute(f"SELECT * FROM {name} ORDER BY rowid")]
                        for name in ("documents", "document_lines", "document_metadata", "journal_entries", "journal_lines", "stock_movements", "audit_events")}
        before = records()
        self.report()
        self.reports.trade_selectors(self.actor, self.company, "purchases")
        self.reports.export_snapshot(self.actor, self.company, "purchase_detail")
        self.assertEqual(records(), before)


if __name__ == "__main__":
    unittest.main()
