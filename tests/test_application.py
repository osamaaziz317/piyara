"""One-window application checks with a synthetic baseline and native data."""

from contextlib import redirect_stdout
from contextlib import closing
import ctypes.util
from datetime import date, timedelta
from decimal import Decimal
import gc
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sqlite3
import sys
import tempfile
import threading
import time
import tkinter as tk
from tkinter import ttk
import unittest
from unittest.mock import patch

from tests.test_preview_lifecycle import ROOT, _archive, _check, _pump
from tests.test_preview_documents import _Pointer


def _application_menu(app):
    path = str(app.cget('menu'))
    _check(bool(path), 'The signed-in application has no top menu bar')
    menu = app.nametowidget(path)
    _check(isinstance(menu, tk.Menu), 'Application navigation is not a native top menu')
    return menu


def _menu_entries(menu):
    last = menu.index('end')
    if last is None:
        return []
    return [(index, str(menu.entrycget(index, 'label')))
            for index in range(last + 1) if menu.type(index) not in {'separator', 'tearoff'}]


def _menu_section(app, title):
    menu = _application_menu(app)
    found = [index for index, label in _menu_entries(menu) if label == title]
    _check(len(found) == 1, 'Expected one top menu section: ' + title)
    index = found[0]
    _check(menu.type(index) == 'cascade', 'Top section is not a dropdown: ' + title)
    return app.nametowidget(str(menu.entrycget(index, 'menu')))


def _menu_commands(menu):
    result = []
    for index, label in _menu_entries(menu):
        if menu.type(index) == 'cascade':
            child = menu.nametowidget(str(menu.entrycget(index, 'menu')))
            result.extend(_menu_commands(child))
        elif menu.type(index) == 'command':
            result.append((menu, index, label))
    return result


def _invoke_menu(app, section, title):
    matches = [(menu, index) for menu, index, label in _menu_commands(_menu_section(app, section))
               if label == title]
    _check(len(matches) == 1, 'Expected one menu command in ' + section + ': ' + title)
    menu, index = matches[0]
    _check(str(menu.entrycget(index, 'state')) == 'normal', 'Menu command remains disabled: ' + title)
    menu.invoke(index)


def _cash_form(editor):
    from bifma.cash_voucher_ui import CashVoucherForm
    from tools.smoke_native_ui import widgets
    matches = [widget for widget in widgets(editor) if isinstance(widget, CashVoucherForm)]
    _check(len(matches) == 1, 'Expected one inline cash voucher form')
    return matches[0]


def _sale_form(editor):
    from bifma.sale_voucher_ui import SaleVoucherForm
    from tools.smoke_native_ui import widgets
    matches = [widget for widget in widgets(editor) if isinstance(widget, SaleVoucherForm)]
    _check(len(matches) == 1, 'Expected one inline sale voucher form')
    return matches[0]


def _sale_buyer(editor, code):
    form = _sale_form(editor)
    control = form.widgets['buyer_code']
    control.delete(0, 'end')
    control.insert(0, str(code))
    _check(form.inputs['account_id'].get() is not None, 'Exact buyer code did not select its account')


def _enter_sale_line(editor, code, quantity, rate=None, *, store=None, discount='0', index=0):
    form = _sale_form(editor)
    _check(index < len(form.rows), 'Requested sale line is outside the inline form')
    row = form.rows[index]
    for key, value in (('code', code), ('entered_quantity', quantity), ('discount_percent', discount)):
        row.widgets[key].delete(0, 'end')
        row.widgets[key].insert(0, str(value))
    if rate is not None:
        row.widgets['rate'].delete(0, 'end')
        row.widgets['rate'].insert(0, str(rate))
    if store is not None:
        _check(row.inputs['store_id'].get() == store,
               'Sale line did not retain its established internal stock location')
    _check(row.inputs['product_id'].get() is not None, 'Exact item code did not select its item')
    return row


def _enter_cash_line(editor, code, amount, narration='', *, index=0):
    from tools.smoke_native_ui import button
    form = _cash_form(editor)
    while len(form.rows) <= index:
        button(form, 'Add line')
    row = form.rows[index]
    for key, value in (('code', code), ('amount', amount), ('narration', narration)):
        control = row.widgets[key]
        control.delete(0, 'end')
        control.insert(0, str(value))
    _check(row.inputs['account_id'].get() is not None,
           'Typing the complete account code did not select its account')
    return row


def _save_editor(editor):
    from bifma.cash_voucher_ui import CashVoucherForm
    from bifma.sale_voucher_ui import SaleVoucherForm
    from tools.smoke_native_ui import button, widgets
    button(editor, 'Save' if any(isinstance(widget, (CashVoucherForm, SaleVoucherForm)) for widget in widgets(editor))
           else 'Save draft & review')


def _operational_archive(path):
    """An original company with real openings, an unknown price and unit issue."""
    _archive(path)
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute("UPDATE CONFIG SET CHCODE_CASH='cash',SDATE='2026-01-01T00:00:00',EDATE='2099-12-31T00:00:00'")
        connection.executemany("INSERT INTO CHART(CHCODE,CHDESC,ALLOWED) VALUES (?,?,'1')", [
            ("cash", "Cash"), ("customer", "Synthetic customer"), ("sales", "Sales"),
            ("purchases", "Purchases"), ("equity", "Opening equity")])
        connection.executemany("INSERT INTO UOM(UOM_ID,UOM_DESC) VALUES (?,?)", [("1", "Piece"), ("2", "Box")])
        connection.execute("INSERT INTO PUR_ITEM_GROUP_MST(MAIN_CODE,MAIN_DESC) VALUES ('M1','Group')")
        connection.execute("INSERT INTO PUR_ITEM_GROUP_DTL(SUB_CODE,SUB_DESC,MAIN_CODE) VALUES ('S1','Subgroup','M1')")
        connection.execute("UPDATE PUR_ITEM SET SUB_CODE='S1',UOM_ID_SMALL='1',UOM_ID_LARGE='2',QTY_SMALL_UOM='12',PUR_CODE='purchases',EXP_CODE='sales',LPUR_RATE='2'")
        connection.execute("UPDATE OPEN_STOCK SET UOM_ID_SMALL='2'")
        connection.execute("INSERT INTO JMST(TRNID,TRNNO,VTYPE,CMP_ID,TRNDATE) VALUES ('1','1','6','1','2026-01-01T00:00:00')")
        connection.executemany("INSERT INTO JDTL(TRNID,CHCODE,DR,CR) VALUES ('1',?,?,?)", [
            ("customer", "100", "0"), ("equity", "0", "100")])


def _item_archive(path):
    _operational_archive(path)
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute("UPDATE PUR_ITEM SET ICODE='5000'")
        connection.execute("UPDATE OPEN_STOCK SET ICODE='5000'")


def _item_form_case(path):
    from bifma.application import Application
    from bifma.baseline import ensure_baseline
    from bifma.native_ui import label
    from bifma.security import SecurityService
    from tools.smoke_native_ui import button, field, widgets

    working = ensure_baseline(path, path.parent / 'bifma.sqlite3').path
    SecurityService(working).bootstrap_admin('owner', 'Copper orchard 42 window violet')
    app = Application(path)
    panel = app.transactions_workspace
    errors = []
    app.report_callback_exception = lambda kind, value, tb: errors.append((kind.__name__, str(value)))
    def drain():
        _pump(app, lambda: panel.future is None and panel._startup_job is None)
        _check(not errors, repr(errors))
    def window(title):
        drain()
        return next(widget for widget in panel.winfo_children()
                    if isinstance(widget, tk.Toplevel) and widget.title() == title)
    def entry(parent, title):
        heading = next(widget for widget in widgets(parent)
                       if isinstance(widget, ttk.Label) and widget.cget('text') == title)
        return next(widget for widget in heading.master.winfo_children() if isinstance(widget, ttk.Entry))
    def item_controls(dialog, code):
        titles = {widget.cget('text') for widget in widgets(dialog) if isinstance(widget, ttk.Label)}
        _check(titles.isdisjoint({'Sales account', 'Purchase account', 'Inventory account'}),
               'Item details still ask for party/account mappings')
        preview = entry(dialog, 'Item code')
        _check(preview.get() == code and preview.instate(['readonly']),
               'Item code is not the expected automatic readonly value')
        preview.insert(0, '9')
        _check(preview.get() == code, 'Readonly item code accepted a manual edit')
    def records(entity):
        return panel.masters.list_records(panel.actor, entity, limit=100)
    with patch('bifma.native_ui.messagebox.showerror', side_effect=lambda title, message, **kwargs: errors.append((title, message))), \
         patch('bifma.native_ui.messagebox.showinfo'):
        try:
            app.update()
            drain()
            field(panel, 'User', 'owner')
            field(panel, 'Password', 'Copper orchard 42 window violet')
            button(panel, 'OK')
            _pump(app, lambda: panel.actor is not None and panel.future is None)
            original = next(row for row in records('products') if row['code'] == '5000')
            mappings = tuple(original[key] for key in ('sale_account_id', 'purchase_account_id', 'inventory_account_id'))
            base_unit = next(row for row in records('units') if row['id'] == original['small_unit_id'])
            group = next(row for row in records('product_groups') if row['id'] == original['group_id'])
            panel.master_screen('products')
            drain()
            for code, name in (('5001', 'Auto item one'), ('5002', 'Auto item two')):
                button(panel, 'New')
                dialog = window('New Items')
                item_controls(dialog, code)
                field(dialog, 'Item description', name)
                field(dialog, 'Group', label(group))
                field(dialog, 'Base unit', label(base_unit))
                field(dialog, 'Sale rate per base unit', '5')
                field(dialog, 'Purchase rate per base unit', '2')
                button(dialog, 'Save changes')
                drain()
                saved = next(row for row in records('products') if row['name'] == name)
                _check(saved['code'] == code, 'Saved code differs from the next numeric item code')
                _check((saved['sale_account_id'], saved['purchase_account_id']) == mappings[:2]
                       and saved['inventory_account_id'] is None,
                       'New item lost the established internal posting accounts')
                trees = [widget for widget in widgets(panel.main) if isinstance(widget, ttk.Treeview)]
                _check(any(code in tree.item(row, 'values') for tree in trees for row in tree.get_children()),
                       'Saved item did not appear with its allocated code')
            panel.master_dialog('products', original)
            dialog = window('Edit Items')
            item_controls(dialog, '5000')
            field(dialog, 'Item description', 'Updated original item')
            button(dialog, 'Save changes')
            drain()
            edited = next(row for row in records('products') if row['id'] == original['id'])
            _check(edited['code'] == '5000' and edited['name'] == 'Updated original item',
                   'Editing item details changed its established code')
            _check(tuple(edited[key] for key in ('sale_account_id', 'purchase_account_id', 'inventory_account_id')) == mappings,
                   'Removing account controls cleared saved item posting accounts')
            panel.master_dialog('accounts')
            dialog = window('New Accounts')
            _check(not entry(dialog, 'Code').instate(['readonly']), 'Party account code became an automatic item code')
            field(dialog, 'Code', '9000')
            field(dialog, 'Name', 'Separate trading party')
            field(dialog, 'Kind', 'both')
            button(dialog, 'Save changes')
            drain()
            party = next(row for row in records('accounts') if row['code'] == '9000')
            _check(party['kind'] == 'both', 'Separate customer/supplier account form was removed')
            panel.master_dialog('products')
            dialog = window('New Items')
            item_controls(dialog, '5003')
            field(dialog, 'Item description', 'Item saved after concurrent creation')
            field(dialog, 'Group', label(group))
            field(dialog, 'Base unit', label(base_unit))
            concurrent = panel.masters.create_product(panel.actor, {
                'name': 'Item from another operator', 'small_unit_id': base_unit['id'], 'group_id': group['id'],
            })
            _check(concurrent['code'] == '5003', 'An unsaved preview incorrectly reserved its item code')
            button(dialog, 'Save changes')
            drain()
            actual = next(row for row in records('products') if row['name'] == 'Item saved after concurrent creation')
            _check(actual['code'] == '5004', 'Saving a stale preview reused another item code')
            trees = [widget for widget in widgets(panel.main) if isinstance(widget, ttk.Treeview)]
            _check(any('5004' in tree.item(row, 'values') for tree in trees for row in tree.get_children()),
                   'Items list displayed the stale preview instead of the allocated code')
            product = next(row for row in records('products') if row['code'] == '5001')
            store = records('stores')[0]
            for kind, title, quantity, rate in (('purchase', 'Purchases', '3', '2'), ('sale', 'Sale Voucher', '1', '5')):
                panel.document_editor(kind)
                editor = window('New · ' + title)
                if kind == 'sale':
                    _sale_buyer(editor, party['code'])
                    row = _enter_sale_line(editor, product['code'], quantity, store=store['id'])
                    _check(row.widgets['rate'].get() == rate, 'New item lost its automatic sale price')
                else:
                    field(editor, 'Party account', label(party))
                    button(editor, 'Add line')
                    line = window('Add line')
                    field(line, 'Item', label(product))
                    field(line, 'Store', label(store))
                    field(line, 'Quantity in base units', quantity)
                    button(line, 'Use stored rate')
                    _check(entry(line, 'Rate per base unit').get() == rate, 'New item lost its stored trading price')
                    button(line, 'Use line')
                _save_editor(editor)
                drain()
                review = next(widget for widget in panel.winfo_children()
                              if isinstance(widget, tk.Toplevel) and widget.title().startswith('Review draft'))
                button(review, 'Post document')
                drain()
                posted = next(widget for widget in panel.winfo_children()
                              if isinstance(widget, tk.Toplevel) and widget.title().startswith('Posted ·'))
                button(posted, 'Close')
                drain()
            stock = panel.transactions.stock_balance(panel.actor, panel.company_id,
                                                     product_id=product['id'], store_id=store['id'])
            _check(len(stock) == 1 and stock[0]['quantity'] == '2',
                   'Automatically coded item cannot continue through purchase and sale')
            ledger = panel.reports.ledger(panel.actor, panel.company_id, party['id'])
            _check(ledger['debit'] == 5 and ledger['credit'] == 6 and ledger['closing'] == -1,
                   'Trading on the new item did not use the separate party account')
        finally:
            app.close()
            if not app._closed:
                app.wait_window(app)
            gc.collect()


def _operational_case(path):
    from bifma.application import Application
    from bifma.native_ui import label
    from tools.smoke_native_ui import button, field, widgets

    app = None
    errors = []
    def collect(title, message, **kwargs):
        errors.append((title, message))
    def drain():
        _pump(app, lambda: app.transactions_workspace.future is None
              and app.transactions_workspace._startup_job is None)
        _check(not errors, repr(errors))
    def labels(parent):
        return [str(widget.cget('text')) for widget in widgets(parent) if isinstance(widget, ttk.Label)]
    def entry(parent, title):
        heading = next(w for w in widgets(parent) if isinstance(w, ttk.Label) and w.cget('text') == title)
        return next(w for w in heading.master.winfo_children() if isinstance(w, ttk.Entry))
    def dialog(title):
        drain()
        return next(w for w in app.transactions_workspace.winfo_children()
                    if isinstance(w, tk.Toplevel) and w.title() == title)
    def authenticate(initial):
        panel = app.transactions_workspace
        field(panel, 'User', 'owner')
        field(panel, 'Password', 'Copper orchard 42 window violet')
        if initial:
            field(panel, 'User Name', 'Synthetic owner')
            field(panel, 'Confirm password', 'Copper orchard 42 window violet')
        button(panel, 'Create administrator' if initial else 'OK')
        _pump(app, lambda: panel.actor is not None and panel.future is None)
        _check(not errors, repr(errors))
    def finish_document(kind, amount):
        panel = app.transactions_workspace
        _save_editor(dialog('New · ' + kind))
        drain()
        review = next(w for w in panel.winfo_children()
                      if isinstance(w, tk.Toplevel) and w.title().startswith('Review draft'))
        button(review, 'Post document')
        drain()
        posted = next(w for w in panel.winfo_children()
                      if isinstance(w, tk.Toplevel) and w.title().startswith('Posted ·'))
        _check(any('Total: ' + amount in text for text in labels(posted)), 'Wrong posted total')
        button(posted, 'Close')
        drain()
    with patch('bifma.native_ui.filedialog.askopenfilename', side_effect=AssertionError('Unexpected database picker')), \
         patch('bifma.native_ui.filedialog.asksaveasfilename', side_effect=AssertionError('Unexpected empty database creation')), \
         patch('bifma.native_ui.messagebox.showerror', side_effect=collect):
        try:
            app = Application(path)
            app.geometry('1050x720')
            app.report_callback_exception = lambda kind, value, tb: errors.append((kind.__name__, str(value)))
            _check(not hasattr(app, 'workspace_buttons') and not hasattr(app, 'history_host'),
                   'Startup still exposes separate record and transaction shells')
            app.update()
            drain()
            panel = app.transactions_workspace
            _check(panel.path == path.parent / 'bifma.sqlite3' and panel.baseline is not None,
                   'Startup did not automatically prepare the existing baseline')
            _check(panel._login_form is not None and 'confirm' in panel._login_form.widgets,
                   'Fresh baseline did not request personal credentials')
            authenticate(True)
            app.update()
            details_button = next(widget for widget in widgets(panel) if isinstance(widget, ttk.Button)
                                  and widget.cget('text') == 'View starting balance details')
            _check(details_button.winfo_height() >= details_button.winfo_reqheight(),
                   'Starting-balance action was clipped at the minimum window size')
            _check(len(panel.companies) == 1, 'Imported company missing after owner setup')
            _check(not any('Set up your company' in text for text in labels(panel)), 'User was sent to empty setup')
            _check(any('Balances continue from your existing records' in text for text in labels(panel)), 'Overview omits integrated baseline')
            choices = panel.masters.lookup(panel.actor, panel.company_id, 'sales', action='create')
            product, store = choices['products'][0], choices['stores'][0]
            customer = next(row for row in choices['accounts'] if row['code'] == 'customer')
            _check(panel.transactions.stock_balance(panel.actor, panel.company_id, store_id=store['id'], product_id=product['id'])[0]['quantity'] == '10',
                   'Historical stock unavailable for new postings')
            panel.document_editor('sale')
            sale = dialog('New · Sale Voucher')
            _sale_buyer(sale, customer['code'])
            form = _sale_form(sale)
            _check(form.widgets['document_number'].get() == '1', 'New document number was not suggested')
            cutoff = date.fromisoformat(panel.baseline.cutoff_date)
            _check(form.widgets['business_date'].get() == max(date.today(), cutoff + timedelta(days=1)).isoformat(),
                   'New document date entered the preserved historical interval')
            row = _enter_sale_line(sale, product['code'], '2', store=store['id'])
            _check(row.widgets['rate'].get() == '' and 'No saved sale price' in form.notice.get(),
                   'Unknown price appeared as a known zero or lacked its explanation')
            row.widgets['rate'].insert(0, '10')
            finish_document('Sale Voucher', '20')
            panel.document_editor('cash_receipt')
            receipt = dialog('New · Cash Receipt Voucher')
            _enter_cash_line(receipt, customer['code'], '25')
            finish_document('Cash Receipt Voucher', '25')
            _check(panel.transactions.stock_balance(panel.actor, panel.company_id, store_id=store['id'], product_id=product['id'])[0]['quantity'] == '8',
                   'New sale did not continue historical stock')
            balance = panel.reports.ledger(panel.actor, panel.company_id, customer['id'], date_from='2026-01-02')
            _check(balance['opening'] == 100 and balance['closing'] == 95,
                   'Cash receipt and sale did not continue the original customer balance')
            panel.report_screen()
            field(panel, 'Report', 'Stock valuation')
            button(panel, 'Run report')
            filters = dialog('Stock valuation filters')
            button(filters, 'Show stock valuation')
            drain()
            _check(any('Closing stock value: 16' in text for text in labels(panel)),
                   'Stock value did not continue preserved value20 less saved sale cost4')
            output = path.parent / 'stock-valuation.html'
            with patch('bifma.native_ui.filedialog.asksaveasfilename', return_value=str(output)), \
                 patch('bifma.native_ui.messagebox.showinfo'):
                button(panel, 'Save full report')
                drain()
            _check(output.is_file() and 'Stock valuation' in output.read_text(), 'Stock valuation export unavailable')
            field(panel, 'Report', 'Profit and loss')
            button(panel, 'Run report')
            drain()
            _check(any('Periodic trading profit: Review required' in text for text in labels(panel)),
                   'Unclassified original accounts were treated as a verified profit')
            _check(any('accounts need a classification' in text for text in labels(panel)),
                   'Profit report omitted the action needed for original classifications')
            classifications = {'cash': 'asset', 'customer': 'asset', 'equity': 'equity',
                               'sales': 'income', 'purchases': 'expense'}
            for account in panel.masters.list_records(panel.actor, 'accounts'):
                panel.masters.update(panel.actor, 'accounts', account['id'],
                                     {'classification': classifications[account['code']]}, account['version'])
            button(panel, 'Run report')
            drain()
            _check(any('Periodic trading profit: 16' in text for text in labels(panel)),
                   'Classified profit report did not apply income20 less stock cost4')
            output = path.parent / 'profit-loss.html'
            with patch('bifma.native_ui.filedialog.asksaveasfilename', return_value=str(output)), \
                 patch('bifma.native_ui.messagebox.showinfo'):
                button(panel, 'Save full report')
                drain()
            _check(output.is_file() and 'Periodic trading profit' in output.read_text(), 'Profit report export unavailable')
            panel.document_list('opening_balance')
            drain()
            _check(any(row['source'] == 'original' and row['document_number'] == '1'
                       for row in panel.document_rows), 'Original opening voucher missing from its module')
            app.close()
            if not app._closed:
                app.wait_window(app)
            app = Application(path)
            app.report_callback_exception = lambda kind, value, tb: errors.append((kind.__name__, str(value)))
            app.update()
            drain()
            _check(app.transactions_workspace._login_form is not None
                   and 'confirm' not in app.transactions_workspace._login_form.widgets,
                   'Restart repeated administrator bootstrap')
            authenticate(False)
            panel = app.transactions_workspace
            _check(panel.reports.dashboard(panel.actor, panel.company_id)['posted'] == 2, 'Restart lost posted documents')
            _check(panel.transactions.stock_balance(panel.actor, panel.company_id, store_id=store['id'], product_id=product['id'])[0]['quantity'] == '8',
                   'Restart duplicated the imported starting stock')
        finally:
            if app is not None:
                app.close()
                if not app._closed:
                    app.wait_window(app)
            gc.collect()


def _historical_return_case(path):
    from bifma.application import Application
    from bifma.baseline import ensure_baseline
    from bifma.native_schema import native_session
    from bifma.security import SecurityService
    from tools.smoke_native_ui import button, field, widgets

    target = path.parent / 'bifma.sqlite3'
    ensure_baseline(path, target)
    SecurityService(target).bootstrap_admin('owner', 'Copper orchard 42 window violet')
    app = Application(path)
    errors = []
    app.report_callback_exception = lambda kind, value, tb: errors.append((kind.__name__, str(value)))
    panel = app.transactions_workspace
    def drain():
        _pump(app, lambda: panel.future is None and panel._startup_job is None)
        _check(not errors, repr(errors))
    def window(title):
        drain()
        return next(w for w in panel.winfo_children() if isinstance(w, tk.Toplevel) and w.title() == title)
    def select_first(parent):
        tree = next(w for w in widgets(parent) if isinstance(w, ttk.Treeview))
        _check(bool(tree.get_children()), 'No eligible original documents or lines')
        tree.selection_set(tree.get_children()[0])
        return tree.item(tree.get_children()[0], 'values')
    with patch('bifma.native_ui.messagebox.showerror', side_effect=lambda title, message, **kwargs: errors.append((title, message))):
        try:
            app.update()
            drain()
            field(panel, 'User', 'owner')
            field(panel, 'Password', 'Copper orchard 42 window violet')
            button(panel, 'OK')
            _pump(app, lambda: panel.actor is not None and panel.future is None)
            panel.document_editor('sale_return')
            editor = window('New · Sales returns')
            field(editor, 'Header discount', '1')
            button(editor, 'Add line')
            line = window('Add line')
            button(line, 'Choose original line')
            picker = window('Choose the original document')
            _check('Original history' in select_first(picker), 'Historical invoice lacks its source label')
            button(picker, 'Show lines')
            drain()
            select_first(picker)
            button(picker, 'Use selected line')
            field(line, 'Quantity in base units', '1')
            button(line, 'Use line')
            button(editor, 'Save draft & review')
            drain()
            review = next(w for w in panel.winfo_children() if isinstance(w, tk.Toplevel) and w.title().startswith('Review draft'))
            button(review, 'Post document')
            drain()
            posted = next(w for w in panel.winfo_children() if isinstance(w, tk.Toplevel) and w.title().startswith('Posted ·'))
            button(posted, 'Close')
            drain()
            stock = panel.transactions.stock_balance(panel.actor, panel.company_id)
            _check(len(stock) == 1 and stock[0]['quantity'] == '8', 'Return did not restore stock against imported original')
            accounts = panel.reports.accounts(panel.actor, panel.company_id)
            party = next(row for row in accounts if row['code'] == '0002')
            balance = panel.reports.ledger(panel.actor, panel.company_id, party['id'], date_from='2026-01-04')
            _check(balance['opening'] == 20 and balance['closing'] == 11, 'Staff-entered return adjustment did not update carried balance')
            with native_session(target) as connection:
                _check(connection.execute("SELECT COUNT(*) FROM documents WHERE kind='sale'").fetchone()[0] == 0,
                       'Returning original history forged a new native invoice')
                _check(connection.execute("SELECT COUNT(*) FROM documents WHERE kind='sale_return' AND status='posted'").fetchone()[0] == 1,
                       'Historical return did not post exactly once')
        finally:
            app.close()
            if not app._closed:
                app.wait_window(app)
            gc.collect()


def _unified_module_case(path):
    from bifma.application import Application
    from bifma.baseline import ensure_baseline
    from bifma.native_ui import label
    from bifma.security import SecurityService
    from tools.smoke_native_ui import button, field, widgets

    working = ensure_baseline(path, path.parent / 'bifma.sqlite3').path
    SecurityService(working).bootstrap_admin('owner', 'Copper orchard 42 window violet')
    app = Application(path)
    panel = app.transactions_workspace
    errors = []
    app.report_callback_exception = lambda kind, value, tb: errors.append((kind.__name__, str(value)))
    def drain():
        _pump(app, lambda: panel.future is None and panel._startup_job is None)
        _check(not errors, repr(errors))
    def window(title):
        drain()
        return next(w for w in panel.winfo_children() if isinstance(w, tk.Toplevel) and w.title() == title)
    def post_editor(editor):
        _save_editor(editor)
        drain()
        review = next(w for w in panel.winfo_children() if isinstance(w, tk.Toplevel) and w.title().startswith('Review draft'))
        button(review, 'Post document')
        drain()
        posted = next(w for w in panel.winfo_children() if isinstance(w, tk.Toplevel) and w.title().startswith('Posted ·'))
        button(posted, 'Close')
        drain()
    with patch('bifma.native_ui.messagebox.showerror', side_effect=lambda title, message, **kwargs: errors.append((title, message))):
        try:
            app.update()
            drain()
            field(panel, 'User', 'owner')
            field(panel, 'Password', 'Copper orchard 42 window violet')
            button(panel, 'OK')
            _pump(app, lambda: panel.actor is not None and panel.future is None)
            _check(not hasattr(app, 'history_host')
                   and len([child for child in app.winfo_children() if not isinstance(child, tk.Menu)]) == 1,
                   'Application retained a second browsing shell')
            buttons = {w.cget('text') for w in widgets(app) if isinstance(w, (tk.Button, ttk.Button))}
            _check(buttons.isdisjoint({'Workspace', 'History', 'Existing records', 'Transactions & setup'}),
                   'The application still offers separate data workspaces')
            panel.document_list('sale')
            drain()
            _check([(row['source'], row['document_number']) for row in panel.document_rows] == [('original', '260001')],
                   'Sales module did not show the original invoice')
            original = panel.document_rows[0]
            panel.open_document(original['id'], source='original', kind='sale')
            drain()
            old_dialog = next(w for w in panel.winfo_children() if isinstance(w, tk.Toplevel))
            controls = {w.cget('text') for w in widgets(old_dialog) if isinstance(w, ttk.Button)}
            _check('Edit draft' not in controls and 'Post document' not in controls,
                   'Original document was exposed as a mutable new draft')
            button(old_dialog, 'Close')
            drain()
            choices = panel.masters.lookup(panel.actor, panel.company_id, 'sales', action='create')
            product, store = choices['products'][0], choices['stores'][0]
            party = next(row for row in choices['accounts'] if row['code'] == '0002')
            button(panel, 'New document')
            editor = window('New · Sale Voucher')
            _sale_buyer(editor, party['code'])
            _enter_sale_line(editor, product['code'], '2', '10', store=store['id'])
            post_editor(editor)
            _check({(row['source'], row['document_number']) for row in panel.document_rows}
                   == {('original', '260001'), ('current', '260002')},
                   'Posting did not return to the same Sales list with both original and new invoices')
            panel.document_list('sale', '260001', 0)
            drain()
            _check(len(panel.document_rows) == 1 and panel.document_rows[0]['source'] == 'original',
                   'Combined literal search lost the original invoice')
            panel.document_list('sale', '260002', 0)
            drain()
            _check(len(panel.document_rows) == 1 and panel.document_rows[0]['source'] == 'current',
                   'Combined literal search lost the new invoice')
            panel.document_list('sale', '', 0, date_to='2026-01-02')
            drain()
            _check(len(panel.document_rows) == 1 and panel.document_rows[0]['source'] == 'original',
                   'Date filter did not apply to original and new invoices together')
            panel.document_list('sale', '', 0, date_from=date.today().isoformat())
            drain()
            _check(len(panel.document_rows) == 1 and panel.document_rows[0]['source'] == 'current',
                   'Current date filter included pre-cutoff originals')
            panel.document_list('cash_receipt')
            drain()
            _check([(row['source'], row['document_number']) for row in panel.document_rows] == [('original', '26')],
                   'Cash receipts module omitted original cash records')
            button(panel, 'New document')
            editor = window('New · Cash Receipt Voucher')
            _enter_cash_line(editor, party['code'], '15')
            post_editor(editor)
            _check({(row['source'], row['document_number']) for row in panel.document_rows}
                   == {('original', '26'), ('current', '27')},
                   'Cash receipt entry did not refresh the same combined module list')
            stock = panel.transactions.stock_balance(panel.actor, panel.company_id,
                                                     product_id=product['id'], store_id=store['id'])
            _check(stock[0]['quantity'] == '5', 'Unified list entry lost original stock7 minus new sale2')
            ledger = panel.reports.ledger(panel.actor, panel.company_id, party['id'], date_from='2026-01-04')
            _check(ledger['opening'] == 20 and ledger['closing'] == 25,
                   'Unified entry lost original account20 plus new sale20 minus receipt15')
            sale = next(row for row in panel.register.list_documents(panel.actor, panel.company_id, 'sale')
                        if row['source'] == 'current' and row['kind'] == 'sale')
            saved = panel.transactions.get(panel.actor, panel.company_id, sale['id'])
            correction = panel.transactions.reverse(panel.actor, panel.company_id, saved['id'], saved['revision'],
                                                      document_number='REV-LIST-1', business_date=saved['business_date'],
                                                      reason='Synthetic display verification')
            panel.document_list('sale', '', 0)
            drain()
            for index, row in enumerate(panel.document_rows):
                if row['id'] in {saved['id'], correction['id']}:
                    expected = 'Reversed' if row['id'] == saved['id'] else 'Reversal'
                    _check(panel.document_tree.set(str(index), 'status_display') == expected,
                           'Sales list presented a reversed invoice or correction as an ordinary posted sale')
            panel.open_document(correction['id'], source='current', kind='sale')
            drain()
            corrected = next(w for w in panel.winfo_children() if isinstance(w, tk.Toplevel))
            button(corrected, 'Close')
            drain()
            _check({row['id'] for row in panel.document_rows} == {original['id'], saved['id'], correction['id']},
                   'Closing a linked correction left its Sales list')
            panel.open_document(original['id'], source='original', kind='sale')
            drain()
            panel.logout()
            drain()
            _check(panel.actor is None and not any(isinstance(w, tk.Toplevel) for w in panel.winfo_children()),
                   'Signing out left an original document visible')
            with patch('bifma.native_ui.messagebox.showerror') as denied:
                panel.open_document(original['id'], source='original', kind='sale')
                drain()
                _check(denied.call_count == 1, 'Original records could be reopened after signing out')
            _check(not any(isinstance(w, tk.Toplevel) for w in panel.winfo_children()),
                   'Unauthenticated original detail was displayed')
        finally:
            app.close()
            if not app._closed:
                app.wait_window(app)
            gc.collect()


def _cash_layout_case(path):
    """Inline cash entry keeps drafts safe and prints only through real posting."""
    from bifma.application import Application
    from bifma.baseline import ensure_baseline
    from bifma.security import SecurityService
    from tools.smoke_native_ui import button, field, widgets

    working = ensure_baseline(path, path.parent / 'bifma.sqlite3').path
    SecurityService(working).bootstrap_admin('owner', 'Copper orchard 42 window violet')
    app = Application(path)
    app.geometry('1050x720')
    panel = app.transactions_workspace
    errors = []
    app.report_callback_exception = lambda kind, value, tb: errors.append((kind.__name__, str(value)))

    def drain():
        app.update()
        _pump(app, lambda: panel.future is None and panel._startup_job is None)
        app.update()
        _check(not errors, repr(errors))

    def window(prefix):
        drain()
        matches = [widget for widget in panel.winfo_children()
                   if isinstance(widget, tk.Toplevel) and widget.title().startswith(prefix)]
        _check(len(matches) == 1, 'Expected one voucher window: ' + prefix)
        return matches[0]

    with patch('bifma.native_ui.messagebox.showerror', side_effect=lambda title, message, **kwargs: errors.append((title, message))), \
         patch('bifma.native_ui.messagebox.showinfo'):
        try:
            drain()
            field(panel, 'User', 'owner')
            field(panel, 'Password', 'Copper orchard 42 window violet')
            button(panel, 'OK')
            drain()
            _invoke_menu(app, 'Financial Transactions', 'Cash Receipt Voucher')
            editor = window('New · Cash Receipt Voucher')
            form = _cash_form(editor)
            _check(form.inputs['document_number'].get() == '27' and form.last_number.get() == '26',
                   'Voucher numbering did not continue the original receipt series')
            _check(not any(isinstance(widget, ttk.Treeview) for widget in widgets(editor)),
                   'Cash voucher still requires a separate line-table editor')
            editor.geometry('1010x680')
            drain()
            for name, control in form.buttons.items():
                _check(control.winfo_viewable()
                       and control.winfo_rooty() + control.winfo_height() <= editor.winfo_rooty() + editor.winfo_height(),
                       'Cash action is clipped at the minimum layout: ' + name)
            form.rows[0].widgets['code'].insert(0, '00')
            form.rows[0].widgets['amount'].insert(0, '9')
            with patch('bifma.native_ui.messagebox.askyesno', return_value=False) as confirm:
                button(editor, 'New')
                drain()
                _check(confirm.call_count == 1 and editor.winfo_exists()
                       and form.rows[0].widgets['code'].get() == '00',
                       'New discarded an incomplete account row without consent')
                button(editor, 'Exit')
                drain()
                _check(confirm.call_count == 2 and editor.winfo_exists(),
                       'Exit discarded changed cash entry without consent')
            with patch('bifma.native_ui.messagebox.askyesno', return_value=True):
                button(editor, 'New')
                editor = window('New · Cash Receipt Voucher')
            form = _cash_form(editor)
            _check(not form.payload()['lines'] and form.inputs['document_number'].get() == '27',
                   'Clearing unsaved cash entry created a voucher or consumed its number')
            first = _enter_cash_line(editor, '0002', '11.25', 'First cash line')
            _enter_cash_line(editor, '0002', '2.80', 'Second cash line', index=1)
            _check(form.total.get() == '14.05', 'Inline cash total did not use both exact decimal amounts')
            first.widgets['narration'].focus_force()
            app.update()
            first.widgets['narration'].event_generate('<F8>')
            browser = window('Saved records · Cash Receipt Voucher')
            _check([row['source'] for row in panel.document_rows] == ['original'],
                   'Discarding an entry or browsing created a saved voucher')
            button(browser, 'Back to entry')
            drain()
            _check(form.payload()['lines'][1]['narration'] == 'Second cash line',
                   'F8 lost the second inline cash row')
            before = panel.reports.ledger(panel.actor, panel.company_id, first.inputs['account_id'].get())['closing']
            button(editor, 'Print')
            review = window('Review draft · 27')
            saved = [row for row in panel.register.list_documents(panel.actor, panel.company_id, 'cash_receipt')
                     if row['source'] == 'current']
            _check(len(saved) == 1 and saved[0]['status'] == 'draft',
                   'Print bypassed review or failed to save exactly one draft')
            party = next(row for row in panel.masters.list_records(panel.actor, 'accounts') if row['code'] == '0002')
            _check(panel.reports.ledger(panel.actor, panel.company_id, party['id'])['closing'] == before,
                   'Printing a reviewed draft changed the account balance before posting')
            output = path.parent / 'cash-receipt.html'
            with patch('bifma.native_ui.filedialog.asksaveasfilename') as chooser, patch('webbrowser.open', return_value=True) as opener:
                button(review, 'Post and print')
                posted = window('Posted · 27')
                drain()
                _check(chooser.call_count == 0 and opener.call_count == 1, 'Post and print did not directly open its page preview')
                from urllib.parse import urlsplit
                from urllib.request import url2pathname
                preview_path = Path(url2pathname(urlsplit(opener.call_args.args[0]).path))
                _check(preview_path.exists(), 'Post and print did not publish its real private HTML preview')
            html = preview_path.read_text(encoding='utf-8')
            _check('First cash line' in html and 'Second cash line' in html and '27' in html,
                   'Cash print lost the saved voucher or inline row narrations')
            with patch('bifma.native_ui.filedialog.asksaveasfilename', return_value=str(output)), patch('bifma.native_ui.messagebox.showinfo'):
                button(posted, 'Save printable copy')
                drain()
            _check(output.read_text(encoding='utf-8') == html, 'Saved copy differed from the authorized print preview')
            _check(panel.reports.ledger(panel.actor, panel.company_id, party['id'])['closing'] == before - Decimal('14.05'),
                   'Multirow cash receipt did not post its exact full amount once')
            button(posted, 'Close')
            editor = window('New · Cash Receipt Voucher')
            form = _cash_form(editor)
            _check(form.inputs['document_number'].get() == '28' and form.last_number.get() == '27'
                   and not form.payload()['lines'], 'Posted cash voucher did not return to the next blank form')
            button(editor, 'Exit')
            drain()
        finally:
            app.close()
            if not app._closed:
                app.wait_window(app)
            gc.collect()


def _grouped_navigation_case(path):
    """Exercise the owner's menu layout and preserve already saved corrections."""
    from bifma.application import Application
    from bifma.baseline import ensure_baseline
    from bifma.native_ui import label
    from bifma.security import SecurityService
    from tools.smoke_native_ui import button, field, widgets

    working = ensure_baseline(path, path.parent / 'bifma.sqlite3').path
    SecurityService(working).bootstrap_admin('owner', 'Copper orchard 42 window violet')
    app = Application(path)
    panel = app.transactions_workspace
    errors = []
    app.report_callback_exception = lambda kind, value, tb: errors.append((kind.__name__, str(value)))
    groups = ('File', 'Coding', 'Financial Transactions', 'Inventory', 'Reports', 'Setup')
    selected_section = None

    def drain():
        _pump(app, lambda: panel.future is None and panel._startup_job is None)
        app.update_idletasks()
        _check(not errors, repr(errors))

    def section(title):
        nonlocal selected_section
        _menu_section(app, title)
        selected_section = title
        drain()

    def menu(title):
        _invoke_menu(app, selected_section, title)
        drain()

    def window(title):
        drain()
        return next(widget for widget in panel.winfo_children()
                    if isinstance(widget, tk.Toplevel) and widget.title() == title)

    def controls(parent):
        return {str(widget.cget('text')) for widget in widgets(parent)
                if isinstance(widget, (tk.Button, ttk.Button))}

    def visible_menu():
        return {title for _, _, title in _menu_commands(_menu_section(app, selected_section))}

    def navigation_disabled(reason):
        menubar = _application_menu(app)
        _check(all(str(menubar.entrycget(index, 'state')) == 'disabled'
                   for index, _ in _menu_entries(menubar)), reason)

    def browse(entry, title):
        control = next(widget for widget in widgets(entry) if isinstance(widget, ttk.Entry))
        control.focus_force()
        app.update()
        control.event_generate('<F8>')
        return window('Saved records · ' + title)

    with patch('bifma.native_ui.messagebox.showerror', side_effect=lambda title, message, **kwargs: errors.append((title, message))):
        try:
            app.update()
            drain()
            _check(not str(app.cget('menu')), 'Authenticated menu commands were exposed before sign-in')
            field(panel, 'User', 'owner')
            field(panel, 'Password', 'Copper orchard 42 window violet')
            button(panel, 'OK')
            _pump(app, lambda: panel.actor is not None and panel.future is None)
            _check(tuple(title for _, title in _menu_entries(_application_menu(app))) == groups,
                   'The application did not present the requested sections across the top')
            _check(not any(isinstance(widget, tk.Button)
                           and str(widget.cget('text')).startswith(('▸ ', '▾ ')) for widget in widgets(panel)),
                   'The old collapsible sidebar is still displayed')
            removed = {'Bank receipts', 'Bank payments', 'Bank Receipt Voucher', 'Bank Payment Voucher',
                       'Reversals', 'Reverse document', 'Post reversal'}
            _check({title for _, _, title in _menu_commands(_application_menu(app))}.isdisjoint(removed),
                   'Removed bank/reversal entry remains in navigation')

            section('Coding')
            coding_menu = _menu_section(app, 'Coding')
            _check({'Financial Coding', 'Inventory Coding'} <= {title for _, title in _menu_entries(coding_menu)},
                   'Coding lacks the original financial/inventory submenu groups')
            _check({'Accounts', 'Items', 'Cities', 'Opening stock'} <= visible_menu(),
                   'Coding does not contain accounts, items, cities and opening stock')
            _check(visible_menu().isdisjoint({'Companies', 'Sale Voucher', 'Journal Voucher', 'Trial balance'}),
                   'Unrelated modules are still mixed into Coding')
            for title in ('Accounts', 'Items', 'Cities'):
                menu(title)
                entry = window('New ' + title)
                navigation_disabled('Opening an entry left the top menu enabled')
                if title == 'Cities':
                    other_menu, other_index, _ = next(command for command in
                        _menu_commands(_menu_section(app, 'Inventory')) if command[2] == 'Sale Voucher')
                    other_menu.invoke(other_index)
                    drain()
                    _check(entry.winfo_exists() and [child for child in panel.winfo_children()
                           if isinstance(child, tk.Toplevel)] == [entry],
                           'A disabled menu replaced the unfinished entry')
                _check(not any(isinstance(widget, ttk.Treeview) for widget in widgets(panel.main)),
                       'Coding automatically displayed saved records')
                browser = browse(entry, title)
                button(browser, 'Back to entry')
                drain()
                if title == 'Cities':
                    field(entry, 'Code', 'NEW-CITY')
                    field(entry, 'Name', 'City saved through Coding')
                    button(entry, 'Save changes')
                    drain()
                    _check(any(row['code'] == 'NEW-CITY' for row in panel.masters.list_records(panel.actor, 'cities')),
                           'Coding city entry was not saved')
                    entry = window('New Cities')
                entry.destroy()
                drain()
            section('Setup')
            menu('Companies')
            entry = window('New Companies')
            browser = browse(entry, 'Companies')
            _check(not any(isinstance(widget, ttk.Combobox) and 'Items' in widget.cget('values')
                           for widget in widgets(browser)), 'Setup browser contains routine coding records')
            button(browser, 'Back to entry')
            entry.destroy()
            drain()

            section('Financial Transactions')
            financial = (('opening_balance', 'Opening Balance'), ('cash_payment', 'Cash Payment Voucher'),
                         ('cash_receipt', 'Cash Receipt Voucher'), ('journal', 'Journal Voucher'))
            _check(tuple(title for _, title in _menu_entries(_menu_section(app, 'Financial Transactions')))
                   == tuple(title for _, title in financial),
                   'Financial Transactions does not contain exactly the four requested entry types')
            accounts = {row['code']: row for row in panel.masters.list_records(panel.actor, 'accounts')}
            saved_documents = {}
            for kind, title in financial:
                menu(title)
                editor = window('New · ' + title)
                _check(not any(isinstance(widget, ttk.Treeview) for widget in widgets(panel.main)),
                       'Finance module automatically displayed saved records')
                if kind in {'cash_receipt', 'cash_payment'}:
                    titles = {widget.cget('text') for widget in widgets(editor) if isinstance(widget, ttk.Label)}
                    _check('Cash account' in titles and 'Cash / bank account' not in titles,
                           'Cash voucher still offers a bank entry form')
                for code, debit, credit in ([('0002', '2', '0'), ('0003', '0', '2')]
                                             if kind in {'opening_balance', 'journal'} else [('0002', None, None)]):
                    if debit is None:
                        _enter_cash_line(editor, code, '2')
                    else:
                        button(editor, 'Add line')
                        line = window('Add line')
                        field(line, 'Account', label(accounts[code]))
                        field(line, 'Debit', debit)
                        field(line, 'Credit', credit)
                        button(line, 'Use line')
                _save_editor(editor)
                drain()
                review = next(widget for widget in panel.winfo_children()
                              if isinstance(widget, tk.Toplevel) and widget.title().startswith('Review draft'))
                button(review, 'Post document')
                drain()
                posted = next(widget for widget in panel.winfo_children()
                              if isinstance(widget, tk.Toplevel) and widget.title().startswith('Posted ·'))
                _check(controls(posted).isdisjoint(removed), 'Posted voucher exposes removed reversal action')
                button(posted, 'Close')
                drain()
                editor = window('New · ' + title)
                browser = browse(editor, title)
                current = [row for row in panel.document_rows if row['source'] == 'current']
                _check(len(current) == 1 and current[0]['kind'] == kind,
                       'F8 did not include the saved finance document in the same section')
                saved_documents[kind] = panel.transactions.get(panel.actor, panel.company_id, current[0]['id'])
                button(browser, 'Back to entry')
                editor.destroy()
                drain()

            section('Inventory')
            _check({'Sale Voucher', 'Purchases', 'Sales returns', 'Purchase returns', 'Counter sales',
                    'Cash purchases', 'Breakage receive', 'Breakage dispatch'} <= visible_menu(),
                   'Inventory document entries are missing from Inventory')
            _check(visible_menu().isdisjoint({title for _, title in financial} | {'Opening stock', 'Items'}),
                   'Coding or finance entries are mixed into Inventory')
            menu('Sale Voucher')
            editor = window('New · Sale Voucher')
            browser = browse(editor, 'Sale Voucher')
            _check(any(row['source'] == 'original' for row in panel.document_rows),
                   'Moving Sales into Inventory hid its original records')
            button(browser, 'Back to entry')
            editor.destroy()
            drain()
            section('Reports')
            _check(tuple(title for _, title in _menu_entries(_menu_section(app, 'Reports')))
                   == ('Voucher Printing', 'Financial reports', 'Inventory reports'),
                   'Reports lost its financial/inventory submenu groups')
            for title in ('Trial balance', 'Account ledger', 'Stock quantities', 'Item ledger'):
                menu(title)
                if title == 'Account ledger':
                    filters = window('Party Ledger Report')
                    _check(filters.report_filter.report == title,
                           'Party Ledger menu did not open its dedicated filters')
                    filters.destroy()
                    drain()
                    continue
                heading = next(widget for widget in widgets(panel.main)
                               if isinstance(widget, ttk.Label) and widget.cget('text') == 'Report')
                selection = next(widget for widget in heading.master.winfo_children() if isinstance(widget, ttk.Combobox))
                _check(selection.get() == title, 'Individual report menu did not select its report')

            # Old corrections retain their document/list meaning after entry
            # removal. This fixture uses the retained compatibility service.
            original = saved_documents['journal']
            reversal = panel.transactions.reverse(panel.actor, panel.company_id, original['id'], original['revision'],
                document_number='SAVED-CORRECTION', business_date=original['business_date'],
                reason='Previously saved correction fixture')
            section('Financial Transactions')
            menu('Journal Voucher')
            editor = window('New · Journal Voucher')
            browser = browse(editor, 'Journal Voucher')
            _check({row['id'] for row in panel.document_rows} == {original['id'], reversal['id']},
                   'Removing reversal entry hid an already saved correction')
            for identifier in (original['id'], reversal['id']):
                panel.document_tree.selection_set(str(next(index for index, row in enumerate(panel.document_rows)
                                                          if row['id'] == identifier)))
                button(browser, 'Open selected')
                drain()
                detail = next(widget for widget in panel.winfo_children()
                              if isinstance(widget, tk.Toplevel) and widget.title().startswith('Posted ·'))
                _check(controls(detail).isdisjoint(removed), 'Existing voucher still exposes reversal entry')
                button(detail, 'Close')
                drain()
            button(browser, 'Back to entry')
            editor.destroy()
            drain()
            for kind in ('bank_receipt', 'bank_payment', 'reversal'):
                for route in (panel.document_editor, panel.document_list):
                    try:
                        route(kind)
                    except ValueError:
                        pass
                    else:
                        raise AssertionError('Removed entry route remains available: ' + kind)
            _check(not any(isinstance(widget, tk.Toplevel) for widget in panel.winfo_children()),
                   'A removed entry route opened a form')
            # Main-screen selectors belong to the embedded workspace; their
            # Find popup must block the same menu as a nested voucher lookup.
            from bifma.native_ui import Choice
            holder = ttk.Frame(panel.main)
            holder.pack()
            choice = Choice(holder, list(accounts.values()))
            choice.widget.pack()
            choice.search('Account')
            drain()
            picker = choice._search_window
            _pump(app, lambda: app.grab_current() is picker)
            navigation_disabled('A Find window from the main screen left menus enabled')
            target, index, _ = next(command for command in _menu_commands(_menu_section(app, 'Inventory'))
                                    if command[2] == 'Sale Voucher')
            target.invoke(index)
            drain()
            _check(picker.winfo_exists() and app.grab_current() is picker,
                   'A main-screen Find lost its grab through the blocked menu')
            picker.destroy()
            holder.destroy()
            drain()
            started, release = threading.Event(), threading.Event()
            def held_job():
                started.set()
                _check(release.wait(15), 'Busy-menu check did not release its worker')
            try:
                panel.run(held_job)
                _pump(app, started.is_set)
                navigation_disabled('An active worker left the top menu enabled')
                target, index, _ = next(command for command in _menu_commands(_menu_section(app, 'Coding'))
                                        if command[2] == 'Cities')
                target.invoke(index)
                app.update()
                _check(not any(isinstance(widget, tk.Toplevel) for widget in panel.winfo_children()),
                       'A menu opened an entry while another request was active')
            finally:
                release.set()
                drain()
            section('File')
            menu('Sign out')
            _check(panel.actor is None and not str(app.cget('menu')),
                   'Signing out left authenticated top menu commands attached')
            field(panel, 'User', 'owner')
            field(panel, 'Password', 'Copper orchard 42 window violet')
            button(panel, 'OK')
            drain()
            _check(tuple(title for _, title in _menu_entries(_application_menu(app))) == groups,
                   'Signing back in duplicated or lost top menu sections')
        finally:
            app.close()
            if not app._closed:
                app.wait_window(app)
            gc.collect()


def _case(mode, path):
    if mode == 'cash-layout':
        return _cash_layout_case(path)
    if mode == 'grouped-navigation':
        return _grouped_navigation_case(path)
    if mode == 'item-form':
        return _item_form_case(path)
    if mode == 'history-smoke':
        from bifma.application import main
        with patch('bifma.baseline.ensure_baseline', side_effect=AssertionError('Historical smoke attempted an import')), \
             redirect_stdout(io.StringIO()) as output:
            _check(main(['--database', str(path), '--smoke-test']) == 0, 'Historical smoke failed')
        _check('Existing-record smoke test passed' in output.getvalue(), 'Historical smoke did not finish')
        _check(not (path.parent / 'bifma.sqlite3').exists(), 'Historical smoke created an operational database')
        return
    if mode == 'baseline-startup':
        return _operational_case(path)
    if mode == 'historical-return':
        return _historical_return_case(path)
    if mode == 'unified-modules':
        return _unified_module_case(path)

    from bifma.application import Application
    from bifma.native_ui import NativeWorkspace
    from bifma.native_schema import initialize_database, native_session
    from bifma.database import session
    from tools.smoke_native_ui import button, field, widgets

    if mode == 'init-failure':
        unrelated = tk.Tk()
        unrelated.withdraw()
        try:
            with patch.object(NativeWorkspace, '_styles', side_effect=RuntimeError('synthetic init failure')):
                try:
                    Application(database=None)
                except RuntimeError as error:
                    _check(str(error) == 'synthetic init failure', 'Startup hid its actual failure')
                else:
                    raise AssertionError('Incomplete shell was returned after startup failure')
            _check(unrelated.winfo_exists() and tk._default_root is unrelated,
                   'Failed application startup destroyed another root')
            retry = Application(database=None)
            retry.update()
            _check(retry.transactions_workspace.winfo_exists(), 'Clean retry did not construct the shell')
            retry.close()
            if not retry._closed:
                retry.wait_window(retry)
        finally:
            unrelated.destroy()
            gc.collect()
        return

    destination = path.parent / 'native.sqlite3'
    if mode in {'shutdown', 'native-startup'}:
        initialize_database(destination)
    app = Application(database=path if mode == 'native-startup' else None,
                      transaction_database=destination if destination.exists() else None)
    panel = app.transactions_workspace
    errors = []
    app.report_callback_exception = lambda kind, value, tb: errors.append((kind.__name__, str(value)))
    release = threading.Event()
    messages = patch('bifma.native_ui.messagebox.showerror', side_effect=lambda title, message, **kwargs: errors.append((title, message)))
    messages.start()
    try:
        app.update()
        _pump(app, lambda: panel.future is None and panel._startup_job is None)
        _check(tk._default_root is app and panel.tk is app.tk and panel.winfo_toplevel() is app,
               'Single application did not own its only interpreter and window')
        _check(len([child for child in app.winfo_children() if not isinstance(child, tk.Menu)]) == 1
               and not hasattr(app, 'history_host'),
               'A second shell or navigation was retained')
        if mode == 'native-startup':
            _check(panel.path == destination and panel.baseline is None,
                   'An explicit native file unexpectedly imported original data')
            field(panel, 'User', 'test_owner')
            field(panel, 'User Name', 'Synthetic operator')
            field(panel, 'Password', 'Copper orchard 42 window violet')
            field(panel, 'Confirm password', 'Copper orchard 42 window violet')
            username = next(w for w in widgets(panel) if isinstance(w, ttk.Entry) and w.get() == 'test_owner')
            username.focus_force()
            username.event_generate('<Return>')
            app.update()
            _check(panel.actor is None, 'Username Enter submitted owner setup instead of moving through its fields')
            _check(app.focus_get() is panel._login_form.widgets['display_name'],
                   'Username Enter did not move to the full-name field')
            button(panel, 'Create administrator')
            _pump(app, lambda: panel.actor is not None and panel.future is None)
            _check(panel.actor.user.is_admin, 'Explicit owner setup did not create the administrator')
            dialog, _ = panel.dialog('Synthetic dialog')
            _check(str(dialog.transient()) == str(app), 'Modal belongs to another window')
            app.update()
            dialog.destroy()
        elif mode == 'native-workflow':
            import tools.smoke_native_ui as smoke
            def embedded_app():
                panel.geometry = app.geometry
                return panel
            with patch.object(smoke, 'NativeApp', embedded_app), redirect_stdout(io.StringIO()) as output:
                smoke.main()
            result = json.loads(output.getvalue())
            _check(result['native_ui'] == 'passed' and result['posted_documents'] == 6,
                   'Single-shell transaction workflow failed')
            _check(panel._closed and not app._closed, 'Component cleanup bypassed its root owner')
        elif mode == 'native-pointer':
            target = ttk.Label(panel, text='Synthetic double-click target')
            target.place(x=20, y=20, width=250, height=40)
            opened = []
            def open_dialog(event):
                opened.append(panel.dialog('Synthetic pointer dialog')[0])
                return 'break'
            target.bind('<Double-1>', open_dialog)
            app.update()
            pointer = _Pointer()
            try:
                pointer.move(target.winfo_rootx() + 25, target.winfo_rooty() + 20)
                for _ in range(2):
                    pointer.button(True)
                    for _ in range(5):
                        app.update()
                        time.sleep(.005)
                    pointer.button(False)
                    app.update()
                _check(not errors, repr(errors))
                _check(len(opened) == 1, 'Double-click did not open exactly one dialog')
                _pump(app, lambda: app.grab_current() is opened[0])
                _check(opened[0].winfo_viewable(), 'Modal acquired input before it was visible')
                opened[0].destroy()
                app.update()
                _check(app.grab_current() is None, 'Closed modal retained its input grab')
            finally:
                pointer.close()
        elif mode == 'shutdown':
            started = threading.Event()
            def held_combined_read():
                with session(path) as original, native_session(destination) as working:
                    _check(original.execute('SELECT COUNT(*) FROM PUR_ITEM').fetchone()[0] == 1,
                           'Original source was unavailable to the combined read')
                    _check(working.execute('SELECT COUNT(*) FROM products').fetchone()[0] == 0,
                           'Working source was unavailable to the combined read')
                    started.set()
                    _check(release.wait(15), 'Read release timed out')
                return 'finished'
            panel.run(held_combined_read)
            _pump(app, started.is_set)
            app.destroy()
            heartbeat = []
            app.after_idle(lambda: heartbeat.append(True))
            _pump(app, lambda: bool(heartbeat))
            _check(app.winfo_exists() and not app._closed, 'Root closed while its combined database read was active')
            release.set()
            _pump(app, lambda: app._closed)
            _check(panel._closed and all(not thread.is_alive() for thread in panel.worker._threads),
                   'Database worker outlived its Tk root')
        _check(not errors, repr(errors))
    finally:
        release.set()
        app.close()
        if not app._closed:
            app.wait_window(app)
        messages.stop()
        gc.collect()


@unittest.skipIf(os.name == 'posix' and not os.environ.get('DISPLAY'),
                 'Application integration checks require a graphical display')
class ApplicationTests(unittest.TestCase):
    def _run(self, mode):
        with tempfile.TemporaryDirectory(prefix='bifma-application-') as folder:
            path = Path(folder) / 'synthetic-history.sqlite3'
            if mode in {'historical-return', 'unified-modules', 'grouped-navigation', 'cash-layout'}:
                from tests.test_baseline_import import synthetic_archive
                synthetic_archive(path)
            else:
                (_item_archive if mode == 'item-form' else _operational_archive if mode == 'baseline-startup' else _archive)(path)
            before = hashlib.sha256(path.read_bytes()).digest()
            result = subprocess.run([sys.executable, '-B', '-m', 'tests.test_application', mode, str(path)],
                                    cwd=ROOT, capture_output=True, text=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(result.stderr, '')
            self.assertEqual(json.loads(result.stdout), {'case': mode, 'passed': True})
            self.assertEqual(hashlib.sha256(path.read_bytes()).digest(), before)

    def test_original_and_new_records_share_their_modules_and_authenticated_details(self):
        self._run('unified-modules')

    def test_coding_financial_inventory_and_report_sections_preserve_saved_documents(self):
        self._run('grouped-navigation')

    def test_cash_voucher_inline_rows_preserve_unsaved_work_and_print_through_posting(self):
        self._run('cash-layout')

    def test_item_form_automatically_numbers_products_and_keeps_party_accounts_separate(self):
        self._run('item-form')

    def test_explicit_native_database_has_one_shell_and_no_archive_dependency(self):
        self._run('native-startup')

    def test_default_startup_imports_balances_posts_and_reopens_without_a_picker(self):
        self._run('baseline-startup')

    def test_return_picker_uses_original_history_with_staff_entered_adjustments(self):
        self._run('historical-return')

    def test_history_smoke_does_not_create_or_import_a_working_database(self):
        self._run('history-smoke')

    def test_direct_destroy_waits_for_original_and_working_database_reads(self):
        self._run('shutdown')

    def test_failed_shell_initialization_releases_its_root_and_allows_retry(self):
        self._run('init-failure')

    def test_full_native_workflow_runs_inside_the_single_application(self):
        self._run('native-workflow')

    @unittest.skipUnless(sys.platform.startswith('linux') and ctypes.util.find_library('X11')
                         and ctypes.util.find_library('Xtst'), 'Real pointer regression requires X11/XTest')
    def test_native_dialog_double_click_in_the_shared_window(self):
        self._run('native-pointer')


if __name__ == '__main__':
    if len(sys.argv) == 3 and sys.argv[1] in {'cash-layout', 'grouped-navigation', 'item-form', 'unified-modules', 'native-startup', 'shutdown', 'init-failure', 'native-workflow', 'native-pointer', 'baseline-startup', 'historical-return', 'history-smoke'}:
        _case(sys.argv[1], Path(sys.argv[2]))
        print(json.dumps({'case': sys.argv[1], 'passed': True}))
    else:
        unittest.main()
