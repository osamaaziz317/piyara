"""Entry-first menus and real focused-widget F8 events on disposable data."""

from contextlib import closing
import gc
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import tkinter as tk
from tkinter import ttk
import unittest
from decimal import Decimal
from unittest.mock import patch

from tests.test_baseline_import import synthetic_archive
from tests.test_application import (_application_menu, _cash_form, _enter_cash_line,
                                    _enter_sale_line, _invoke_menu, _menu_commands,
                                    _menu_section, _sale_buyer, _sale_form)
from tests.test_preview_lifecycle import ROOT, _check, _pump
from tools.smoke_native_ui import button, field, widgets


PASSWORD = 'Copper orchard 42 window violet'
STAFF_PASSWORD = 'Cobalt bridges welcome sunrise 83'


class _EntryApp:
    def __init__(self, path):
        from bifma.application import Application
        from bifma.baseline import ensure_baseline
        from bifma.security import SecurityService
        self.path = ensure_baseline(path, path.parent / 'bifma.sqlite3').path
        self.security = SecurityService(self.path)
        self.owner = self.security.bootstrap_admin('owner', PASSWORD)
        self.app = Application(path)
        self.panel = self.app.transactions_workspace
        self.errors = []
        self.app.report_callback_exception = lambda kind, value, tb: self.errors.append((kind.__name__, str(value)))
        self.messages = patch('bifma.native_ui.messagebox.showerror',
                              side_effect=lambda title, message, **kwargs: self.errors.append((title, message)))
        self.messages.start()
        self.drain()

    def drain(self):
        self.app.update()
        _pump(self.app, lambda: self.panel.future is None and self.panel._startup_job is None)
        self.app.update()
        _pump(self.app, lambda: self.panel.future is None and self.panel._startup_job is None)
        _check(not self.errors, repr(self.errors))

    def login(self, username='owner', password=PASSWORD):
        field(self.panel, 'Username', username)
        field(self.panel, 'Passphrase', password)
        button(self.panel, 'Sign in')
        self.drain()
        _check(self.panel.actor is not None, 'Sign-in did not complete')

    def section(self, title):
        _menu_section(self.app, title)
        self.menu_section = title
        self.drain()

    def menu(self, title):
        _invoke_menu(self.app, self.menu_section, title)
        self.drain()

    def windows(self):
        return [widget for widget in self.panel.winfo_children() if isinstance(widget, tk.Toplevel)]

    def window(self, title):
        self.drain()
        found = [widget for widget in self.windows() if widget.title() == title]
        _check(len(found) == 1, 'Expected one dialog ' + title + ': ' + repr([w.title() for w in self.windows()]))
        return found[0]

    def titled(self, prefix):
        self.drain()
        found = [widget for widget in self.windows() if widget.title().startswith(prefix)]
        _check(len(found) == 1, 'Expected one dialog beginning ' + prefix)
        return found[0]

    def entry(self, parent, title):
        heading = next(widget for widget in widgets(parent)
                       if isinstance(widget, ttk.Label) and widget.cget('text') == title)
        return next(widget for widget in heading.master.winfo_children() if isinstance(widget, ttk.Entry))

    def f8(self, control):
        control.focus_force()
        self.app.update()
        _check(self.app.focus_get() is control, 'Keyboard event did not originate from the focused control')
        control.event_generate('<F8>')
        self.drain()

    def browse(self, entry, title, control=None):
        self.f8(control if control is not None else next(widget for widget in widgets(entry) if isinstance(widget, ttk.Entry)))
        return self.window('Saved records · ' + title)

    def search(self, browser, term):
        control = next(widget for widget in widgets(browser) if isinstance(widget, ttk.Entry))
        control.delete(0, 'end')
        control.insert(0, term)
        control.focus_force()
        self.app.update()
        control.event_generate('<Return>')
        self.drain()

    def tree(self, parent):
        trees = [widget for widget in widgets(parent) if isinstance(widget, ttk.Treeview)]
        _check(len(trees) == 1, 'Expected one record/line table')
        return trees[0]

    def back(self, browser, entry):
        button(browser, 'Back to entry')
        self.drain()
        _pump(self.app, lambda: self.app.grab_current() is entry)
        _check(entry.winfo_exists() and entry.winfo_viewable(), 'Browsing destroyed the unsaved entry')
        _check(not any(window.title().startswith('Saved records · ') for window in self.windows()),
               'Back to entry did not close the browser')

    def blank_landing(self):
        _check(not any(isinstance(widget, ttk.Treeview) for widget in widgets(self.panel.main)),
               'Opening an entry module automatically exposed saved records')

    def close(self):
        self.app.close()
        if not self.app._closed:
            self.app.wait_window(self.app)
        self.messages.stop()
        gc.collect()


def _master_case(path):
    from bifma.native_ui import label
    test = _EntryApp(path)
    panel = test.panel
    try:
        test.login()
        masters = panel.masters
        # More than a page of records demonstrates that F8 is an actual browser,
        # not a preloaded lookup that loses new-entry fields while searching.
        for number in range(101):
            masters.create(panel.actor, 'cities', {'code': f'C{number:03}', 'name': f'City {number:03}'})
        test.section('Coding')
        with patch.object(masters, 'list_records', wraps=masters.list_records) as reads:
            test.menu('Cities')
            entry = test.window('New Cities')
            test.blank_landing()
            _check(reads.call_count == 0, 'New City queried saved city rows before F8')
            field(entry, 'Code', 'UNSAVED')
            field(entry, 'Name', 'City being entered')
            browser = test.browse(entry, 'Cities', test.entry(entry, 'Name'))
            _check(len(test.tree(browser).get_children()) == 100, 'F8 did not display the first saved-record page')
            browser_search = next(widget for widget in widgets(browser) if isinstance(widget, ttk.Entry))
            test.f8(browser_search)
            _check(len(test.windows()) == 2 and test.window('Saved records · Cities') is browser,
                   'Repeated F8 created duplicate browsers')
            button(browser, 'Next')
            test.drain()
            _check(len(test.tree(browser).get_children()) == 2, 'F8 browser pagination lost records')
            button(browser, 'Previous')
            test.drain()
            test.search(browser, 'C050')
            tree = test.tree(browser)
            _check(len(tree.get_children()) == 1, 'F8 search did not filter saved cities')
            tree.selection_set(tree.get_children()[0])
            button(browser, 'Edit selected')
            edit = test.window('Edit Cities')
            field(edit, 'Name', 'Changed existing city')
            button(edit, 'Save changes')
            test.drain()
            _check(test.window('Saved records · Cities') is browser,
                   'Editing a saved record abandoned its browser')
            _check(any('Changed existing city' in tree_values(test.tree(browser), key)
                       for key in test.tree(browser).get_children()), 'Saved city edit did not refresh its browser')
            test.back(browser, entry)
            _check(test.entry(entry, 'Code').get() == 'UNSAVED'
                   and test.entry(entry, 'Name').get() == 'City being entered',
                   'F8 search/paging/editing changed the unsaved City form')
            browser = test.browse(entry, 'Cities', test.entry(entry, 'Name'))
            search = next(widget for widget in widgets(browser) if isinstance(widget, ttk.Entry))
            search.focus_force()
            test.app.update()
            search.event_generate('<Escape>')
            test.drain()
            _pump(test.app, lambda: test.app.grab_current() is entry)
            _check(not browser.winfo_exists() and test.entry(entry, 'Name').get() == 'City being entered',
                   'Escape did not return from F8 to the same unsaved entry')
            reads.reset_mock()
            button(entry, 'Save changes')
            fresh = test.window('New Cities')
            test.blank_landing()
            _check(fresh is not entry and test.entry(fresh, 'Code').get() == ''
                   and test.entry(fresh, 'Name').get() == '', 'Saving did not open a fresh City entry')
            _check(reads.call_count == 0, 'Saving a city automatically loaded its record list')
            fresh.destroy()
            test.drain()

        original = masters.list_records(panel.actor, 'products')[0]
        unit = next(row for row in masters.list_records(panel.actor, 'units') if row['id'] == original['small_unit_id'])
        with patch.object(masters, 'list_records', wraps=masters.list_records) as reads:
            test.menu('Items')
            entry = test.window('New Items')
            _check(not any(call.args[1] == 'products' for call in reads.call_args_list),
                   'Opening Item entry loaded saved items')
            field(entry, 'Item description', 'Unsaved next item')
            field(entry, 'Base unit', label(unit))
            _check(test.entry(entry, 'Item code').get() == '5001', 'Entry lost the next automatic item code')
            browser = test.browse(entry, 'Items', test.entry(entry, 'Item description'))
            tree = test.tree(browser)
            tree.selection_set(tree.get_children()[0])
            button(browser, 'Edit selected')
            edit = test.window('Edit Items')
            _check(test.entry(edit, 'Item code').get() == '5000', 'Edit selected did not open the original item')
            field(edit, 'Item description', 'Updated existing item')
            button(edit, 'Save changes')
            test.drain()
            test.back(browser, entry)
            _check(test.entry(entry, 'Item code').get() == '5001'
                   and test.entry(entry, 'Item description').get() == 'Unsaved next item',
                   'Editing an old item replaced the new item being entered')
            reads.reset_mock()
            button(entry, 'Save changes')
            fresh = test.window('New Items')
            _check(test.entry(fresh, 'Item code').get() == '5002'
                   and test.entry(fresh, 'Item description').get() == '',
                   'Saving the new item did not reset its description and advance its code')
            _check(not any(call.args[1] == 'products' for call in reads.call_args_list),
                   'Saving an item automatically opened its list')
            test.blank_landing()
            fresh.destroy()
            test.drain()
        _check(masters.list_records(panel.actor, 'cities', search='UNSAVED')[0]['name'] == 'City being entered',
               'Preserved city values did not reach the saved record')
        edited = masters.list_records(panel.actor, 'products', search='5000')[0]
        for key in ('code', 'sale_account_id', 'purchase_account_id', 'inventory_account_id'):
            _check(edited[key] == original[key], 'F8 editing modified an original item code/account mapping')

        test.section('Setup')
        test.menu('Companies')
        entry = test.window('New Companies')
        field(entry, 'Code', 'NEXT-COMPANY')
        field(entry, 'Name', 'Unsaved company in progress')
        browser = test.browse(entry, 'Companies', test.entry(entry, 'Name'))
        main, chooser, company = panel.main, panel._company_choice, panel.company_id
        for deactivate in (False, True):
            tree = test.tree(browser)
            tree.selection_set(tree.get_children()[0])
            button(browser, 'Edit selected')
            edit = test.window('Edit Companies')
            if deactivate:
                heading = next(widget for widget in widgets(edit)
                               if isinstance(widget, ttk.Label) and widget.cget('text') == 'Active')
                active = next(widget for widget in heading.master.winfo_children()
                              if isinstance(widget, ttk.Checkbutton))
                _check(active.instate(['selected']), 'The current company was unexpectedly inactive')
                active.invoke()
            else:
                field(edit, 'Name', 'Renamed current company')
            button(edit, 'Save changes')
            test.drain()
            _check(panel.main is main and panel._company_choice is chooser and entry.winfo_exists()
                   and test.window('Saved records · Companies') is browser,
                   'Updating company metadata rebuilt the shell and discarded the open forms')
            _check(test.entry(entry, 'Code').get() == 'NEXT-COMPANY'
                   and test.entry(entry, 'Name').get() == 'Unsaved company in progress',
                   'Updating an existing company erased the unsaved new company')
            if deactivate:
                _check(panel.company_id is None and panel.companies == [] and panel.permissions == {},
                       'Deactivating the only company retained a stale active company or permissions')
                _check(not chooser.get() and not chooser.cget('values'),
                       'Company selector still offers the deactivated company')
                _check(panel.security.accessible_companies(panel.actor) == []
                       and test.security.accessible_companies(test.owner) == [],
                       'The refreshed company selection disagrees with fresh authorized company access')
            else:
                _check(panel.company_id == company and panel.companies == [(company, 'Renamed current company')]
                       and chooser.get() == 'Renamed current company',
                       'Editing a company left its old name in the active company selector')
                _check(panel.permissions == panel.security.permissions(panel.actor, company),
                       'Editing a company left stale company permissions')
        test.back(browser, entry)
        test.blank_landing()
    finally:
        test.close()


def tree_values(tree, key):
    return tuple(str(value) for value in tree.item(key, 'values'))


def _document_case(path):
    from bifma.native_ui import label
    test = _EntryApp(path)
    panel = test.panel
    try:
        test.login()
        party = next(row for row in panel.masters.list_records(panel.actor, 'accounts') if row['code'] == '0002')
        test.section('Financial Transactions')
        with patch.object(panel.register, 'list_documents', wraps=panel.register.list_documents) as reads:
            test.menu('Cash Receipt Voucher')
            entry = test.window('New · Cash Receipt Voucher')
            test.blank_landing()
            _check(reads.call_count == 0, 'Opening cash receipt queried its saved register before F8')
            field(entry, 'Notes', 'Receipt still being entered')
            row = _enter_cash_line(entry, party['code'], '3', 'Line still being entered')
            row.find_button.invoke()
            test.drain()
            lookup = row.inputs['account_id']._search_window
            _check(lookup is not None and lookup.winfo_viewable(), 'Inline account Find did not open')
            # A nested account lookup must retain keyboard ownership; F8 on it
            # must not unexpectedly surface a saved-document browser.
            test.f8(test.entry(lookup, 'Code or name'))
            _check(not any(window.title().startswith('Saved records · ') for window in test.windows()),
                   'F8 in an account lookup switched away from that lookup')
            lookup.destroy()
            test.drain()
            before_values = _cash_form(entry).payload()
            # Inline rows are part of the voucher form, so F8 on the narration
            # preserves its current text and opens the same module's records.
            browser = test.browse(entry, 'Cash Receipt Voucher', row.widgets['narration'])
            _check([(row['source'], row['document_number']) for row in panel.document_rows] == [('original', '26')],
                   'F8 omitted the original receipt')
            panel.document_tree.selection_set('0')
            button(browser, 'Open selected')
            test.drain()
            detail = next(window for window in test.windows() if window not in (entry, browser))
            controls = {widget.cget('text') for widget in widgets(detail) if isinstance(widget, ttk.Button)}
            _check(controls.isdisjoint({'Edit draft', 'Post document'}), 'F8 made an original voucher editable')
            button(detail, 'Close')
            test.drain()
            _check(test.window('Saved records · Cash Receipt Voucher') is browser,
                   'Closing original details abandoned the saved records browser')
            test.back(browser, entry)
            _check(_cash_form(entry).payload() == before_values,
                   'F8 browsing discarded the voucher header or unsaved lines')
            reads.reset_mock()
            button(entry, 'Save')
            review = test.titled('Review draft · ')
            button(review, 'Keep as draft')
            fresh = test.window('New · Cash Receipt Voucher')
            _check(reads.call_count == 0 and not _cash_form(fresh).payload()['lines']
                   and test.entry(fresh, 'Notes').get() == '',
                   'Keeping a draft automatically loaded old data instead of a fresh entry')
            field(fresh, 'Notes', 'Second unsaved receipt')
            browser = test.browse(fresh, 'Cash Receipt Voucher', test.entry(fresh, 'Notes'))
            _check({row['source'] for row in panel.document_rows} == {'original', 'current'},
                   'F8 separated original receipts from new drafts')
            current_index = next(index for index, row in enumerate(panel.document_rows) if row['source'] == 'current')
            panel.document_tree.selection_set(str(current_index))
            button(browser, 'Open selected')
            review = test.titled('Review draft · ')
            button(review, 'Edit draft')
            edit = test.window('Edit draft · Cash Receipt Voucher')
            field(edit, 'Notes', 'Reviewed existing draft')
            button(edit, 'Save')
            review = test.titled('Review draft · ')
            button(review, 'Post document')
            posted = test.titled('Posted · ')
            button(posted, 'Close')
            test.drain()
            _check(test.window('Saved records · Cash Receipt Voucher') is browser,
                   'Posting a selected existing draft replaced its browser')
            _check(any(row['source'] == 'current' and row['status'] == 'posted' for row in panel.document_rows),
                   'Posted saved draft did not refresh inside its F8 browser')
            test.back(browser, fresh)
            _check(test.entry(fresh, 'Notes').get() == 'Second unsaved receipt',
                   'Posting an existing draft discarded the separate entry in progress')
            _enter_cash_line(fresh, party['code'], '2')
            reads.reset_mock()
            button(fresh, 'Save')
            button(test.titled('Review draft · '), 'Post document')
            button(test.titled('Posted · '), 'Close')
            newest = test.window('New · Cash Receipt Voucher')
            _check(reads.call_count == 0 and not _cash_form(newest).payload()['lines'],
                   'Posting from new entry automatically opened saved records')
            test.blank_landing()
            newest.destroy()
            test.drain()
            # F8 still works on the blank module page after closing its form.
            test.f8(panel.main)
            browser = test.window('Saved records · Cash Receipt Voucher')
            _check(len(panel.document_rows) == 3, 'F8 on the module page lost saved receipts')
            button(browser, 'Back to entry')
            test.drain()
            test.blank_landing()
    finally:
        test.close()


def _permissions_case(path):
    test = _EntryApp(path)
    panel = test.panel
    try:
        company = test.security.accessible_companies(test.owner)[0][0]
        user = test.security.create_user(test.owner, 'reader', STAFF_PASSWORD)
        test.security.set_company_access(test.owner, user.id, [company])
        test.security.set_permissions(test.owner, user.id, 'sales', ['read'])
        test.login('reader', STAFF_PASSWORD)
        visible = {title for _, _, title in _menu_commands(_application_menu(test.app))}
        _check('Sale Voucher' in visible and visible.isdisjoint({'Accounts', 'Items', 'Companies', 'Users & access'}),
               'Top menus ignored the read-only user\'s granted modules')
        test.section('Inventory')
        with patch.object(panel.register, 'list_documents', wraps=panel.register.list_documents) as reads:
            test.menu('Sale Voucher')
            _check(not test.windows() and reads.call_count == 0,
                   'Read-only module opened an unauthorized new form or automatically displayed records')
            test.blank_landing()
            test.f8(panel.main)
            browser = test.window('Saved records · Sale Voucher')
            _check(len(panel.document_rows) == 1 and panel.document_rows[0]['source'] == 'original',
                   'Read-only F8 cannot access the authorized original sale')
            _check(not any(isinstance(widget, ttk.Button) and widget.cget('text') in {'New', 'New document'}
                           for widget in widgets(browser)), 'Read-only F8 browser offers document creation')
            test.security.set_permissions(test.owner, user.id, 'sales', [])
            panel.document_tree.selection_set('0')
            with patch('bifma.native_ui.messagebox.showerror') as denied:
                button(browser, 'Open selected')
                test.drain()
                _check(denied.call_count == 1, 'Opening original details ignored a freshly revoked read grant')
            _check(test.windows() == [browser], 'Revoked original details were displayed')
            button(browser, 'Back to entry')
            test.drain()
            with patch('bifma.native_ui.messagebox.showerror') as denied:
                test.f8(panel.main)
                _check(denied.call_count == 1, 'F8 ignored the module read revocation')
            denied_browser = test.window('Saved records · Sale Voucher')
            _check(not panel.document_rows
                   and not any(isinstance(widget, ttk.Treeview) for widget in widgets(denied_browser)),
                   'F8 published saved records after access was revoked')
            button(denied_browser, 'Back to entry')
            test.drain()
            test.security.set_permissions(test.owner, user.id, 'sales', ['read'])
            test.f8(panel.main)
            browser = test.window('Saved records · Sale Voucher')
            panel.logout()
            test.drain()
            _check(panel.actor is None and not test.windows(), 'Logout left a saved-record browser visible')
            _check(not str(test.app.cget('menu')), 'Logout retained authenticated top menu commands')
            reads.reset_mock()
            test.f8(test.entry(panel, 'User'))
            _check(not test.windows() and reads.call_count == 0, 'F8 retained a module action on the login page')
            # A subsequent login starts clean rather than retaining another
            # actor's last module target or modal keyboard binding.
            test.login()
            reads.reset_mock()
            test.f8(panel.main)
            _check(not test.windows() and reads.call_count == 0, 'F8 leaked the previous session module into Overview')
    finally:
        test.close()


def _sale_keyboard_case(path):
    test = _EntryApp(path)
    panel = test.panel

    def press(control):
        control.focus_force()
        test.app.update()
        _check(test.app.focus_get() is control, 'Enter was not sent from its actual focused field')
        control.event_generate('<Return>')
        test.drain()

    def query(title):
        test.drain()
        found = [widget for widget in widgets(panel)
                 if isinstance(widget, tk.Toplevel) and widget.title() == 'Find · ' + title]
        _check(len(found) == 1, 'Enter did not open the expected Find: ' + title)
        return found[0], test.entry(found[0], 'Code or name')

    def type_name(control, text):
        _check(test.app.focus_get() is control, 'Find did not transfer keyboard focus to its search input')
        for character in text:
            control.event_generate('<KeyPress>', keysym=character)
        test.drain()
        _check(control.get() == text, 'Typed Find letters did not remain in the editable query')

    def date_field(browser, title):
        label = next(widget for widget in widgets(browser)
                     if isinstance(widget, ttk.Label) and widget.cget('text') == title)
        siblings = label.master.winfo_children()
        return next(widget for widget in siblings[siblings.index(label) + 1:]
                    if isinstance(widget, ttk.Entry))

    def filter_dates(browser, lower, upper):
        for title, value in (('Date From', lower), ('Date To', upper)):
            control = date_field(browser, title)
            control.delete(0, 'end')
            control.insert(0, value)
        press(date_field(browser, 'Date To'))

    try:
        # The login helper fills controls programmatically. Activate this
        # window as a real operator would before checking its initial focus.
        test.app.focus_force()
        test.app.update()
        test.login()
        choices = panel.masters.lookup(panel.actor, panel.company_id, 'sales', action='create')
        party = next(row for row in choices['accounts'] if row['code'] == '0002')
        product, store = choices['products'][0], choices['stores'][0]
        previous = panel.transactions.save_draft(panel.actor, panel.company_id, 'sale', {
            'document_number': '260002', 'business_date': '2026-02-02', 'account_id': party['id'],
            'rounding_policy': 'legacy_whole', 'posting_places': 0,
            'lines': [{'product_id': product['id'], 'store_id': store['id'], 'quantity': '1', 'rate': '10'}],
        })
        test.section('Inventory')
        with patch.object(panel.register, 'list_documents', wraps=panel.register.list_documents) as reads:
            test.menu('Sale Voucher')
            editor = test.window('New · Sale Voucher')
            form = _sale_form(editor)
            test.blank_landing()
            _check(reads.call_count == 0, 'Opening Sale Voucher loaded saved document rows')
            _check(form.widgets['document_number'].get() == '260003',
                   'Sale did not suggest the next saved document number')
            _pump(test.app, lambda: test.app.grab_current() is editor
                  and test.app.focus_get() is form.widgets['document_number'])
            press(form.widgets['document_number'])
            _check(test.app.focus_get() is form.widgets['business_date'], 'Sale # Enter did not focus Date')
            press(form.widgets['business_date'])
            buyer_popup, search = query('Buyer')
            type_name(search, 'party')
            press(search)
            item_popup, search = query('Item')
            _check(not buyer_popup.winfo_exists() and form.inputs['account_id'].get() == party['id'],
                   'Buyer Enter did not retain the selected account before opening Item Find')
            type_name(search, 'synthetic')
            press(search)
            first = form.rows[0]
            _check(not item_popup.winfo_exists() and test.app.focus_get() is first.widgets['unit_id'],
                   'Item selection did not advance to Unit')
            _check(first.inputs['unit_id'].get() == product['small_unit_id']
                   and first.inputs['entered_quantity'].get() == '1'
                   and first.inputs['rate'].get() == '10',
                   'Item selection lost its base unit, default quantity one or saved rate')
            press(first.widgets['unit_id'])
            _check(test.app.focus_get() is first.widgets['entered_quantity'], 'Unit Enter did not advance to Unit Qty')
            press(first.widgets['entered_quantity'])
            _check(test.app.focus_get() is first.widgets['rate'], 'Unit Qty Enter did not advance to Rate')
            press(first.widgets['rate'])
            _check(test.app.focus_get() is first.widgets['discount_percent'], 'Rate Enter did not advance to discount')
            first.widgets['discount_percent'].delete(0, 'end')
            first.widgets['discount_percent'].insert(0, '10')
            press(first.widgets['discount_percent'])
            item_popup, search = query('Item')
            type_name(search, 'synthetic')
            press(search)
            second = form.rows[1]
            _check(second.inputs['product_id'].get() == product['id'] and form.total_value == Decimal('19'),
                   'Discount Enter failed to start the next item or changed the invoice total')
            form.widgets['bill_number'].insert(0, '000012')
            form.widgets['notes'].insert(0, 'Keyboard entry retained through F8')
            original_payload = form.payload()
            browser = test.browse(editor, 'Sale Voucher', second.widgets['rate'])
            _check(date_field(browser, 'Date From').get() == '2026-01-02'
                   and date_field(browser, 'Date To').get() == '2026-02-02',
                   'First F8 did not fill the date range from original and new saved sales')
            _check({row['source'] for row in panel.document_rows} == {'original', 'current'},
                   'Default F8 dates excluded either original or native sales')
            filter_dates(browser, '2026-01-02', '2026-01-02')
            _check(len(panel.document_rows) == 1 and panel.document_rows[0]['source'] == 'original',
                   'Sale Date From/To did not filter the original invoice')
            filter_dates(browser, '2026-02-02', '2026-02-02')
            _check([row['id'] for row in panel.document_rows] == [previous['id']],
                   'Sale Date From/To did not filter the native draft')
            button(browser, 'Clear filters')
            test.drain()
            _check(date_field(browser, 'Date From').get() == '' and date_field(browser, 'Date To').get() == ''
                   and len(panel.document_rows) == 2, 'Clearing sale date filters did not show all saved sales')
            test.back(browser, editor)
            _check(form.payload() == original_payload, 'F8 date filtering lost unfinished sale fields or rows')
            button(editor, 'Save')
            review = test.titled('Review draft · 260003')
            button(review, 'Post document')
            posted = test.titled('Posted · 260003')
            destination = path.parent / 'keyboard-sale.html'
            with patch('bifma.native_ui.filedialog.asksaveasfilename', return_value=str(destination)), \
                 patch('bifma.native_ui.messagebox.showinfo'):
                button(posted, 'Save printable copy')
                test.drain()
            html = destination.read_text(encoding='utf-8')
            _check('Bill number' in html and '000012' in html and 'Keyboard entry retained through F8' in html,
                   'Posted sale print lost its exact Bill # or remarks')
            saved = panel.transactions.get(panel.actor, panel.company_id,
                next(row['id'] for row in panel.register.list_documents(panel.actor, panel.company_id, 'sale')
                     if row['source'] == 'current' and row['document_number'] == '260003'))
            _check(saved['snapshot']['bill_number'] == '000012' and saved['total_amount'] == '19',
                   'Posted sale snapshot lost the exact bill reference or amount')
            stock = panel.transactions.stock_balance(panel.actor, panel.company_id,
                                                     product_id=product['id'], store_id=store['id'])
            _check(stock[0]['quantity'] == '5', 'Inline sale did not reduce baseline stock 7 by its two lines')
            _check(panel.reports.ledger(panel.actor, panel.company_id, party['id'])['closing'] == Decimal('39'),
                   'Inline sale did not add 19 to the original buyer balance 20')
            button(posted, 'Close')
            fresh = test.window('New · Sale Voucher')
            fresh_form = _sale_form(fresh)
            _check(all(row.is_blank() for row in fresh_form.rows)
                   and fresh_form.inputs['document_number'].get() == '260004',
                   'Posting did not return to a blank next Sale Voucher')
            browser = test.browse(fresh, 'Sale Voucher', fresh_form.widgets['document_number'])
            _check(len(panel.document_rows) == 3 and date_field(browser, 'Date From').get() == ''
                   and date_field(browser, 'Date To').get() == '',
                   'F8 reset cleared filters or omitted the newly posted sale')
            test.back(browser, fresh)
            button(fresh, 'Exit')
            test.drain()
    finally:
        test.close()


def _routes_case(path):
    from bifma.native_ui import DOCUMENT_SECTIONS, KINDS, MASTER_LABELS, MASTER_SECTIONS
    test = _EntryApp(path)
    panel = test.panel
    try:
        test.login()
        for section, entities in MASTER_SECTIONS.items():
            test.section(section)
            for entity in entities:
                title = MASTER_LABELS[entity]
                test.menu(title)
                entry = test.window('New ' + title)
                test.blank_landing()
                browser = test.browse(entry, title)
                test.back(browser, entry)
                entry.destroy()
                test.drain()
        for section, kinds in DOCUMENT_SECTIONS.items():
            test.section(section)
            for kind in kinds:
                title = KINDS[kind][0]
                with patch.object(panel.register, 'list_documents', wraps=panel.register.list_documents) as reads:
                    test.menu(title)
                    entry = test.window('New · ' + title)
                    test.blank_landing()
                    _check(reads.call_count == 0, 'Document module fetched its saved list on entry: ' + kind)
                    browser = test.browse(entry, title)
                    _check(reads.call_count == 1, 'F8 did not fetch exactly one initial page for ' + kind)
                    test.back(browser, entry)
                    entry.destroy()
                    test.drain()
        test.section('Reports')
        test.menu('Stock quantities')
        test.f8(panel.main)
        _check(not test.windows(), 'F8 on Reports reopened the previous business-entry module')
    finally:
        test.close()


def _sale_date_refresh_case(path):
    """Automatic F8 dates grow with saved sales; deliberate filters stay put."""
    test = _EntryApp(path)
    panel = test.panel

    def date_control(browser, title):
        heading = next(widget for widget in widgets(browser)
                       if isinstance(widget, ttk.Label) and widget.cget('text') == title)
        siblings = heading.master.winfo_children()
        return next(widget for widget in siblings[siblings.index(heading) + 1:]
                    if isinstance(widget, ttk.Entry))

    def expect_dates(browser, lower, upper, numbers):
        _check(date_control(browser, 'Date From').get() == lower
               and date_control(browser, 'Date To').get() == upper,
               'F8 dates do not match the requested automatic/manual behavior')
        _check({row['document_number'] for row in panel.document_rows} == set(numbers),
               'F8 dates exposed the wrong saved sales')

    try:
        test.login()
        choices = panel.masters.lookup(panel.actor, panel.company_id, 'sales', action='create')
        party = next(row for row in choices['accounts'] if row['code'] == '0002')
        product, store = choices['products'][0], choices['stores'][0]
        test.section('Inventory')
        test.menu('Sale Voucher')
        editor = test.window('New · Sale Voucher')
        form = _sale_form(editor)
        browser = test.browse(editor, 'Sale Voucher', form.widgets['document_number'])
        expect_dates(browser, '2026-01-02', '2026-01-02', ['260001'])
        test.back(browser, editor)
        _sale_buyer(editor, party['code'])
        _enter_sale_line(editor, product['code'], '1', '10', store=store['id'])
        form.widgets['business_date'].delete(0, 'end')
        form.widgets['business_date'].insert(0, '2026-02-02')
        button(editor, 'Save')
        button(test.titled('Review draft · 260002'), 'Keep as draft')
        editor = test.window('New · Sale Voucher')
        form = _sale_form(editor)
        form.widgets['notes'].insert(0, 'Keep this unfinished entry')
        unfinished = form.raw_values()
        browser = test.browse(editor, 'Sale Voucher', form.widgets['notes'])
        expect_dates(browser, '2026-01-02', '2026-02-02', ['260001', '260002'])
        for title in ('Date From', 'Date To'):
            control = date_control(browser, title)
            control.delete(0, 'end')
            control.insert(0, '2026-01-02')
        button(browser, 'Search')
        test.drain()
        expect_dates(browser, '2026-01-02', '2026-01-02', ['260001'])
        test.back(browser, editor)

        def save_later(number, business_date):
            panel.transactions.save_draft(panel.actor, panel.company_id, 'sale', {
                'document_number': number, 'business_date': business_date, 'account_id': party['id'],
                'rounding_policy': 'legacy_whole', 'posting_places': 0,
                'lines': [{'product_id': product['id'], 'store_id': store['id'], 'quantity': '1', 'rate': '10'}],
            })

        save_later('260003', '2026-03-03')
        browser = test.browse(editor, 'Sale Voucher', form.widgets['notes'])
        expect_dates(browser, '2026-01-02', '2026-01-02', ['260001'])
        button(browser, 'Clear filters')
        test.drain()
        expect_dates(browser, '', '', ['260001', '260002', '260003'])
        test.back(browser, editor)
        save_later('260004', '2026-04-04')
        browser = test.browse(editor, 'Sale Voucher', form.widgets['notes'])
        expect_dates(browser, '', '', ['260001', '260002', '260003', '260004'])
        test.back(browser, editor)
        _check(form.raw_values() == unfinished,
               'Refreshing automatic or deliberate F8 dates changed unfinished entry fields')
    finally:
        test.close()


CASES = {'masters': _master_case, 'documents': _document_case,
         'permissions': _permissions_case, 'routes': _routes_case, 'sale-keyboard': _sale_keyboard_case,
         'sale-dates': _sale_date_refresh_case}


@unittest.skipIf(os.name == 'posix' and not os.environ.get('DISPLAY'),
                 'Entry navigation checks require a graphical display')
class EntryNavigationTests(unittest.TestCase):
    def _run(self, mode):
        with tempfile.TemporaryDirectory(prefix='bifma-entry-navigation-') as folder:
            path = Path(folder) / 'synthetic-history.sqlite3'
            synthetic_archive(path)
            with closing(sqlite3.connect(path)) as connection, connection:
                for table in ('PUR_ITEM', 'OPEN_STOCK', 'SDTL'):
                    connection.execute(f"UPDATE {table} SET ICODE='5000'")
                if mode == 'sale-keyboard':
                    connection.execute("UPDATE PUR_ITEM SET SALE_RATE='10'")
            before = hashlib.sha256(path.read_bytes()).digest()
            result = subprocess.run([sys.executable, '-B', '-m', 'tests.test_entry_navigation', mode, str(path)],
                                    cwd=ROOT, capture_output=True, text=True, timeout=90)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(result.stderr, '')
            self.assertEqual(json.loads(result.stdout), {'case': mode, 'passed': True})
            self.assertEqual(hashlib.sha256(path.read_bytes()).digest(), before)

    def test_f8_master_paging_search_and_edit_preserve_unsaved_city_and_item_entries(self):
        self._run('masters')

    def test_f8_cash_receipts_preserve_lines_and_return_to_fresh_entry_after_saving(self):
        self._run('documents')

    def test_f8_respects_read_only_access_fresh_revocation_and_logout(self):
        self._run('permissions')

    def test_sale_enter_workflow_f8_date_filters_and_bill_reference_post_and_print(self):
        self._run('sale-keyboard')

    def test_sale_f8_refreshes_automatic_dates_after_saving_but_preserves_manual_or_cleared_filters(self):
        self._run('sale-dates')

    def test_all_entry_menu_routes_open_new_forms_and_only_f8_loads_saved_records(self):
        self._run('routes')


if __name__ == '__main__':
    if len(sys.argv) == 3 and sys.argv[1] in CASES:
        CASES[sys.argv[1]](Path(sys.argv[2]))
        print(json.dumps({'case': sys.argv[1], 'passed': True}))
    else:
        unittest.main()
