import { emptyRow, options, secondaryPage, stats, urdu } from './secondary-components.js';
import { faq, guides, reportGroups, shortcuts } from './guides.js';
import { renderSetup } from './setup.js';

const paymentStatuses = {
  overdue: 'Overdue',
  'due-soon': 'Due soon',
  current: 'Within terms',
};

function paymentChip(status) {
  const key = Object.hasOwn(paymentStatuses, status) ? status : 'current';
  return `<span class="chip sp-payment-chip sp-payment-${key}">${paymentStatuses[key]}</span>`;
}

function printButton(ui) {
  return ui.demo.can('accounting', 'print')
    ? `<button class="btn btn-secondary" type="button" data-sp-print>${ui.icon('printer')} Print</button>`
    : '';
}

function bindAccountingActions(page, ui, actions = {}) {
  const print = page.querySelector('[data-sp-print]');
  const printPage = () => window.print();
  print?.addEventListener('click', printPage);
  ui.setActions({ refresh: ui.refresh, ...actions, ...(print ? { print: printPage } : {}) });
}

function balanceText(ui, value) {
  if (!value) return ui.money(0);
  return `${ui.money(Math.abs(value))} ${value < 0 ? 'Cr' : 'Dr'}`;
}

function ledgerBody(ui, data) {
  if (!data?.account) return '<div class="card empty">Choose an account to see its ledger.</div>';
  const { account, openingBalance, balance, received, outstanding, rows, invoices } = data;
  return `
    ${stats(ui, [
      ['Opening balance', balanceText(ui, openingBalance), 'archive'],
      ['Payments received', ui.money(received), 'wallet'],
      ['Outstanding invoices', ui.money(outstanding), 'clock'],
      ['Closing balance', balanceText(ui, balance), 'book-open'],
    ])}
    <section class="card sp-panel sp-table-panel">
      <div class="sp-panel-heading">
        <div><h2>${ui.escape(account.name)} ${urdu(ui, account.urdu)}</h2><p>Account activity and running balance</p></div>
        <span class="sp-code">${ui.escape(account.code)}</span>
      </div>
      <div class="table-wrap">
        <table class="data-table">
          <thead><tr><th scope="col">Date</th><th scope="col">Voucher no.</th><th scope="col">Description</th><th scope="col" class="sp-number">Debit</th><th scope="col" class="sp-number">Credit</th><th scope="col" class="sp-number">Payment received</th><th scope="col" class="sp-number">Balance</th></tr></thead>
          <tbody>
            ${(rows ?? []).map(row => `<tr>
              <td class="sp-nowrap">${ui.date(row.date)}</td>
              <td><span class="sp-code">${ui.escape(row.number)}</span></td>
              <td dir="auto">${ui.escape(row.narration || '—')}</td>
              <td class="sp-number">${row.debit ? ui.money(row.debit) : '—'}</td>
              <td class="sp-number">${row.credit ? ui.money(row.credit) : '—'}</td>
              <td class="sp-number">${row.paymentReceived ? ui.money(row.paymentReceived) : '—'}</td>
              <td class="sp-number"><strong>${balanceText(ui, row.balance)}</strong></td>
            </tr>`).join('') || emptyRow(7, 'This account has no posted entries.')}
          </tbody>
        </table>
      </div>
    </section>
    <section class="card sp-panel sp-table-panel">
      <div class="sp-panel-heading"><div><h2>Outstanding invoices</h2><p>Unpaid balances after receipts and credits</p></div></div>
      <div class="table-wrap">
        <table class="data-table">
          <thead><tr><th scope="col">Invoice</th><th scope="col">Due date</th><th scope="col" class="sp-number">Total</th><th scope="col" class="sp-number">Paid</th><th scope="col" class="sp-number">Credits</th><th scope="col" class="sp-number">Outstanding</th><th scope="col">Status</th></tr></thead>
          <tbody>
            ${(invoices ?? []).filter(row => row.outstanding > 0).map(row => `<tr>
              <td><span class="sp-code">${ui.escape(row.number)}</span></td><td class="sp-nowrap">${ui.date(row.dueDate)}</td>
              <td class="sp-number">${ui.money(row.total)}</td><td class="sp-number">${ui.money(row.paid)}</td>
              <td class="sp-number">${row.returned ? ui.money(row.returned) : '—'}</td>
              <td class="sp-number"><strong>${ui.money(row.outstanding)}</strong></td><td>${paymentChip(row.status)}</td>
            </tr>`).join('') || emptyRow(7, 'This account has no outstanding invoices.')}
          </tbody>
        </table>
      </div>
    </section>`;
}

function renderLedger(ui) {
  const page = secondaryPage(ui, 'Party ledger', 'Review debits, credits and outstanding invoices by account.', printButton(ui));
  const groups = [['parties', 'Parties'], ['cash', 'Cash accounts'], ['banks', 'Bank accounts']]
    .map(([kind, label]) => [label, ui.demo.list(kind).map(account => [account.id, [account.name, account.urdu, account.code].filter(Boolean).join(' · ')])]);
  const requested = ui.params?.id;
  const defaultId = requested ?? groups.find(([, choices]) => choices.length)?.[1][0][0];
  page.insertAdjacentHTML('beforeend', `
    <div class="card sp-account-picker">
      <label class="field"><span>Account</span><select name="account"></select></label>
      <label class="field sp-inline-field"><span>Find an account</span><input name="account-search" type="search" placeholder="Name or account code"></label>
    </div>
    <div class="sp-stack" data-ledger-body></div>`);
  const picker = page.querySelector('select');
  const search = page.querySelector('[name=account-search]');
  const body = page.querySelector('[data-ledger-body]');
  const update = () => { body.innerHTML = ledgerBody(ui, picker.value ? ui.demo.ledger(picker.value) : null); };
  const updateOptions = selected => {
    const query = search.value.trim().toLowerCase();
    const markup = groups.map(([label, choices]) => {
      const visible = choices.filter(([, text]) => text.toLowerCase().includes(query));
      return visible.length ? `<optgroup label="${label}">${options(ui, visible, selected)}</optgroup>` : '';
    }).join('');
    picker.innerHTML = markup || '<option value="">No accounts match</option>';
    picker.disabled = !markup;
    update();
  };
  picker.addEventListener('change', update);
  search.addEventListener('input', () => updateOptions(picker.value));
  search.addEventListener('keydown', event => {
    if (event.key === 'Enter' && !picker.disabled) {
      event.preventDefault();
      picker.focus();
    }
  });
  updateOptions(defaultId);
  bindAccountingActions(page, ui, { search: () => search.focus() });
  return page;
}

function trialFooter(ui, columns, debit, credit) {
  return `<tfoot><tr><th scope="row" colspan="${columns - 2}">Total</th><td class="sp-number">${ui.money(debit)}</td><td class="sp-number">${ui.money(credit)}</td></tr></tfoot>`;
}

function trialRows(ui, rows, grouped) {
  return rows.map(row => `<tr>
    <td><span class="sp-code">${ui.escape(row.code)}</span></td>
    <td><strong>${ui.escape(row.name)}</strong>${urdu(ui, row.urdu)}</td>
    <td>${ui.escape(row.group || '—')}</td>
    ${grouped ? `<td>${ui.escape(row.accountType || '—')}</td>` : ''}
    <td class="sp-number">${row.debit ? ui.money(row.debit) : '—'}</td>
    <td class="sp-number">${row.credit ? ui.money(row.credit) : '—'}</td>
  </tr>`).join('') || emptyRow(grouped ? 6 : 5, 'There are no accounts in this view.');
}

function renderTrial(ui, grouped = false) {
  const page = secondaryPage(ui, grouped ? 'Sub trial balance' : 'Trial balance', grouped
    ? 'Review debit and credit balances by account group and category.'
    : 'Review closing account balances and check that debits equal credits.', printButton(ui));
  const { rows, groups, debit, credit } = ui.demo.trialBalance();
  const accountTypes = [...new Set(rows.map(row => row.accountType).filter(Boolean))].sort();
  const columns = grouped ? 6 : 5;
  const difference = Number(debit) - Number(credit);
  const controls = grouped ? `
    <div class="card sp-panel sp-table-panel">
      <div class="sp-panel-heading"><h2>Group totals</h2></div>
      <div class="table-wrap"><table class="data-table">
        <thead><tr><th scope="col">Account group</th><th scope="col" class="sp-number">Debit</th><th scope="col" class="sp-number">Credit</th></tr></thead>
        <tbody>${groups.map(group => `<tr><td><strong>${ui.escape(group.name)}</strong></td><td class="sp-number">${ui.money(group.debit)}</td><td class="sp-number">${ui.money(group.credit)}</td></tr>`).join('') || emptyRow(3, 'No account groups.')}</tbody>
        ${trialFooter(ui, 3, debit, credit)}
      </table></div>
    </div>` : '';
  page.insertAdjacentHTML('beforeend', `
    ${stats(ui, [
      ['Total debit', ui.money(debit), 'book-open'],
      ['Total credit', ui.money(credit), 'book-open'],
      ['Difference', ui.money(difference), difference ? 'alert' : 'shield', difference ? 'Review account balances' : 'Debits and credits are balanced'],
    ])}
    ${controls}
    <section class="card sp-panel sp-table-panel">
      <div class="sp-panel-heading sp-responsive-heading">
        <div><h2>Account balances</h2></div>
        ${grouped ? `<div class="sp-filter-controls">
          <label class="field sp-inline-field"><span class="sp-sr-only">Account group</span><select name="group">${options(ui, [['', 'All account groups'], ...groups.map(group => [group.name, group.name])], '')}</select></label>
          <label class="field sp-inline-field"><span class="sp-sr-only">Account category</span><select name="category">${options(ui, [['', 'All account categories'], ...accountTypes.map(type => [type, type])], '')}</select></label>
        </div>` : ''}
      </div>
      <div class="table-wrap"><table class="data-table">
        <thead><tr><th scope="col">Code</th><th scope="col">Account name</th><th scope="col">Account group</th>${grouped ? '<th scope="col">Category</th>' : ''}<th scope="col" class="sp-number">Debit balance</th><th scope="col" class="sp-number">Credit balance</th></tr></thead>
        <tbody>${trialRows(ui, rows, grouped)}</tbody>
        ${trialFooter(ui, columns, debit, credit)}
      </table></div>
    </section>`);
  const group = page.querySelector('[name=group]');
  const category = page.querySelector('[name=category]');
  const filter = () => {
    const table = page.querySelector('.sp-table-panel:last-child table');
    const visibleRows = rows.filter(row => (!group.value || row.group === group.value) && (!category.value || row.accountType === category.value));
    const sum = name => visibleRows.reduce((total, row) => total + row[name], 0);
    table.tBodies[0].innerHTML = trialRows(ui, visibleRows, grouped);
    table.tFoot.outerHTML = trialFooter(ui, columns, sum('debit'), sum('credit'));
  };
  group?.addEventListener('change', filter);
  category?.addEventListener('change', filter);
  bindAccountingActions(page, ui, group ? { search: () => group.focus() } : {});
  return page;
}

function fiscalYears(ui) {
  const clock = ui.today();
  const [current, month] = clock.split('-').map(Number);
  const dateYear = month < 7 ? current - 1 : current;
  const profileYear = Number.parseInt(ui.demo.profile().financialYear, 10) || dateYear;
  const years = [...new Set([profileYear, ...Array.from({ length: 6 }, (_, index) => dateYear - index)])].sort((a, b) => b - a);
  return { profileYear, choices: years.map(year => [year, `${year}–${year + 1}`]) };
}

function renderProfit(ui) {
  const page = secondaryPage(ui, 'Profit and loss', 'Compare sales, cost of goods, operating expenses and net profit for a financial year.', printButton(ui));
  const { profileYear, choices } = fiscalYears(ui);
  page.insertAdjacentHTML('beforeend', `
    <div class="card sp-account-picker"><label class="field"><span>Financial year</span><select name="year">${options(ui, choices, profileYear)}</select></label></div>
    <div class="sp-stack" data-profit-body></div>`);
  const select = page.querySelector('select');
  const body = page.querySelector('[data-profit-body]');
  const update = () => {
    const profit = ui.demo.profit(select.value);
    const rows = [
      ['Total sales', profit.sales], ['Sales returns', profit.salesReturns], ['Net sales', profit.netSales, 'subtotal'],
      ['Cost of goods sold', profit.costOfGoodsSold], ['Gross profit', profit.grossProfit, 'subtotal'],
      ['Operating expenses', profit.operatingExpenses], ['Other income', profit.otherIncome], ['Net profit', profit.netProfit, 'total'],
    ];
    body.innerHTML = `
      ${stats(ui, [['Net sales', ui.money(profit.netSales), 'file-text'], ['Gross profit', ui.money(profit.grossProfit), 'book-open'], ['Net profit', ui.money(profit.netProfit), 'wallet']])}
      <section class="card sp-panel sp-table-panel">
        <div class="sp-panel-heading"><h2>Financial year ${ui.escape(select.selectedOptions[0].textContent.trim())}</h2></div>
        <div class="table-wrap"><table class="data-table">
          <thead><tr><th scope="col">Income and expense</th><th scope="col" class="sp-number">Amount</th></tr></thead>
          <tbody>${rows.map(([label, amount, emphasis]) => `<tr class="${emphasis ? `sp-${emphasis}` : ''}"><th scope="row">${label}</th><td class="sp-number">${ui.money(amount)}</td></tr>`).join('')}</tbody>
        </table></div>
      </section>`;
  };
  select.addEventListener('change', update);
  update();
  bindAccountingActions(page, ui, { search: () => select.focus() });
  return page;
}

function renderLpd(ui) {
  const page = secondaryPage(ui, 'Late payments', 'Track unpaid credit invoices and prioritize customer follow-up.', printButton(ui));
  const open = ui.demo.overdue();
  const overdue = open.filter(row => row.status === 'overdue');
  const dueSoon = open.filter(row => row.status === 'due-soon');
  const total = rows => rows.reduce((sum, row) => sum + Number(row.outstanding), 0);
  const byParty = new Map();
  for (const invoice of overdue) {
    const summary = byParty.get(invoice.partyId) ?? { ...invoice, count: 0, amount: 0, age: 0 };
    summary.count += 1;
    summary.amount += Number(invoice.outstanding);
    summary.age = Math.max(summary.age, Number(invoice.daysOverdue));
    byParty.set(invoice.partyId, summary);
  }
  const ranked = [...byParty.values()].sort((left, right) => right.amount - left.amount);
  const partyButton = row => `<button class="sp-text-link" type="button" data-ledger-id="${ui.escape(row.partyId)}">${ui.escape(row.party)}</button>${urdu(ui, row.partyUr)}`;
  page.insertAdjacentHTML('beforeend', `
    ${stats(ui, [
      ['Overdue balance', ui.money(total(overdue)), 'alert', `${overdue.length} overdue ${overdue.length === 1 ? 'invoice' : 'invoices'}`],
      ['Due soon', ui.money(total(dueSoon)), 'clock', `${dueSoon.length} ${dueSoon.length === 1 ? 'invoice' : 'invoices'} approaching due date`],
      ['Total outstanding', ui.money(total(open)), 'wallet', `${open.length} open ${open.length === 1 ? 'invoice' : 'invoices'}`],
    ])}
    <section class="card sp-panel sp-table-panel">
      <div class="sp-panel-heading sp-responsive-heading">
        <div><h2>Open invoices</h2><p>Select a customer to open their ledger.</p></div>
        <div class="sp-filter-controls">
          <label class="field sp-inline-field"><span class="sp-sr-only">Search invoices or parties</span><input type="search" name="search" placeholder="Find invoice or party"></label>
          <label class="field sp-inline-field"><span class="sp-sr-only">Payment status</span><select name="status">${options(ui, [['', 'All statuses'], ...Object.entries(paymentStatuses)], ui.params?.status ?? '')}</select></label>
        </div>
      </div>
      <div class="table-wrap"><table class="data-table">
        <thead><tr><th scope="col">Invoice</th><th scope="col">Customer</th><th scope="col">Due date / terms</th><th scope="col" class="sp-number">Paid</th><th scope="col" class="sp-number">Outstanding</th><th scope="col">Status</th></tr></thead>
        <tbody data-open-invoices></tbody>
      </table></div>
    </section>
    <section class="card sp-panel sp-table-panel">
      <div class="sp-panel-heading"><div><h2>Overdue by customer</h2><p>Customer totals across all overdue invoices</p></div></div>
      <div class="table-wrap"><table class="data-table">
        <thead><tr><th scope="col">Customer</th><th scope="col" class="sp-number">Overdue invoices</th><th scope="col" class="sp-number">Oldest overdue</th><th scope="col" class="sp-number">Overdue balance</th></tr></thead>
        <tbody>${ranked.map(row => `<tr><td>${partyButton(row)}</td><td class="sp-number">${row.count}</td><td class="sp-number">${row.age} days</td><td class="sp-number"><strong>${ui.money(row.amount)}</strong></td></tr>`).join('') || emptyRow(4, 'No overdue balances.')}</tbody>
      </table></div>
    </section>`);
  const body = page.querySelector('[data-open-invoices]');
  const search = page.querySelector('[name=search]');
  const status = page.querySelector('[name=status]');
  const update = () => {
    const query = search.value.trim().toLowerCase();
    const visible = open.filter(row => (!status.value || row.status === status.value)
      && `${row.number} ${row.party} ${row.partyUr}`.toLowerCase().includes(query));
    body.innerHTML = visible.map(row => `<tr>
      <td><span class="sp-code">${ui.escape(row.number)}</span><small class="sp-cell-caption">${ui.date(row.date)}</small></td>
      <td>${partyButton(row)}</td>
      <td class="sp-nowrap">${ui.date(row.dueDate)}<small class="sp-cell-caption">${ui.escape(row.terms)} days</small></td>
      <td class="sp-number">${ui.money(row.paid)}</td>
      <td class="sp-number"><strong>${ui.money(row.outstanding)}</strong></td>
      <td>${paymentChip(row.status)}${row.daysOverdue ? `<small class="sp-cell-caption">${ui.escape(row.daysOverdue)} days overdue</small>` : ''}</td>
    </tr>`).join('') || emptyRow(6, 'No open invoices match.');
  };
  search.addEventListener('input', update);
  status.addEventListener('change', update);
  page.addEventListener('click', event => {
    const link = event.target.closest('[data-ledger-id]');
    if (link) ui.go('accounting.ledger', { id: link.dataset.ledgerId });
  });
  update();
  bindAccountingActions(page, ui, { search: () => search.focus() });
  return page;
}

function renderReports(route, ui) {
  const type = route.split('.')[1] === 'inventory' ? 'inventory' : 'financial';
  const group = reportGroups[type];
  const page = secondaryPage(ui, 'Reports', 'Choose a report to see its workspace.');
  const active = group.entries.find(([id]) => id === ui.params?.id)?.[0] ?? group.entries[0][0];
  page.insertAdjacentHTML('beforeend', `
    <div class="sp-report-tabs" aria-label="Report categories">
      ${Object.entries(reportGroups).map(([id, category]) => `<button type="button" class="btn btn-${type === id ? 'primary' : 'secondary'}" data-report-group="${id}" ${type === id ? 'aria-current="page"' : ''}>${category.label}</button>`).join('')}
    </div>
    <div class="sp-report-layout">
      <section class="card sp-panel sp-report-menu" aria-label="${group.label}">
        <div class="sp-panel-heading"><h2>${group.label}</h2></div>
        ${group.entries.map(([id, label]) => `<button class="sp-report-button" type="button" data-report="${id}">${ui.icon('file-text')}<span>${label}</span>${ui.icon('chevron-right')}</button>`).join('')}
      </section>
      <section class="card sp-report-placeholder" aria-live="polite">
        <span class="sp-placeholder-icon">${ui.icon('file-text', 30)}</span>
        <h2 data-report-title></h2>
        <p>The layout for this report will be added when its requirements are supplied.</p>
      </section>
    </div>`);
  const title = page.querySelector('[data-report-title]');
  const select = id => {
    title.textContent = group.entries.find(([key]) => key === id)[1];
    for (const button of page.querySelectorAll('[data-report]')) button.setAttribute('aria-pressed', String(button.dataset.report === id));
  };
  page.addEventListener('click', event => {
    const category = event.target.closest('[data-report-group]');
    const report = event.target.closest('[data-report]');
    if (category && category.dataset.reportGroup !== type) ui.go(`reports.${category.dataset.reportGroup}`);
    if (report) select(report.dataset.report);
  });
  select(active);
  ui.setActions({ refresh: ui.refresh });
  return page;
}

function helpTopic(ui, guide) {
  return `<details class="sp-help-topic">
    <summary><strong>${ui.escape(guide.question)}</strong>${urdu(ui, guide.urduTitle)}${ui.icon('chevron-right')}</summary>
    <div class="sp-help-answer"><p>${ui.escape(guide.answer)}</p><p class="urdu" lang="ur" dir="rtl">${ui.escape(guide.urdu)}</p></div>
  </details>`;
}

function renderHelp(ui) {
  const page = secondaryPage(ui, 'Help', 'Learn to use FM Solutions in English and Urdu.');
  page.insertAdjacentHTML('beforeend', `
    <div class="sp-help-layout">
      <div class="sp-stack">
        <section class="card sp-panel sp-help-panel">
          <div class="sp-panel-heading"><div><h2>Wholesale software user guide</h2><p class="urdu" lang="ur" dir="rtl">ہول سیل سافٹ ویئر استعمال کرنے کا طریقہ</p></div></div>
          ${guides.map(guide => helpTopic(ui, guide)).join('')}
        </section>
        <section class="card sp-panel sp-help-panel">
          <div class="sp-panel-heading"><h2>Frequently asked questions</h2></div>
          ${faq.map(guide => helpTopic(ui, guide)).join('')}
        </section>
      </div>
      <aside class="sp-stack">
        <section class="card sp-panel">
          <div class="sp-panel-heading"><h2>Keyboard shortcuts</h2></div>
          <dl class="sp-shortcuts">${shortcuts.map(([key, label]) => `<div><dt>${label}</dt><dd><kbd>${ui.escape(key)}</kbd></dd></div>`).join('')}</dl>
        </section>
        <section class="card sp-panel">
          <div class="sp-panel-heading"><h2>Contact FM Solutions</h2></div>
          <div class="sp-support"><strong>Fahad Masood</strong><p class="urdu" lang="ur" dir="rtl">مدد کے لیے ہم سے رابطہ کریں</p><a class="btn btn-secondary" href="tel:+923263833835">0326 3833835</a></div>
        </section>
      </aside>
    </div>`);
  ui.setActions({ refresh: ui.refresh });
  return page;
}

const screens = {
  'accounting.ledger': renderLedger,
  'accounting.trial': renderTrial,
  'accounting.sub-trial': ui => renderTrial(ui, true),
  'accounting.profit': renderProfit,
  lpd: renderLpd,
  help: renderHelp,
};

const retainedFields = {
  'accounting.ledger': ['account-search', 'account'],
  'accounting.trial': [],
  'accounting.sub-trial': ['group', 'category'],
  'accounting.profit': ['year'],
  lpd: ['search', 'status'],
};

function renderReadView(route, ui) {
  const host = document.createElement('div');
  let view = screens[route](ui);
  let revision = ui.demo.revision?.();
  let day = ui.today();
  host.append(view);
  host.addEventListener('fm:show', () => {
    const changed = ui.demo.revision?.();
    const today = ui.today();
    if (changed !== undefined && changed === revision && today === day) return;
    revision = changed;
    day = today;
    const active = view.contains(document.activeElement) ? document.activeElement.name : null;
    const controls = retainedFields[route].map(name => [name, view.querySelector(`[name="${name}"]`)?.value]);
    view = screens[route](ui);
    for (const [name, value] of controls) {
      const field = view.querySelector(`[name="${name}"]`);
      if (!field || value === undefined || field.disabled) continue;
      if (field.matches('select') && !Array.from(field.options).some(option => option.value === value)) continue;
      field.value = value;
      field.dispatchEvent(new Event(field.matches('select') ? 'change' : 'input', { bubbles: true }));
    }
    host.replaceChildren(view);
    if (active) view.querySelector(`[name="${active}"]`)?.focus({ preventScroll: true });
  });
  return host;
}

export function renderSecondary(route, ui) {
  if (route.startsWith('setup.')) return renderSetup(route, ui);
  if (route.startsWith('reports.')) return renderReports(route, ui);
  if (Object.hasOwn(retainedFields, route)) return renderReadView(route, ui);
  return screens[route]?.(ui) ?? secondaryPage(ui, 'Page unavailable', 'Choose a workspace from the navigation menu.');
}
