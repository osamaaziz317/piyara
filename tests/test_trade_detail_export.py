"""Trade exports preserve exact scope and recheck every required grant."""

from decimal import Context, localcontext
import unittest
from unittest.mock import patch

from bifma import report_export
from bifma.native_schema import native_transaction
from bifma.report_export import ReportExporter
from bifma.security import AuthorizationError
from tests.test_transactions import TransactionFixture


class TradeExportTests(TransactionFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.exporter = ReportExporter(self.path, self.security)

    def export(self, report, filename='detail.html', actor=None, **filters):
        destination = self.path.parent / filename
        self.exporter.export_html(actor or self.actor, self.company, report, destination, **filters)
        return destination.read_text(encoding='utf-8')

    def reader(self, modules):
        user = self.security.create_user(self.actor, 'reader', 'Copper fields 94 river violet')
        self.security.set_company_access(self.actor, user.id, [self.company])
        for module in modules:
            self.security.set_permissions(self.actor, user.id, module, ['read'])
        return user, self.security.authenticate('reader', 'Copper fields 94 river violet')

    def test_unfiltered_document_totals_once_filtered_header_amounts_omitted(self):
        values = self.trade_values('purchase', quantity='2', rate='10', discount='3', freight='1')
        values['document_number'] = 'HEADER-INVOICE'
        values['lines'] *= 2
        self.post(self.save('purchase', values))
        whole = self.export('purchase_detail')
        self.assertIn('Final document totals (each document once)', whole)
        self.assertIn('>38</bdi>', whole)
        self.assertIn('>40</bdi>', whole)
        self.assertNotIn('>76</bdi>', whole)
        filtered = self.export('purchase_detail', 'filtered.html', product_id=self.product)
        self.assertIn('Final document totals are omitted', filtered)
        self.assertNotIn('Final document totals (each document once)', filtered)
        self.assertNotIn('>38</bdi>', filtered)
        self.assertIn('Line amounts exclude header discounts', filtered)

    def test_saved_bilingual_labels_escaped_and_reversal_has_signed_values(self):
        with native_transaction(self.path) as connection:
            connection.execute("UPDATE products SET name=?,name_urdu=? WHERE id=?", ('<script>old item</script>', 'نمونہ کپ', self.product))
        original = self.post(self.save('purchase', self.trade_values('purchase', quantity='2', rate='12.75')))
        self.reverse(original)
        with native_transaction(self.path) as connection:
            connection.execute("UPDATE products SET name='Changed current item' WHERE id=?", (self.product,))
        text = self.export('purchase_detail')
        self.assertIn('&lt;script&gt;old item&lt;/script&gt;', text)
        self.assertNotIn('<script>', text)
        self.assertNotIn('Changed current item', text)
        self.assertIn('نمونہ کپ', text)
        self.assertIn('Reverses ' + '<bdi dir="auto">' + original['document_number'], text)
        self.assertIn('>-25.5</bdi>', text)
        self.assertIn('>-2</bdi>', text)
        self.assertIn('Full-period quantities by item and saved base unit', text)

    def test_complete_export_exceeds_page_and_preserves_exact_amounts(self):
        values = self.trade_values('purchase', rate='0.01')
        values['document_number'] = 'MORE-THAN-A-PAGE'
        values['lines'] = [dict(values['lines'][0]) for _ in range(501)]
        self.post(self.save('purchase', values))
        with localcontext(Context(prec=3)):
            text = self.export('purchase_detail')
        # 501 document lines plus one grouped item quantity row.
        self.assertEqual(text.count('<tr class="report-row">'), 502)
        self.assertIn('>501</bdi> rows', text)
        self.assertIn('>5.01</bdi>', text)
        with patch('bifma.native_reports.MAX_EXPORT_ROWS', 500):
            with self.assertRaisesRegex(ValueError, 'exceeds'):
                self.export('purchase_detail', 'too-large.html')
        self.assertFalse((self.path.parent / 'too-large.html').exists())

    def test_return_only_permission_cannot_export_invoice_or_combined_activity(self):
        original = self.post(self.save('purchase', self.trade_values('purchase', quantity='2')))
        self.post(self.save('purchase_return', self.trade_values('purchase_return', source=original['lines'][0]['id'])))
        _, actor = self.reader(['purchase_returns'])
        text = self.export('purchase_detail', actor=actor, kind='return')
        self.assertIn('purchase return', text)
        self.assertIn('>-10</bdi>', text)
        for kind in ('all', 'invoice'):
            with self.assertRaises(AuthorizationError):
                self.export('purchase_detail', kind + '.html', actor=actor, kind=kind)
            self.assertFalse((self.path.parent / (kind + '.html')).exists())

    def test_either_permission_revoked_during_render_prevents_publication(self):
        self.post(self.save('purchase', self.trade_values('purchase')))
        user, actor = self.reader(['purchases', 'purchase_returns'])
        real_render = report_export._render
        for module in ('purchases', 'purchase_returns'):
            def revoke(snapshot):
                result = real_render(snapshot)
                self.security.set_permissions(self.actor, user.id, module, [])
                return result
            with self.subTest(module=module), patch.object(report_export, '_render', side_effect=revoke):
                with self.assertRaises(AuthorizationError):
                    self.export('purchase_detail', module + '.html', actor=actor)
            self.assertFalse((self.path.parent / (module + '.html')).exists())
            self.assertEqual(list(self.path.parent.glob('.bifma-report-*')), [])
            self.security.set_permissions(self.actor, user.id, module, ['read'])

    def test_snapshot_is_consistent_when_another_document_posts_during_render(self):
        self.post(self.save('purchase', self.trade_values('purchase', rate='10')))
        real_render = report_export._render
        def post_during_render(snapshot):
            self.post(self.save('purchase', self.trade_values('purchase', rate='17')))
            return real_render(snapshot)
        with patch.object(report_export, '_render', side_effect=post_during_render):
            text = self.export('purchase_detail')
        self.assertIn('>10</bdi>', text)
        self.assertNotIn('>17</bdi>', text)
        self.assertNotIn('>27</bdi>', text)

    def test_sales_and_empty_report_render_with_explicit_filters(self):
        self.opening()
        self.post(self.save('sale', self.trade_values('sale', rate='2.5')))
        text = self.export('sales_detail', kind='invoice', search='D-', date_from='2026-01-02', date_to='2026-01-02', account_id=self.accounts['customer'])
        self.assertIn('Sales / returns detail', text)
        self.assertIn('Party filter (current label)', text)
        self.assertIn('>2.5</bdi>', text)
        empty = self.export('purchase_detail', 'empty.html')
        self.assertIn('No rows in the selected period.', empty)
        self.assertIn('>0</bdi> rows', empty)


if __name__ == '__main__':
    unittest.main()
