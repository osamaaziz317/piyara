"""Main/sub item-group screens share records and enforce their saved hierarchy."""

import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from uuid import uuid4

from bifma.masters import MasterService, MasterValidationError, MasterConflictError
from bifma.native_schema import initialize_database, native_session
from bifma.security import SecurityService, AuthorizationError
from tests.test_masters import Clock, PASSWORD, STAFF_PASSWORD


class ItemGroupServiceTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(prefix='bifma-item-groups-')
        self.addCleanup(folder.cleanup)
        self.path = Path(folder.name) / 'native.sqlite3'
        initialize_database(self.path)
        self.clock = Clock()
        self.security = SecurityService(self.path, clock=self.clock)
        self.owner = self.security.bootstrap_admin('owner', PASSWORD)
        self.masters = MasterService(self.path, security=self.security, clock=self.clock)
        self.main = self.create('ROOT', 'main')
        self.sub = self.create('CHILD', 'sub', parent_id=self.main['id'])

    def create(self, code, section=None, **values):
        return self.masters.create(self.owner, 'product_groups',
                                   {'code': code, 'name': code, **values}, group_section=section)

    def counts(self):
        with native_session(self.path) as connection:
            return tuple(connection.execute('SELECT (SELECT count(*) FROM product_groups),'
                                            '(SELECT count(*) FROM audit_events)').fetchone())

    def test_main_sub_filters_are_applied_before_pagination_and_literal_search(self):
        # Interleave the two classes in ordinary code ordering; page after the
        # first 100 must still be a page of the selected class, not a sliced mix.
        for index in range(105):
            self.create(f'G{index:03d}-A', 'main')
            self.create(f'G{index:03d}-B', 'sub', parent_id=self.main['id'])
        for section, suffix in (('main', 'A'), ('sub', 'B')):
            rows = self.masters.list_records(self.owner, 'product_groups', group_section=section,
                                             search='G', offset=100, limit=100)
            self.assertEqual([row['code'] for row in rows], [f'G{i:03d}-{suffix}' for i in range(100, 105)])
        literal = self.create('100%_MAIN', 'main')
        self.create('100XXMAIN', 'main')
        rows = self.masters.list_records(self.owner, 'product_groups', group_section='main', search='%_')
        self.assertEqual([row['id'] for row in rows], [literal['id']])
        self.assertEqual(len(self.masters.list_records(self.owner, 'product_groups', limit=500)), 214)

    def test_invalid_hierarchy_and_wrong_section_fail_without_writes(self):
        before = self.counts()
        for section, parent in (('main', self.main['id']), ('sub', None),
                                ('sub', self.sub['id']), ('sub', str(uuid4()))):
            with self.subTest(section=section, parent=parent), self.assertRaises(MasterValidationError):
                self.create('INVALID', section, parent_id=parent)
            self.assertEqual(self.counts(), before)
        for record, section, values in (
            (self.main, 'main', {'parent_id': self.sub['id']}),
            (self.main, 'sub', {'parent_id': self.sub['id']}),
            (self.sub, 'main', {'parent_id': None}),
            (self.sub, 'sub', {'parent_id': None}),
        ):
            with self.assertRaises(MasterValidationError):
                self.masters.update(self.owner, 'product_groups', record['id'], values,
                                    record['version'], group_section=section)
            self.assertEqual(self.counts(), before)
        self.assertEqual(self.masters.get(self.owner, 'product_groups', self.main['id']), self.main)
        self.assertEqual(self.masters.get(self.owner, 'product_groups', self.sub['id']), self.sub)

    def test_parent_is_rechecked_at_save_and_generic_existing_trees_are_not_rewritten(self):
        other = self.create('ANOTHER', 'main')
        # A generic administrator can retain an existing deeper tree. A form
        # opened before this change must not silently accept the stale parent.
        moved = self.masters.update(self.owner, 'product_groups', self.main['id'],
                                    {'parent_id': other['id']}, self.main['version'])
        before = self.counts()
        with self.assertRaisesRegex(MasterValidationError, 'main item group'):
            self.create('STALE-PARENT', 'sub', parent_id=self.main['id'])
        with self.assertRaisesRegex(MasterValidationError, 'main item group'):
            self.masters.update(self.owner, 'product_groups', self.sub['id'], {'name': 'Changed'},
                                self.sub['version'], group_section='sub')
        with self.assertRaises(MasterConflictError):
            self.masters.update(self.owner, 'product_groups', self.main['id'], {'name': 'Changed'},
                                self.main['version'], group_section='main')
        self.assertEqual(self.counts(), before)
        self.assertEqual(self.masters.get(self.owner, 'product_groups', self.main['id']), moved)
        self.assertEqual(self.masters.get(self.owner, 'product_groups', self.sub['id']), self.sub)
        # Ordinary generic reads still expose every record, including the tree.
        self.assertEqual({row['id'] for row in self.masters.list_records(self.owner, 'product_groups')},
                         {self.main['id'], self.sub['id'], other['id']})

    def test_valid_edits_preserve_identity_references_and_stale_version_checks(self):
        unit = self.masters.create(self.owner, 'units', {'code': 'PCS', 'name': 'PCS'})
        product = self.masters.create(self.owner, 'products', {'code': '1', 'name': 'One',
                                      'small_unit_id': unit['id'], 'group_id': self.sub['id']})
        changed = self.masters.update(self.owner, 'product_groups', self.sub['id'], {'name': 'Renamed'},
                                      self.sub['version'], group_section='sub')
        self.assertEqual(changed['id'], self.sub['id'])
        self.assertEqual(changed['parent_id'], self.main['id'])
        self.assertEqual(self.masters.get(self.owner, 'products', product['id'])['group_id'], self.sub['id'])
        with self.assertRaises(MasterConflictError):
            self.masters.update(self.owner, 'product_groups', self.sub['id'], {'name': 'Stale'},
                                self.sub['version'], group_section='sub')

    def test_section_apis_keep_admin_authorization_and_validate_section(self):
        self.security.create_user(self.owner, 'staff', STAFF_PASSWORD)
        staff = self.security.authenticate('staff', STAFF_PASSWORD)
        before = self.counts()
        for operation in (
            lambda: self.masters.list_records(staff, 'product_groups', group_section='main'),
            lambda: self.masters.create(staff, 'product_groups', {'code': 'X', 'name': 'X'}, group_section='main'),
            lambda: self.masters.update(staff, 'product_groups', self.sub['id'], {'name': 'No'},
                                        self.sub['version'], group_section='sub'),
        ):
            with self.assertRaises(AuthorizationError):
                operation()
        for value in ('invalid', True, []):
            with self.assertRaises(MasterValidationError):
                self.masters.list_records(self.owner, 'product_groups', group_section=value)
        with self.assertRaises(MasterValidationError):
            self.masters.create(self.owner, 'cities', {'code': 'X', 'name': 'X'}, group_section='main')
        self.assertEqual(self.counts(), before)


def _gui_case(path):
    from tkinter import ttk
    from bifma.native_ui import label
    from tests.test_entry_navigation import _EntryApp
    from tests.test_application import _menu_commands, _menu_section
    from tools.smoke_native_ui import button, field, widgets

    test = _EntryApp(path)
    panel = test.panel
    try:
        test.login()
        existing_product = panel.masters.list_records(panel.actor, 'products')[0]
        existing_groups = panel.masters.list_records(panel.actor, 'product_groups')
        original_main = next(row for row in existing_groups if row['parent_id'] is None)
        original_sub = next(row for row in existing_groups if row['parent_id'] is not None)
        test.section('Coding')
        titles = [title for _, _, title in _menu_commands(_menu_section(test.app, 'Coding'))]
        assert 'Main Item Groups' in titles and 'Item Sub Groups' in titles and 'Item groups' not in titles
        test.menu('Main Item Groups')
        entry = test.window('New Main Item Groups')
        test.blank_landing()
        assert not any(isinstance(w, ttk.Label) and w.cget('text') == 'Parent group' for w in widgets(entry))
        field(entry, 'Code', 'MAIN-NEW')
        field(entry, 'Name', 'New main')
        browser = test.browse(entry, 'Main Item Groups')
        rows = test.tree(browser)
        assert len(rows.get_children()) == 1
        assert rows.item(rows.get_children()[0], 'values')[0] == original_main['code']
        test.back(browser, entry)
        assert test.entry(entry, 'Code').get() == 'MAIN-NEW'
        button(entry, 'Save changes')
        test.drain()
        fresh = test.window('New Main Item Groups')
        assert test.entry(fresh, 'Code').get() == ''
        fresh.destroy()
        test.drain()
        main = next(row for row in panel.masters.list_records(panel.actor, 'product_groups', group_section='main')
                    if row['code'] == 'MAIN-NEW')
        test.menu('Item Sub Groups')
        entry = test.window('New Item Sub Groups')
        field(entry, 'Code', 'SUB-NEW')
        field(entry, 'Name', 'New sub')
        parent = next(widget for widget in widgets(entry) if isinstance(widget, ttk.Combobox))
        assert label(main) in parent['values'] and label(original_main) in parent['values']
        assert label(original_sub) not in parent['values']
        field(entry, 'Main item group', label(main))
        browser = test.browse(entry, 'Item Sub Groups')
        rows = test.tree(browser)
        assert len(rows.get_children()) == 1
        assert rows.item(rows.get_children()[0], 'values')[0] == original_sub['code']
        test.back(browser, entry)
        assert test.entry(entry, 'Code').get() == 'SUB-NEW' and parent.get() == label(main)
        button(entry, 'Save changes')
        test.drain()
        fresh = test.window('New Item Sub Groups')
        assert test.entry(fresh, 'Code').get() == ''
        browser = test.browse(fresh, 'Item Sub Groups')
        test.search(browser, 'SUB-NEW')
        rows = test.tree(browser)
        assert len(rows.get_children()) == 1
        rows.selection_set(rows.get_children()[0])
        button(browser, 'Edit selected')
        edit = test.window('Edit Item Sub Groups')
        field(edit, 'Name', 'Edited sub')
        button(edit, 'Save changes')
        test.drain()
        saved = next(row for row in panel.masters.list_records(panel.actor, 'product_groups', group_section='sub')
                     if row['code'] == 'SUB-NEW')
        assert saved['parent_id'] == main['id'] and saved['name'] == 'Edited sub'
        assert panel.masters.get(panel.actor, 'products', existing_product['id'])['group_id'] == original_sub['id']
        assert panel.masters.get(panel.actor, 'product_groups', original_sub['id']) == original_sub
        assert panel.masters.get(panel.actor, 'product_groups', original_main['id']) == original_main
        test.back(browser, fresh)
        fresh.destroy()
        test.drain()
    finally:
        test.close()


class ItemGroupGuiTests(unittest.TestCase):
    def test_group_menus_create_edit_and_filtered_f8_preserve_entries_and_original_links(self):
        from tests.test_baseline_import import synthetic_archive
        with tempfile.TemporaryDirectory(prefix='bifma-group-gui-') as folder:
            path = Path(folder) / 'source.sqlite3'
            synthetic_archive(path)
            fingerprint = hashlib.sha256(path.read_bytes()).digest()
            result = subprocess.run([sys.executable, '-B', '-m', 'tests.test_item_group_sections', str(path)],
                                    cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=90)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(result.stderr, '')
            self.assertEqual(json.loads(result.stdout), {'passed': True})
            self.assertEqual(hashlib.sha256(path.read_bytes()).digest(), fingerprint)


if __name__ == '__main__':
    if len(sys.argv) == 2 and sys.argv[1].endswith('.sqlite3'):
        _gui_case(Path(sys.argv[1]))
        print(json.dumps({'passed': True}))
    else:
        unittest.main()
