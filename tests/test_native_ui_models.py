"""Headless checks for issued-document labels and reversal source references."""

from copy import deepcopy
import unittest

from bifma.native_ui import available_reports, saved_rows, trade_report_choices


class LedgerReportAccessChoicesTests(unittest.TestCase):
    def test_entry_rights_do_not_grant_cash_book_or_city_balances(self):
        choices = available_reports({"counter_sales": ["read"], "receipts": ["read"], "payments": ["read"], "ledger": ["create"]})
        self.assertNotIn("Cash book", choices)
        self.assertNotIn("Account balances by city", choices)
        self.assertIn("Sales / returns detail", choices)

    def test_ledger_reader_has_both_ledger_views_without_trial_balance_access(self):
        choices = available_reports({"ledger": ["read"]})
        self.assertEqual(set(choices), {"Account ledger", "Cash book"})

    def test_trial_reader_has_city_and_hierarchy_views_without_ledger_access(self):
        choices = available_reports({"trial_balance": ["read"]})
        self.assertEqual(set(choices), {"Trial balance", "Sub-trial balance", "Account balances by city"})
        self.assertEqual(choices[0], "Trial balance")

    def test_no_rights_means_no_report_choices(self):
        self.assertEqual(available_reports({}), ())
        self.assertEqual(available_reports({"ledger": ["post"], "trial_balance": ["update"]}), ())

    def test_profit_report_requires_every_underlying_read_permission(self):
        grants = {"reports": ["read"], "trial_balance": ["read"], "stock": ["read"]}
        self.assertIn("Profit and loss", available_reports(grants))
        for removed in grants:
            self.assertNotIn("Profit and loss", available_reports({key: value for key, value in grants.items() if key != removed}))


class TradeReportAccessChoicesTests(unittest.TestCase):
    def test_reading_credit_sales_does_not_offer_cash_invoices(self):
        self.assertEqual(trade_report_choices({"sales": ["read"], "counter_sales": ["create"]}, "sales"),
                         {"account": {"Invoices only": "invoice"}})

    def test_counter_operator_can_request_cash_without_credit_access(self):
        self.assertEqual(trade_report_choices({"counter_sales": ["read"]}, "sales"),
                         {"cash": {"Invoices only": "invoice"}})

    def test_returns_access_never_expands_to_invoice_reading(self):
        options = trade_report_choices({"sales_returns": ["read"]}, "sales")
        self.assertEqual(set(options), {"all", "account", "cash"})
        for choices in options.values():
            self.assertEqual(choices, {"Returns only": "return"})

    def test_credit_and_return_operator_keeps_combined_account_default(self):
        options = trade_report_choices({"sales": ["read"], "sales_returns": ["read"]}, "sales")
        self.assertEqual(next(iter(options)), "account")
        self.assertEqual(options["account"]["Invoices and returns"], "all")
        self.assertEqual(options["cash"], {"Returns only": "return"})
        self.assertEqual(options["all"], {"Returns only": "return"})

    def test_combined_invoice_read_requires_both_sales_grants(self):
        options = trade_report_choices({"sales": ["read"], "counter_sales": ["read"]}, "sales")
        self.assertEqual(next(iter(options)), "all")
        self.assertEqual(options["all"], {"Invoices only": "invoice"})
        self.assertEqual(trade_report_choices({"receipts": ["read"], "counter_sales": ["post"]}, "sales"), {})

    def test_purchase_permission_covers_both_purchase_entry_methods(self):
        options = trade_report_choices({"purchases": ["read"]}, "purchases")
        self.assertEqual(next(iter(options)), "all")
        self.assertEqual(set(options), {"all", "account", "cash"})
        self.assertTrue(all(choices == {"Invoices only": "invoice"} for choices in options.values()))


class SavedRowTests(unittest.TestCase):
    def document(self):
        return {
            "kind": "sale", "total_amount": "12.25",
            "snapshot": {
                "company": {"name": "Issued company"},
                "accounts": {"party": {"code": "C001", "name": "Issued customer"},
                             "revenue": {"code": "R001", "name": "Issued revenue"}},
                "lines": [{"line_id": "source-line", "product": {"code": "P001", "name": "Issued item"},
                           "store": {"code": "S001", "name": "Issued store"}}],
            },
            "lines": [{"id": "source-line", "product_id": "product", "store_id": "store", "quantity": "2.5", "net_amount": "12.25"}],
            "journal_lines": [{"id": "journal-line", "account_id": "party", "debit": "12.25", "credit": "0"}],
            "stock_movements": [{"id": "movement", "document_line_id": "source-line", "product_id": "product", "store_id": "store", "direction": "out", "quantity": "2.5"}],
        }

    def test_saved_item_store_and_account_labels_come_from_snapshot(self):
        document = self.document()
        rows = saved_rows(document)
        self.assertEqual((rows["lines"][0]["product_code"], rows["lines"][0]["product_name"], rows["lines"][0]["store_name"]), ("P001", "Issued item", "Issued store"))
        self.assertEqual((rows["journal_lines"][0]["account_code"], rows["journal_lines"][0]["account_name"]), ("C001", "Issued customer"))
        self.assertEqual(rows["stock_movements"][0]["product_name"], "Issued item")

    def test_rendering_preserves_exact_amounts_and_does_not_mutate_document(self):
        document = self.document()
        original = deepcopy(document)
        rendered = saved_rows(document)
        self.assertEqual(document, original)
        self.assertEqual(rendered["journal_lines"][0]["debit"], "12.25")
        self.assertEqual(rendered["lines"][0]["quantity"], "2.5")
        self.assertEqual(rendered["stock_movements"][0]["direction"], "out")

    def test_reversal_maps_new_line_ids_through_original_source_line(self):
        original = self.document()
        reversal = {
            "kind": "reversal", "snapshot": {"original_snapshot": original["snapshot"]},
            "lines": [{"id": "reversal-line", "source_line_id": "source-line", "product_id": "product", "store_id": "store", "quantity": "2.5"}],
            "journal_lines": [{"id": "reverse-journal", "account_id": "party", "debit": "0", "credit": "12.25"}],
            "stock_movements": [{"id": "reverse-movement", "document_line_id": "reversal-line", "direction": "in", "quantity": "2.5"}],
        }
        rows = saved_rows(reversal)
        self.assertEqual(rows["lines"][0]["product_name"], "Issued item")
        self.assertEqual(rows["stock_movements"][0]["store_name"], "Issued store")
        self.assertEqual(rows["stock_movements"][0]["direction"], "in")
        self.assertEqual(rows["journal_lines"][0]["account_name"], "Issued customer")
        self.assertEqual(rows["journal_lines"][0]["credit"], "12.25")

    def test_journal_only_document_labels_do_not_require_product_lines(self):
        document = self.document()
        document.update(kind="cash_receipt", lines=[], stock_movements=[])
        document["snapshot"]["lines"] = []
        rows = saved_rows(document)
        self.assertEqual(rows["journal_lines"][0]["account_code"], "C001")
        self.assertEqual(rows["lines"], [])
        self.assertEqual(rows["stock_movements"], [])

    def test_draft_review_labels_proposed_effects_without_claiming_they_are_posted(self):
        document = self.document()
        document["status"] = "draft"
        document["calculation"] = {
            "journal_lines": document.pop("journal_lines"),
            "stock_movements": document.pop("stock_movements"),
        }
        document["journal_lines"] = []
        document["stock_movements"] = []
        document["calculation"]["stock_movements"][0]["pool"] = "regular"
        original = deepcopy(document)
        rows = saved_rows(document)
        self.assertEqual(rows["journal_lines"][0]["account_name"], "Issued customer")
        self.assertEqual(rows["journal_lines"][0]["debit"], "12.25")
        self.assertEqual(rows["stock_movements"][0]["product_name"], "Issued item")
        self.assertEqual(rows["stock_movements"][0]["store_name"], "Issued store")
        self.assertEqual(rows["stock_movements"][0]["pool"], "regular")
        self.assertEqual(document, original)
        self.assertEqual(document["journal_lines"], [])
        self.assertEqual(document["stock_movements"], [])

    def test_posted_review_uses_persisted_effects_even_with_different_preview(self):
        document = self.document()
        document["status"] = "posted"
        document["calculation"] = {
            "journal_lines": [{"account_id": "revenue", "debit": "0", "credit": "999"}],
            "stock_movements": [],
        }
        rows = saved_rows(document)
        self.assertEqual(rows["journal_lines"][0]["account_name"], "Issued customer")
        self.assertEqual(rows["journal_lines"][0]["debit"], "12.25")
        self.assertEqual(rows["stock_movements"][0]["quantity"], "2.5")

    def test_stock_values_use_saved_cost_evidence_and_preserve_unknowns(self):
        document = self.document()
        document['status'] = 'posted'
        document['stock_movements'][0].update(unit_cost='0', value='0')
        row = saved_rows(document)['stock_movements'][0]
        self.assertEqual((row['unit_cost_display'], row['stock_value_display']), ('Unknown', 'Unknown'))
        document['stock_valuations'] = [{'movement_id': 'movement', 'unit_cost': '4.25', 'value': '10.625'}]
        row = saved_rows(document)['stock_movements'][0]
        self.assertEqual((row['unit_cost_display'], row['stock_value_display']), ('4.25', '10.625'))
        document['status'] = 'draft'
        document['calculation'] = {'stock_movements': document['stock_movements']}
        self.assertEqual(saved_rows(document)['stock_movements'][0]['stock_value_display'], 'Pending posting')


if __name__ == "__main__":
    unittest.main()
