"""Exercise native entry screens against a disposable synthetic database.

Run with a graphical display: python3 -m tools.smoke_native_ui
No real database, recovered archive, or saved user credential is used.
"""

import json
from datetime import date
import argparse
import os
from pathlib import Path
import tempfile
import time
import tkinter as tk
from tkinter import ttk
from unittest.mock import patch

from bifma.native_schema import initialize_database, native_session
from bifma.native_ui import Choice, NativeApp
from bifma.security import SecurityService


def widgets(parent):
    for child in parent.winfo_children():
        yield child
        yield from widgets(child)


def field(parent, title, value):
    # Shared legacy tests name these login fields as they appeared before the
    # owner's reference layout. Resolve by the actual live login form only;
    # similarly named administrator/restore forms retain their own selectors.
    form = getattr(parent, "_login_form", None)
    key = {"User": "username", "Username": "username", "User Name": "display_name",
           "Display name": "display_name", "Password": "password", "Passphrase": "password",
           "Confirm password": "confirm", "Confirm passphrase": "confirm"}.get(title)
    if form is not None and key in form.widgets and form.widgets[key].winfo_exists():
        control = form.widgets[key]
        control.delete(0, "end")
        control.insert(0, value)
        return
    for widget in widgets(parent):
        if isinstance(widget, ttk.Label) and widget.cget("text") == title:
            for control in widget.master.winfo_children():
                if isinstance(control, ttk.Combobox):
                    choices = list(control.cget("values"))
                    control.current(choices.index(str(value)))
                    control.event_generate("<<ComboboxSelected>>")
                    return
                if isinstance(control, ttk.Entry):
                    control.delete(0, "end")
                    control.insert(0, value)
                    return
    raise AssertionError(f"Missing field: {title}")


def button(parent, title):
    form = getattr(parent, "_login_form", None)
    if (title == "Sign in" and form is not None and "username" in form.widgets
            and form.widgets["username"].winfo_exists()):
        title = "OK"
    for widget in widgets(parent):
        if isinstance(widget, ttk.Button) and widget.cget("text") == title:
            widget.invoke()
            return
    raise AssertionError(f"Missing button: {title}")


def main(screenshot_dir=None):
    with tempfile.TemporaryDirectory(prefix="bifma-native-smoke-") as folder:
        path = Path(folder) / "synthetic.sqlite3"
        initialize_database(path)
        SecurityService(path).bootstrap_admin("owner", "Copper orchard 42 window violet")
        errors = []
        with patch("bifma.native_ui.messagebox.showerror", side_effect=lambda title, text, **kwargs: errors.append((title, text))):
            app = NativeApp()
            def capture(name):
                if screenshot_dir is None:
                    return
                from PIL import ImageGrab
                destination = Path(screenshot_dir)
                destination.mkdir(parents=True, exist_ok=True)
                app.update()
                x, y = app.winfo_rootx(), app.winfo_rooty()
                ImageGrab.grab(bbox=(x, y, x + app.winfo_width(), y + app.winfo_height()), xdisplay=os.environ.get("DISPLAY")).save(destination / name)
            def drain():
                end = time.monotonic() + 20
                while True:
                    app.update()
                    if app.future is None:
                        app.update()
                        if app.future is None:
                            break
                    if time.monotonic() > end:
                        raise AssertionError("UI worker did not finish")
                    time.sleep(.02)
                if errors:
                    raise AssertionError(errors)
            def dialog(title):
                drain()
                result = [child for child in widgets(app) if isinstance(child, tk.Toplevel) and child.title() == title]
                if len(result) != 1:
                    raise AssertionError(f"Missing dialog: {title}")
                return result[0]
            try:
                # Large selectors keep IDs distinct while searching/paging
                # English/Urdu labels. The fixture has no database records.
                holder = ttk.Frame(app)
                holder.pack()
                choices = [{"id": str(index), "code": f"CODE-{index:03}", "name": "Shared name", "name_urdu": "نمونہ" if index == 204 else None} for index in range(205)]
                selector = Choice(holder, choices, selected="3")
                selector.widget.pack()
                selector.add_search_button("Item")
                button(holder, "Find…")
                finder = dialog("Find · Item")
                button(finder, "Next")
                button(finder, "Next")
                selection_tree = next(w for w in widgets(finder) if isinstance(w, ttk.Treeview))
                assert len(selection_tree.get_children()) == 5
                selection_tree.selection_set("4")
                button(finder, "Use selected")
                assert selector.get() == "204"
                button(holder, "Find…")
                finder = dialog("Find · Item")
                field(finder, "Code or name", "نمونہ")
                button(finder, "Search")
                selection_tree = next(w for w in widgets(finder) if isinstance(w, ttk.Treeview))
                assert len(selection_tree.get_children()) == 1 and selection_tree.item("0", "values")[0] == "CODE-204"
                field(finder, "Code or name", "no matching record")
                button(finder, "Search")
                assert not selection_tree.get_children()
                finder.destroy()
                assert selector.get() == "204"
                holder.destroy()
                app.open_database(path)
                drain()
                field(app, "User", "owner")
                field(app, "Password", "Copper orchard 42 window violet")
                button(app, "OK")
                drain()
                app.master_dialog("companies")
                company_dialog = dialog("New Companies")
                field(company_dialog, "Code", "DEMO")
                field(company_dialog, "Name", "Synthetic example company")
                field(company_dialog, "Fiscal start (YYYY-MM-DD)", f"{date.today().year}-01-01")
                field(company_dialog, "Fiscal end (YYYY-MM-DD)", f"{date.today().year}-12-31")
                field(company_dialog, "Address", "Synthetic warehouse street")
                field(company_dialog, "Phone", "001-555-0100")
                field(company_dialog, "Sales tax number", "0001-DEMO")
                button(company_dialog, "Save changes")
                drain()
                assert app.company_id
                actor, masters, company = app.actor, app.masters, app.company_id
                root_account = masters.create(actor, "accounts", {"code": "000", "name": "Accounts", "kind": "group"})
                accounts = {}
                for code, kind, classification in (("CUSTOMER", "customer", "asset"), ("REVENUE", "general", "income"), ("PURCHASE", "general", "expense"), ("INVENTORY", "general", "asset"), ("CAPITAL", "general", "equity")):
                    accounts[code] = masters.create(actor, "accounts", {"code": code, "name": code.title(), "kind": kind, "classification": classification, "parent_id": root_account["id"]})
                unit = masters.create(actor, "units", {"code": "PCS", "name": "Pieces"})
                large_unit = masters.create(actor, "units", {"code": "PACK", "name": "Packs"})
                store = masters.create(actor, "stores", {"code": "SHOP", "name": "Shop"})
                masters.create(actor, "company_stores", {"company_id": company, "store_id": store["id"]})
                product = masters.create(actor, "products", {"code": "EXAMPLE", "name": "Example item", "small_unit_id": unit["id"], "large_unit_id": large_unit["id"], "unit_factor": "6", "sale_rate": "20", "purchase_rate": "12", "sale_account_id": accounts["REVENUE"]["id"], "purchase_account_id": accounts["PURCHASE"]["id"], "inventory_account_id": accounts["INVENTORY"]["id"]})
                # Enter, review and post opening stock through the real widgets.
                app.document_editor("stock_opening")
                editor = dialog("New · Opening stock")
                field(editor, "Document number", "OPEN-1")
                field(editor, "Opening balance counterpart", "CAPITAL · Capital")
                button(editor, "Add line")
                line = dialog("Add line")
                field(line, "Item", "EXAMPLE · Example item")
                field(line, "Store", "SHOP · Shop")
                button(line, "Convert large / base quantities")
                conversion = dialog("Convert quantity")
                field(conversion, "Large quantity (Packs)", "1")
                field(conversion, "Base quantity (Pieces)", "4")
                button(conversion, "Use base quantity")
                field(line, "Opening value", "100")
                button(line, "Use line")
                button(editor, "Save draft & review")
                review = dialog("Review draft · OPEN-1")
                button(review, "Post document")
                posted = dialog("Posted · OPEN-1")
                button(posted, "Close")
                drain()
                # Post a discounted sale; verify the service effects, not just
                # the presence of a dialog or the editor's displayed values.
                app.document_editor("sale")
                editor = dialog("New · Sale Voucher")
                sale = editor.sale_form
                sale.inputs["document_number"].set("SALE-1")
                sale.inputs["buyer_code"].set("CUSTOMER")
                sale.inputs["discount"].set("2")
                line = sale.rows[0]
                line.inputs["code"].set("EXAMPLE")
                assert line.inputs["rate"].get() == "20"
                line.inputs["entered_quantity"].set("2")
                line.inputs["discount_percent"].set("10")
                button(editor, "Save")
                review = dialog("Review draft · SALE-1")
                button(review, "Post document")
                posted = dialog("Posted · SALE-1")
                capture("native-sale.png")
                output = Path(folder) / "posted-sale.html"
                with patch("bifma.native_ui.filedialog.asksaveasfilename", return_value=str(output)), patch("bifma.native_ui.messagebox.showinfo"):
                    button(posted, "Save printable copy")
                    drain()
                assert "Sale Voucher" in output.read_text(encoding="utf-8")
                assert "SALE-1" in output.read_text(encoding="utf-8")
                assert "Synthetic warehouse street" in output.read_text(encoding="utf-8")
                assert "0001-DEMO" in output.read_text(encoding="utf-8")
                button(posted, "Close")
                drain()
                # Exercise source-picker paging independently of the real
                # posted-only query tests, then load an actual saved line.
                source = app.transactions.list_documents(actor, company, "sale")[0]
                fixture_rows = [{**source, "document_number": f"PAGE-{index}"} for index in range(100)] + [source]
                chosen = []
                def source_page(_actor, _company, kind, **filters):
                    assert kind == "sale" and filters["status"] == "posted"
                    start = filters["offset"]
                    return fixture_rows[start:start + filters["limit"]]
                with patch.object(app.transactions, "list_documents", side_effect=source_page):
                    app.select_original_line(company, "sale_return", lambda row, document: chosen.append((row, document)))
                    picker = dialog("Choose the original document")
                    button(picker, "Next")
                    drain()
                    source_tree = next(w for w in widgets(picker) if isinstance(w, ttk.Treeview))
                    assert len(source_tree.get_children()) == 1
                    source_tree.selection_set("0")
                    button(picker, "Show lines")
                    drain()
                    button(picker, "Back to documents")
                    drain()
                    source_tree = next(w for w in widgets(picker) if isinstance(w, ttk.Treeview))
                    source_tree.selection_set("0")
                    button(picker, "Show lines")
                    drain()
                    source_tree = next(w for w in widgets(picker) if isinstance(w, ttk.Treeview))
                    source_tree.selection_set("0")
                    button(picker, "Use selected line")
                    drain()
                assert chosen[0][1]["id"] == source["id"] and chosen[0][0]["product_id"] == product["id"]
                with native_session(path) as connection:
                    total = connection.execute("SELECT total_amount FROM documents WHERE document_number='SALE-1' AND status='posted'").fetchone()[0]
                    assert total == "34", total
                assert app.transactions.stock_balance(actor, company, product_id=product["id"])[0]["quantity"] == "8"
                for screen in (app.dashboard, app.master_screen, app.user_screen, app.report_screen):
                    screen()
                    drain()
                button(app, "Run report")
                drain()
                app.geometry("1050x680")
                app.update()
                capture("native-reports-minimum.png")
                field(app, "Report", "Sub-trial balance")
                pool_choice = next(w for w in widgets(app) if isinstance(w, ttk.Combobox) and tuple(w.cget("values")) == ("regular", "breakage"))
                assert pool_choice.instate(["disabled"])
                button(app, "Run report")
                drain()
                hierarchy = next(w for w in widgets(app) if isinstance(w, ttk.Treeview))
                assert any("Accounts" in hierarchy.item(row, "values") for row in hierarchy.get_children())
                field(app, "Report", "Account ledger")
                button(app, "Run report")
                account_dialog = dialog("Choose account")
                chooser = next(w for w in widgets(account_dialog) if isinstance(w, ttk.Combobox))
                chooser.current(list(chooser.cget("values")).index("CUSTOMER · Customer"))
                button(account_dialog, "Show ledger")
                drain()
                assert any(isinstance(w, ttk.Label) and w.cget("text") == "Opening: 0    Closing: 34" for w in widgets(app))
                field(app, "Report", "Item ledger")
                button(app, "Run report")
                item_dialog = dialog("Choose item and store")
                field(item_dialog, "Item", "EXAMPLE · Example item")
                field(item_dialog, "Store (blank for all)", "SHOP · Shop")
                button(item_dialog, "Show item ledger")
                drain()
                assert any(isinstance(w, ttk.Label) and "Opening: 0    In: 10    Out: 2    Closing: 8" in w.cget("text") for w in widgets(app))
                report_output = Path(folder) / "item-ledger.html"
                with patch("bifma.native_ui.filedialog.asksaveasfilename", return_value=str(report_output)), patch("bifma.native_ui.messagebox.showinfo"):
                    button(app, "Save full report")
                    drain()
                report_text = report_output.read_text(encoding="utf-8")
                assert "OPEN-1" in report_text and "SALE-1" in report_text
                field(app, "Report", "Stock quantities")
                assert not pool_choice.instate(["disabled"])
                button(app, "Run report")
                stock_dialog = dialog("Stock quantity filters")
                field(stock_dialog, "Store (blank for all)", "SHOP · Shop")
                field(stock_dialog, "Item code or name", "EXAMPLE")
                button(stock_dialog, "Show stock quantities")
                drain()
                stock_tree = next(w for w in widgets(app) if isinstance(w, ttk.Treeview))
                assert len(stock_tree.get_children()) == 1
                assert stock_tree.item(stock_tree.get_children()[0], "values")[-1] == "8"
                field(app, "Report", "Sales / returns detail")
                button(app, "Run report")
                activity = dialog("Trade activity")
                button(activity, "Choose filters")
                trade_filters = dialog("Trade detail filters")
                button(trade_filters, "Show trade detail")
                drain()
                assert any(isinstance(w, ttk.Label) and "Line amount: 36 · Recorded / final document amount: 34" in w.cget("text") for w in widgets(app))
                capture("native-trade-detail-minimum.png")
                trade_tree = next(w for w in widgets(app) if isinstance(w, ttk.Treeview))
                assert len(trade_tree.get_children()) == 1
                assert trade_tree.bbox("0"), "The first report row must be visible at the minimum window size."
                button(app, "Quantities by item")
                quantities = dialog("Trade quantities")
                quantity_tree = next(w for w in widgets(quantities) if isinstance(w, ttk.Treeview))
                assert quantity_tree.item("0", "values")[-1] == "2"
                quantities.destroy()
                trade_output = Path(folder) / "sales-detail.html"
                with patch("bifma.native_ui.filedialog.asksaveasfilename", return_value=str(trade_output)), patch("bifma.native_ui.messagebox.showinfo"):
                    button(app, "Save full report")
                    drain()
                assert "SALE-1" in trade_output.read_text() and "Final document totals (each document once)" in trade_output.read_text()
                button(app, "Run report")
                activity = dialog("Trade activity")
                button(activity, "Choose filters")
                trade_filters = dialog("Trade detail filters")
                field(trade_filters, "Item (blank for all)", "EXAMPLE · Example item")
                button(trade_filters, "Show trade detail")
                drain()
                assert any(isinstance(w, ttk.Label) and "Header amounts omitted for item/store filters" in w.cget("text") for w in widgets(app))
                field(app, "Report", "Purchases / returns detail")
                button(app, "Run report")
                activity = dialog("Trade activity")
                button(activity, "Choose filters")
                trade_filters = dialog("Trade detail filters")
                button(trade_filters, "Show trade detail")
                drain()
                trade_tree = next(w for w in widgets(app) if isinstance(w, ttk.Treeview))
                assert len(trade_tree.get_children()) == 0
                # Breakage has its own pool and corrected dispatch direction.
                for kind, title, number, quantity in (("breakage_receive", "Breakage receive", "BR-1", "3"), ("breakage_dispatch", "Breakage dispatch", "BD-1", "1")):
                    app.document_editor(kind)
                    editor = dialog("New · " + title)
                    field(editor, "Document number", number)
                    field(editor, "Party account", "CUSTOMER · Customer")
                    if kind == "breakage_receive":
                        field(editor, "Breakage allowance", "1")
                    button(editor, "Add line")
                    line = dialog("Add line")
                    field(line, "Item", "EXAMPLE · Example item")
                    field(line, "Store", "SHOP · Shop")
                    field(line, "Quantity in base units", quantity)
                    field(line, "Rate per base unit", "5")
                    button(line, "Use line")
                    button(editor, "Save draft & review")
                    review = dialog("Review draft · " + number)
                    button(review, "Post document")
                    posted = dialog("Posted · " + number)
                    button(posted, "Close")
                    drain()
                assert app.transactions.stock_balance(actor, company, product_id=product["id"], pool="breakage")[0]["quantity"] == "2"
                assert app.transactions.stock_balance(actor, company, product_id=product["id"], pool="regular")[0]["quantity"] == "8"
                app.audit_screen()
                drain()
                field(app, "Action", "transaction.posted")
                button(app, "Apply filters")
                drain()
                audit_tree = next(w for w in widgets(app) if isinstance(w, ttk.Treeview))
                assert len(audit_tree.get_children()) == 4
                audit_tree.selection_set(audit_tree.get_children()[0])
                button(app, "View event")
                event_dialog = dialog("Audit event")
                assert any(isinstance(w, tk.Text) and '"kind"' in w.get("1.0", "end") for w in widgets(event_dialog))
                button(event_dialog, "Close")
                backup = Path(folder) / "backup.sqlite3"
                with patch("bifma.native_ui.filedialog.asksaveasfilename", return_value=str(backup)), patch("bifma.native_ui.messagebox.showinfo"):
                    app.create_backup()
                    drain()
                assert backup.is_file()
                # A separate approver can review/post without draft-update rights.
                approver = app.security.create_user(actor, "approver", "Silver meadow 63 river lantern")
                inactive_company = masters.create(actor, "companies", {"code": "OLD", "name": "Inactive example"})
                app.security.set_company_access(actor, approver.id, [company, inactive_company["id"]])
                masters.update(actor, "companies", inactive_company["id"], {"is_active": False}, inactive_company["version"])
                app.security.set_permissions(actor, approver.id, "sales", ["read", "post"])
                app.user_screen()
                drain()
                for target_role in (True, False):
                    user_tree = next(w for w in widgets(app) if isinstance(w, ttk.Treeview))
                    selected_user = next(row for row in user_tree.get_children() if user_tree.item(row, "values")[0] == "approver")
                    user_tree.selection_set(selected_user)
                    button(app, "Administrator role")
                    role_dialog = dialog("Administrator role · approver")
                    next(w for w in widgets(role_dialog) if isinstance(w, ttk.Checkbutton)).invoke()
                    button(role_dialog, "Save administrator role")
                    drain()
                    assert app.security.get_access(actor, approver.id)["user"].is_admin is target_role
                app.access_dialog(approver)
                access = dialog("Access · approver")
                rights = {w.cget("text"): w for w in widgets(access) if isinstance(w, ttk.Checkbutton)}
                assert rights["OLD · Inactive example (inactive)"].instate(["selected"])
                button(access, "Save company access")
                drain()
                assert inactive_company["id"] in app.security.get_access(actor, approver.id)["company_ids"]
                assert rights["Read"].instate(["selected"]) and rights["Post"].instate(["selected"])
                assert not rights["Update"].instate(["selected"])
                module_choice = next(w for w in widgets(access) if isinstance(w, ttk.Combobox) and "trial_balance" in w.cget("values"))
                module_choice.current(list(module_choice.cget("values")).index("trial_balance"))
                module_choice.event_generate("<<ComboboxSelected>>")
                drain()
                assert rights["Create"].instate(["disabled"]) and rights["Post"].instate(["disabled"])
                access.destroy()
                draft = app.transactions.save_draft(actor, company, "sale", {
                    "document_number": "APPROVE-1", "business_date": date.today().isoformat(),
                    "rounding_policy": "legacy_whole", "posting_places": 0, "account_id": accounts["CUSTOMER"]["id"],
                    "lines": [{"product_id": product["id"], "store_id": store["id"], "quantity": "1", "rate": "20"}]})
                app.logout()
                drain()
                assert app.actor is None
                field(app, "User", "approver")
                field(app, "Password", "Silver meadow 63 river lantern")
                button(app, "OK")
                drain()
                app.open_document(draft["id"])
                review = dialog("Review draft · APPROVE-1")
                assert not any(isinstance(w, ttk.Button) and w.cget("text") == "Edit draft" for w in widgets(review))
                button(review, "Post document")
                posted = dialog("Posted · APPROVE-1")
                assert not any(isinstance(w, ttk.Button) and w.cget("text") == "Reverse document" for w in widgets(posted))
                button(posted, "Close")
                drain()
                # Seed an existing correction through the service: reversal
                # entry is removed from the UI, but old effects stay readable.
                saved = app.transactions.get(app.actor, company, draft["id"])
                reverse = app.transactions.reverse(app.actor, company, saved["id"], saved["revision"],
                    document_number="REV-1", business_date=date.today().isoformat(),
                    reason="Synthetic saved correction compatibility")
                app.open_document(reverse["id"])
                reversed_dialog = dialog("Posted · REV-1")
                button(reversed_dialog, "Close")
                drain()
                # Sales-only readers can reach their report without unrelated
                # stock/ledger/return grants; invoice reversals stay visible.
                menu_root = app.winfo_toplevel()
                menu = menu_root.nametowidget(str(menu_root.cget("menu")))
                assert any(menu.type(index) == "cascade" and menu.entrycget(index, "label") == "Reports"
                           for index in range(menu.index("end") + 1))
                app.report_screen()
                button(app, "Run report")
                trade_filters = dialog("Trade detail filters")
                button(trade_filters, "Show trade detail")
                drain()
                trade_tree = next(w for w in widgets(app) if isinstance(w, ttk.Treeview))
                assert len(trade_tree.get_children()) == 3
                verifier = app.security.authenticate("owner", "Copper orchard 42 window violet")
                assert app.transactions.stock_balance(verifier, company, product_id=product["id"])[0]["quantity"] == "8"
                app.security.logout(verifier)
                app.logout()
                drain()
                app.welcome()
                restored = Path(folder) / "restored.sqlite3"
                with patch("bifma.native_ui.filedialog.askopenfilename", return_value=str(backup)), patch("bifma.native_ui.filedialog.asksaveasfilename", return_value=str(restored)):
                    app.restore_dialog()
                restore = dialog("Restore native backup")
                field(restore, "Backup administrator username", "owner")
                field(restore, "Passphrase", "Copper orchard 42 window violet")
                button(restore, "Restore to new file")
                drain()
                assert app.path == restored
                with native_session(restored) as connection:
                    assert connection.execute("SELECT COUNT(*) FROM auth_sessions WHERE revoked_at IS NULL").fetchone()[0] == 0
                    assert connection.execute("SELECT COUNT(*) FROM documents WHERE status='posted'").fetchone()[0] == 4
                print(json.dumps({"native_ui": "passed", "database": "temporary synthetic", "posted_documents": 6,
                                  "verified_sale_total": "34", "verified_stock_quantity": "8", "verified_breakage_quantity": "2",
                                  "item_ledger": "passed", "sub_trial_balance": "passed", "audit_browser": "passed", "quantity_conversion": "passed",
                                  "trade_detail": "passed", "large_choice_search": "passed",
                                  "backup_restore": "passed", "document_export": "passed", "report_export": "passed",
                                  "source_picker_paging": "passed", "stock_filters": "passed", "administrator_roles": "passed",
                                  "inactive_company_access": "passed", "stored_item_rate": "passed"}))
            finally:
                if app.winfo_exists():
                    app.close()
                    if app.future is not None:
                        drain()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--screenshot-dir", help="Optional synthetic UI screenshots; requires Pillow")
    main(parser.parse_args().screenshot_dir)
