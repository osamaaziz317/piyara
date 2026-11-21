"""Sale form checks with synthetic master data, never the business database."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tkinter as tk
from tkinter import ttk
import unittest
from decimal import Decimal, localcontext

from bifma.native_ui import Choice
from bifma.sale_voucher_ui import SaleVoucherForm
from tools.smoke_native_ui import widgets


ROOT = Path(__file__).resolve().parents[1]


def _check(condition, message):
    if not condition:
        raise AssertionError(message)


def _fixtures():
    return {
        "accounts": [{"id": "buyer-a", "code": "0403001", "name": "Alpha Buyer"},
                     {"id": "buyer-b", "code": "0403002", "name": "Beta Buyer"}],
        "units": [{"id": "piece", "code": "PCS", "name": "PCS"},
                  {"id": "carton", "code": "CTN", "name": "CTN"}],
        "stores": [{"id": "shop", "code": "1", "name": "Shop"}],
        "products": [
            {"id": "item-a", "code": "6209", "name": "Alpha Item", "small_unit_id": "piece",
             "large_unit_id": "carton", "unit_factor": "12", "sale_rate": "4.125", "sale_rate_known": 1},
            {"id": "item-b", "code": "6210", "name": "Beta Item", "small_unit_id": "piece",
             "large_unit_id": None, "unit_factor": "1", "sale_rate": "0", "sale_rate_known": 0},
            {"id": "item-free", "code": "6211", "name": "Free Item", "small_unit_id": "piece",
             "large_unit_id": None, "unit_factor": "1", "sale_rate": "0", "sale_rate_known": 1},
        ],
    }


def _form(root, *, values=None, choices=None, blank_rows=5):
    saved, printed = [], []
    initial = {"document_number": "260954", "business_date": "2026-10-04",
               "rounding_policy": "legacy_whole", "posting_places": 0,
               "discount": "0", "breakage": "0", "freight": "0"}
    initial.update(values or {})
    form = SaleVoucherForm(root, choices=choices or _fixtures(), company={"name": "Fixture Company"},
                           values=initial, choice_factory=Choice, on_save=saved.append,
                           on_print=printed.append, on_new=lambda: None, on_exit=lambda: None,
                           on_records=lambda: None, last_number="260953", blank_rows=blank_rows)
    form.pack(fill="both", expand=True)
    return form, saved, printed


def _query(root, name):
    root.update()
    dialog = next(widget for widget in widgets(root)
                  if isinstance(widget, tk.Toplevel) and widget.title() == "Find · " + name)
    entry = next(widget for widget in widgets(dialog)
                 if isinstance(widget, ttk.Entry) and not isinstance(widget, ttk.Combobox))
    return dialog, entry


def _enter(widget):
    widget.focus_force()
    widget.event_generate("<Return>")


def _keyboard_case():
    root = tk.Tk()
    root.geometry("1050x650")
    errors = []
    root.report_callback_exception = lambda kind, value, tb: errors.append((kind.__name__, str(value)))
    try:
        form, saved, _ = _form(root)
        root.update()
        _check(all(row.is_blank() for row in form.rows), "New sale loaded saved or fabricated rows")
        _enter(form.widgets["document_number"])
        _check(root.focus_get() is form.widgets["business_date"], "Sale number Enter did not focus Date")
        _enter(form.widgets["business_date"])
        buyer, query = _query(root, "Buyer")
        query.insert(0, "Alpha")
        _enter(query)
        item, query = _query(root, "Item")
        _check(not buyer.winfo_exists() and form.inputs["account_id"].get() == "buyer-a",
               "Buyer Enter did not commit and open Item Find")
        _check(form.inputs["buyer_code"].get() == "0403001", "Buyer code lost its leading zero")
        query.insert(0, "Alpha")
        _enter(query)
        root.update()
        row = form.rows[0]
        _check(not item.winfo_exists() and root.focus_get() is row.widgets["unit_id"],
               "Item Enter did not close Find and focus Unit")
        _check(row.inputs["entered_quantity"].get() == "1" and row.inputs["quantity"].get() == "1"
               and row.inputs["rate"].get() == "4.125", "New item defaults are not its base unit, quantity one and known price")
        _enter(row.widgets["unit_id"])
        _check(root.focus_get() is row.widgets["entered_quantity"], "Unit Enter did not focus quantity")
        row.inputs["entered_quantity"].set("2")
        _enter(row.widgets["entered_quantity"])
        _check(root.focus_get() is row.widgets["rate"], "Quantity Enter did not focus rate")
        _enter(row.widgets["rate"])
        _check(root.focus_get() is row.widgets["discount_percent"], "Rate Enter did not focus discount")
        row.inputs["discount_percent"].set("10")
        _enter(row.widgets["discount_percent"])
        item, query = _query(root, "Item")
        query.insert(0, "Beta")
        _enter(query)
        root.update()
        second = form.rows[1]
        _check(second.inputs["product_id"].get() == "item-b" and second.inputs["rate"].get() == "",
               "Unknown item price was represented as a known zero")
        second.inputs["rate"].set("1.25")
        form.inputs["bill_number"].set("MANUAL-01")
        form.inputs["notes"].set("Keyboard invoice")
        form.buttons["Save"].invoke()
        _check(saved[-1]["bill_number"] == "MANUAL-01" and len(saved[-1]["lines"]) == 2,
               "Save lost the bill reference or included blank rows")
        _check(saved[-1]["lines"][0] == {"product_id": "item-a", "store_id": "shop", "quantity": "2",
                                           "rate": "4.125", "discount_percent": "10",
                                           "unit_id": "piece", "entered_quantity": "2"},
               "Keyboard entry changed quantity, price, discount or stable master IDs")
        for remaining in form.rows[2:]:
            remaining.inputs["code"].set("6211")
        old_first = form.rows[0].value()
        form.first_item()
        dialog, _ = _query(root, "Item")
        _check(len(form.rows) == 6 and form.rows[0].value() == old_first,
               "Adding after a filled grid returned to an existing line")
        dialog.event_generate("<Escape>")
        root.update()
        for button in form.buttons.values():
            _check(button.winfo_rooty() + button.winfo_height() <= root.winfo_rooty() + root.winfo_height(),
                   "An invoice action is outside the supported window")
        _check(not errors, repr(errors))
    finally:
        root.destroy()


def _calculation_case():
    root = tk.Tk()
    errors = []
    root.report_callback_exception = lambda kind, value, tb: errors.append((kind.__name__, str(value)))
    try:
        choices = _fixtures()
        choices["stores"].append({"id": "warehouse", "code": "2", "name": "Warehouse"})
        choices["default_sale_store_id"] = "warehouse"
        form, saved, printed = _form(root, choices=choices, values={"account_id": "buyer-a"})
        row = form.rows[0]
        row.inputs["code"].set("6209")
        _check(row.inputs["store_id"].get() == "warehouse", "The resolved stock location was not retained internally")
        _check("store_id" not in row.widgets, "Sale entry still exposes a store selector")
        unit = row.inputs["unit_id"]
        unit.widget.current(unit.ids.index("carton"))
        unit.widget.event_generate("<<ComboboxSelected>>")
        with localcontext() as context:
            context.prec = 2
            row.inputs["entered_quantity"].set("1.25")
            row.inputs["discount_percent"].set("10")
            form.inputs["discount"].set("0.25")
            form.inputs["breakage"].set("0.5")
            form.inputs["freight"].set("1.25")
        _check(row.inputs["factor"].get() == "12" and row.inputs["quantity"].get() == "15",
               "Large-unit conversion lost precision or used the wrong unit factor")
        _check(row.inputs["discount"].get() == "6.1875" and row.inputs["value"].get() == "55.6875",
               "Line values were rounded or used binary floating point")
        _check(form.subtotal.get() == "56" and form.total_value == Decimal("57"),
               "Preview does not apply existing subtotal and final posting rounding")
        form.buttons["Save"].invoke()
        form.buttons["Print"].invoke()
        _check(saved == printed and saved[0]["lines"][0]["quantity"] == "15"
               and saved[0]["lines"][0]["rate"] == "4.125", "Save/Print changed base-unit quantity or price")
        row.inputs["entered_quantity"].set("invalid")
        _check(form.total_value is None and row.inputs["quantity"].get() == "—",
               "Invalid quantity left a plausible stale quantity or total")
        row.inputs["entered_quantity"].set("1.25")
        row.inputs["code"].set("62")
        _check(row.inputs["product_id"].get() is None and form.total_value is None,
               "Partial item code retained the previously selected item")
        row.inputs["code"].set("6211")
        _check(row.inputs["rate"].get() == "0", "Explicit zero sale price was treated as missing")
        _check(not errors, repr(errors))
    finally:
        root.destroy()


def _draft_cleanup_case():
    root = tk.Tk()
    errors = []
    root.report_callback_exception = lambda kind, value, tb: errors.append((kind.__name__, str(value)))
    try:
        form, saved, _ = _form(root, values={"account_id": "buyer-b", "rounding_policy": "cents", "posting_places": 2,
                                           "lines": [{"product_id": "item-a", "store_id": "shop", "quantity": "1.234567",
                                                      "rate": "8.765432", "discount_percent": "2.345678"}]})
        row = form.rows[0]
        root.update()
        _check(row.inputs["quantity"].get() == "1.234567" and row.inputs["rate"].get() == "8.765432"
               and row.inputs["discount_percent"].get() == "2.345678", "Draft prices or quantities were replaced by current defaults")
        form.buttons["Save"].invoke()
        _check(saved[-1]["rounding_policy"] == "cents" and saved[-1]["posting_places"] == 2,
               "Draft editing changed its precision settings")
        before = form.raw_values()
        form.rows[-1].inputs["code"].set("UNFINISHED")
        _check(form.raw_values() != before, "Discard detection omitted incomplete item input")
        form.set_stock("12 PCS", caption="Stock as of 2026-10-04")
        _check(form.stock.get() == "12 PCS", "Supplied authorized stock display was lost")
        root.geometry("790x610")
        root.update()
        row.focus("value")
        root.update()
        _check(form.canvas.canvasx(0) > 0
               and row.widgets["value"].winfo_rootx() + row.widgets["value"].winfo_width()
                   <= form.canvas.winfo_rootx() + form.canvas.winfo_width() + 1,
               "Keyboard focus did not reveal the field beyond the horizontal viewport")
        captured = [variable for widget in (form, *widgets(form)) for variable in getattr(widget, "_bifma_variables", ())]
        names = [str(variable) for variable in captured]
        # Closing during a deferred Buyer->Item transition must cancel that job.
        form._defer(form.first_item)
        form.destroy()
        root.update()
        _check(all(variable._tk is None and variable._root is None for variable in captured),
               "Sale form shutdown retained an interpreter reference")
        _check(all(not root.tk.getboolean(root.tk.call("info", "exists", name)) for name in names),
               "Sale form shutdown retained a Tcl variable")
        _check(not errors, repr(errors))
    finally:
        root.destroy()


def _store_keyboard_case():
    root = tk.Tk()
    errors = []
    root.report_callback_exception = lambda kind, value, tb: errors.append((kind.__name__, str(value)))
    try:
        choices = _fixtures()
        choices["stores"].append({"id": "warehouse", "code": "2", "name": "Warehouse"})
        choices["default_sale_store_id"] = "warehouse"
        form, saved, _ = _form(root, choices=choices)
        root.update()
        _enter(form.widgets["business_date"])
        _, query = _query(root, "Buyer")
        query.insert(0, "Alpha")
        _enter(query)
        _, query = _query(root, "Item")
        query.insert(0, "Alpha")
        _enter(query)
        root.update()
        _check(form.default_store == "warehouse"
               and all(row.inputs["store_id"].get() == "warehouse" for row in form.rows),
               "Automatic stock assignment was not inherited by new item rows")
        _check("default_store_id" not in form.inputs and "default_store_id" not in form.widgets
               and all("store_id" not in row.widgets for row in form.rows),
               "Sale entry still exposes a Store input")
        _check(not any(isinstance(widget, ttk.Label) and str(widget.cget("text")) in {"Store", "New items store"}
                       for widget in widgets(form)), "A Store heading remains visible")
        _check(not any(isinstance(widget, tk.Toplevel) and widget.title() == "Find · Store" for widget in widgets(root)),
               "Buyer Enter opened Store Find instead of Item Find")
        form.rows[1].inputs["code"].set("6211")
        form.buttons["Save"].invoke()
        _check(len(saved) == 1 and {line["store_id"] for line in saved[0]["lines"]} == {"warehouse"},
               "Saving lost the automatic stock assignment")
        form.destroy()
        empty = _fixtures()
        empty["stores"] = []
        form, _, _ = _form(root, choices=empty)
        form.first_item()
        finder, query = _query(root, "Item")
        query.insert(0, "Alpha")
        _enter(query)
        root.update()
        _check("stock posting setup" in form.notice.get() and not finder.winfo_exists(),
               "Unconfigured stock unexpectedly guessed a location or added a Store picker")
        try:
            form.rows[0].value()
        except ValueError as error:
            _check("stock posting setup" in str(error), "Missing setup produced an unrelated validation error")
        else:
            raise AssertionError("Sale without stock assignment was accepted")
        _check(not errors, repr(errors))
    finally:
        root.destroy()


def _saved_stores_case():
    root = tk.Tk()
    try:
        choices = _fixtures()
        choices["stores"].append({"id": "warehouse", "code": "2", "name": "Warehouse"})
        choices["default_sale_store_id"] = "shop"
        lines = [{"product_id": "item-a", "store_id": location, "quantity": "1.25",
                  "rate": "8.765432", "discount_percent": "2.345678"}
                 for location in ("warehouse", "shop", "inactive-old-location")]
        form, saved, _ = _form(root, choices=choices, values={"account_id": "buyer-a", "lines": lines})
        root.update()
        expected = [dict(line, unit_id="piece", entered_quantity=line["quantity"]) for line in lines]
        _check([row.value() for row in form.rows[:3]] == expected,
               "Hiding Store changed recorded locations or decimal values on an existing draft")
        _check(form.rows[3].inputs["store_id"].get() == "shop", "New draft rows ignored the resolved default")
        _check(all("store_id" not in row.widgets for row in form.rows), "An existing draft exposes a Store selector")
        # The service still rejects inactive memberships at save/post; the form
        # must preserve their ID instead of silently redirecting the old line.
        form.buttons["Save"].invoke()
        _check(saved[0]["lines"] == expected, "Draft payload silently remapped an existing location")
        form.destroy()
        choices["default_sale_store_id"] = None
        form, _, _ = _form(root, choices=choices)
        _check(form.default_store is None and all(not row.inputs["store_id"].get() for row in form.rows),
               "Ambiguous assignment silently selected the first active store")
    finally:
        root.destroy()


def _saved_units_case():
    root = tk.Tk()
    root.geometry("790x610")
    errors = []
    root.report_callback_exception = lambda kind, value, tb: errors.append((kind.__name__, str(value)))
    try:
        original = {"product_id": "item-a", "store_id": "shop", "unit_id": "carton",
                    "entered_quantity": "1.2500", "quantity": "15", "rate": "4.125678",
                    "discount_percent": "2.345678"}
        form, saved, printed = _form(root, values={"account_id": "buyer-a", "lines": [original]})
        root.update()
        row = form.rows[0]
        _check(row.inputs["unit_id"].get() == "carton" and row.inputs["entered_quantity"].get() == "1.2500"
               and row.inputs["factor"].get() == "12" and row.inputs["quantity"].get() == "15",
               "Reopening a draft replaced its saved large unit or converted its quantity twice")
        _check(row.value() == original, "Reopening a large-unit line changed saved decimal input")
        _check(form.buttons["Print"] is form.buttons["Bill"]
               and str(form.buttons["Bill"].cget("text")) == "Bill",
               "Bill did not retain the existing print action contract")
        labels = [str(widget.cget("text")) for widget in widgets(form) if isinstance(widget, ttk.Button)]
        _check(labels.count("Bill") == 1 and not any(label in {"DC", "Print"} for label in labels),
               "The sale header contains duplicate or unsupported print actions")
        for title in ("New", "Bill", "Exit"):
            button = form.buttons[title]
            _check(button.winfo_rooty() < form.widgets["business_date"].winfo_rooty()
                   and button.winfo_rootx() + button.winfo_width() <= root.winfo_rootx() + root.winfo_width(),
                   "A top sale action is clipped or remains below the voucher")
        form.buttons["Save"].invoke()
        form.buttons["Bill"].invoke()
        _check(saved == printed and saved[0]["lines"] == [original],
               "Bill or Save lost the recorded unit, base quantity or input precision")
        reopened = saved[0]
        form.destroy()
        form, saved, _ = _form(root, values=reopened)
        _check(form.rows[0].value() == original, "A second draft reopening changed the large-unit line")
        form.rows[0].inputs["code"].set("6210")
        _check(form.rows[0].inputs["unit_id"].get() == "piece"
               and form.rows[0].inputs["entered_quantity"].get() == "1",
               "Selecting a different item retained the previous item's large-unit input")
        _check(not errors, repr(errors))
    finally:
        root.destroy()


def _finish_case():
    root = tk.Tk()
    root.geometry("1050x650")
    errors = []
    root.report_callback_exception = lambda kind, value, tb: errors.append((kind.__name__, str(value)))
    try:
        form, saved, _ = _form(root, values={"account_id": "buyer-a"})
        root.update()
        row, blank = form.rows[:2]
        row.inputs["code"].set("6209")
        row.widgets["product_id"].focus_force()
        row.widgets["product_id"].event_generate("<KP_Enter>")
        root.update()
        _check(root.focus_get() is row.widgets["unit_id"], "Existing item Enter did not advance to unit")
        row.widgets["unit_id"].event_generate("<F4>")
        unit_finder, unit_query = _query(root, "Unit")
        unit_query.insert(0, "PCS")
        unit_query.event_generate("<Return>")
        root.update()
        _check(not unit_finder.winfo_exists() and root.focus_get() is row.widgets["entered_quantity"],
               "Keyboard unit selection did not commit and advance quantity")
        for key in ("entered_quantity", "rate", "discount_percent"):
            _check(root.focus_get() is row.widgets[key], "Keypad Enter did not retain the row's entry order")
            row.widgets[key].event_generate("<KP_Enter>")
            root.update()
        finder, query = _query(root, "Item")
        query.event_generate("<Escape>")
        root.update()
        _check(not finder.winfo_exists() and root.focus_get() is blank.widgets["code"],
               "Cancelling the next Item Find did not restore its blank item code")
        blank.widgets["code"].event_generate("<Return>")
        root.update()
        for key, value in (("discount", "0"), ("breakage", "0"), ("freight", "2")):
            _check(root.focus_get() is form.widgets[key], "Finishing items did not traverse header adjustments")
            form.inputs[key].set(value)
            form.widgets[key].event_generate("<KP_Enter>")
            root.update()
        _check(len(saved) == 1 and len(saved[0]["lines"]) == 1 and saved[0]["freight"] == "2",
               "Last adjustment Enter did not save exactly the populated sale")
        _check(saved[0]["lines"][0]["rate"] == "4.125", "Finishing sale changed saved decimal precision")
        form.widgets["notes"].focus_set()
        form.widgets["notes"].event_generate("<Control-s>")
        root.update()
        _check(len(saved) == 2, "Sale Ctrl+S did not work from header remarks")
        row.widgets["rate"].focus_set()
        row.widgets["rate"].event_generate("<Control-Delete>")
        root.update()
        _check(row not in form.rows and root.focus_get() is blank.widgets["code"],
               "Sale row deletion lost its neighbor's keyboard focus")
        _check(not errors, repr(errors))
    finally:
        root.destroy()


def _repeated_discount_case():
    from bifma.keyboard import install_window
    root = tk.Tk()
    root.geometry("1050x650")
    install_window(root, root)
    errors = []
    root.report_callback_exception = lambda kind, value, tb: errors.append((kind.__name__, str(value)))
    try:
        form, saved, _ = _form(root, values={"account_id": "buyer-a"}, blank_rows=1)
        root.update()
        root.focus_force()
        form.first_item()

        def press(control, sequence):
            _check(root.focus_get() is control, "Sale entry lost its expected keyboard focus")
            control.event_generate(sequence)
            root.update()

        # Include clicked computed amounts as well as normal Dis % entry.
        # A one-row starting grid also exercises newly created row bindings.
        for index, ending in enumerate(("discount", "discount_percent", "value", "discount_percent", "discount", "value")):
            sequence = "<Return>" if index % 2 == 0 else "<KP_Enter>"
            finder, query = _query(root, "Item")
            query.insert(0, "Alpha")
            press(query, sequence)
            row = form.rows[index]
            _check(not finder.winfo_exists(), "Selecting the next item left Find open")
            press(row.widgets["unit_id"], sequence)
            row.inputs["entered_quantity"].set(str(index + 1))
            press(row.widgets["entered_quantity"], sequence)
            press(row.widgets["rate"], sequence)
            row.inputs["discount_percent"].set(str(index))
            if ending != "discount_percent":
                row.widgets[ending].focus_set()  # Same focused field as a click on its amount.
                root.update()
                _check(row.widgets[ending].instate(["readonly"]), "Calculated amount became editable")
            before = row.value()
            press(row.widgets[ending], sequence)
            next_finder, next_query = _query(root, "Item")
            _check(root.focus_get() is next_query and form.rows[index + 1].inputs["product_id"]._search_window is next_finder,
                   f"Row {index + 1} {ending} Enter did not open the next row's Item Find")
            _check(row.value() == before and len(form.rows) == index + 2,
                   "Moving to the next item changed the finished row or created duplicate rows")
        next_query.event_generate("<Escape>")
        root.update()
        _check(root.focus_get() is form.rows[-1].widgets["code"], "Next-item cancellation lost the new row")
        form.buttons["Save"].invoke()
        _check(len(saved) == 1 and len(saved[0]["lines"]) == 6, "Repeated Enter included an empty row or lost an item")
        _check([line["quantity"] for line in saved[0]["lines"]] == [str(i) for i in range(1, 7)]
               and [line["discount_percent"] for line in saved[0]["lines"]] == [str(i) for i in range(6)]
               and all(line["rate"] == "4.125" for line in saved[0]["lines"]),
               "Repeated entry changed saved quantities, rates or discounts")
        _check(not errors, repr(errors))
    finally:
        root.destroy()


@unittest.skipIf(os.name == "posix" and not os.environ.get("DISPLAY"), "Sale voucher checks require a graphical display")
class SaleVoucherUITests(unittest.TestCase):
    def _run(self, case):
        result = subprocess.run([sys.executable, "-B", "-m", "tests.test_sale_voucher_ui", case],
                                cwd=ROOT, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertEqual(json.loads(result.stdout), {"case": case, "passed": True})

    def test_keyboard_buyer_item_defaults_and_inline_save(self):
        self._run("keyboard")

    def test_exact_unit_conversion_discounts_and_existing_rounding(self):
        self._run("calculation")

    def test_existing_draft_precision_incomplete_input_and_cleanup(self):
        self._run("draft_cleanup")

    def test_buyer_enter_opens_items_without_any_store_controls(self):
        self._run("store_keyboard")

    def test_hidden_stock_assignment_preserves_mixed_and_inactive_draft_lines(self):
        self._run("saved_stores")

    def test_large_unit_draft_reopens_and_top_bill_uses_existing_print_action(self):
        self._run("saved_units")

    def test_keyboard_finish_adjustments_keypad_save_and_remove(self):
        self._run("finish")

    def test_discount_and_value_enter_continue_through_six_item_rows(self):
        self._run("repeated_discount")


if __name__ == "__main__":
    cases = {"keyboard": _keyboard_case, "calculation": _calculation_case,
             "draft_cleanup": _draft_cleanup_case, "store_keyboard": _store_keyboard_case,
             "finish": _finish_case, "repeated_discount": _repeated_discount_case,
             "saved_stores": _saved_stores_case, "saved_units": _saved_units_case}
    if len(sys.argv) == 2 and sys.argv[1] in cases:
        cases[sys.argv[1]]()
        print(json.dumps({"case": sys.argv[1], "passed": True}))
    else:
        unittest.main()
