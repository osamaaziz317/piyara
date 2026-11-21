import {element} from './ui.js';

const typeLabels = {
  receipt: 'Cash receipt', payment: 'Cash payment', opening: 'Opening balance',
  journal: 'Journal voucher', transfer: 'Account transfer', purchase: 'Purchase',
  counter: 'Counter sale', sale: 'Credit sale', 'purchase-return': 'Purchase return',
  'sale-return': 'Sale return', 'breakage-receive': 'Breakage receive', 'breakage-dispatch': 'Breakage dispatch'
};

export const typeLabel = kind => typeLabels[kind] ?? kind;
export const documentRoute = kind =>
  `${['receipt', 'payment', 'opening', 'journal', 'transfer'].includes(kind) ? 'financial' : 'inventory'}.${kind}`;

const amountFormat = new Intl.NumberFormat('en-PK', {maximumFractionDigits: 0});
const chartColors = [['#2c64df', 'rgba(45, 101, 227, .15)'], ['#119c86', 'rgba(23, 161, 139, .04)']];

function chartTick(value) {
  if (Math.abs(value) >= 1_000_000) return `${(value / 1_000_000).toFixed(1).replace(/\.0$/, '')}m`;
  if (Math.abs(value) >= 1000) return `${(value / 1000).toFixed(0)}k`;
  return String(value);
}

function renderChart(container, series, money) {
  const canvas = container.querySelector('canvas');
  const tooltip = container.querySelector('.chart-tooltip');
  const select = container.querySelector('select');
  const readout = container.querySelector('.chart-readout');
  const ctx = canvas.getContext('2d');
  let values = [];
  let plot = null;
  let active = -1;

  function draw() {
    const bounds = canvas.getBoundingClientRect();
    if (!bounds.width || !bounds.height || !ctx) return;
    const dpr = window.devicePixelRatio || 1;
    const width = bounds.width;
    const height = bounds.height;
    canvas.width = width * dpr;
    canvas.height = height * dpr;
    ctx.scale(dpr, dpr);
    ctx.clearRect(0, 0, width, height);
    ctx.font = '11px Inter, Segoe UI, sans-serif';
    const extremes = values.flatMap(entry => [entry.sales, entry.purchases]);
    const limit = Math.max(1000, ...extremes.map(Math.abs));
    const stepBase = 10 ** Math.floor(Math.log10(limit / 4));
    const step = Math.ceil(limit / (4 * stepBase)) * stepBase;
    const max = Math.max(0, Math.ceil(Math.max(...extremes, 0) / step)) * step || step;
    const min = Math.min(0, Math.floor(Math.min(...extremes, 0) / step)) * step;
    const vertical = height - 34;
    const top = 9;
    const axis = width > 360 ? 43 : 40;
    const right = width - 18;
    const x = index => values.length <= 1 ? (right + axis) / 2 : axis + index * ((right - axis) / (values.length - 1));
    const y = value => top + (max - value) / (max - min) * vertical;
    plot = {x, y, right};
    for (let value = min; value <= max; value += step) {
      const yy = y(value);
      ctx.strokeStyle = '#e6ecf5';
      ctx.setLineDash([3, 4]);
      ctx.beginPath();
      ctx.moveTo(axis, yy);
      ctx.lineTo(right, yy);
      ctx.stroke();
      ctx.setLineDash([]);
      ctx.fillStyle = '#6a7890';
      ctx.textAlign = 'right';
      ctx.textBaseline = 'middle';
      ctx.fillText(chartTick(value), axis - 9, yy);
    }
    ctx.textBaseline = 'alphabetic';
    ctx.textAlign = 'center';
    values.forEach((entry, index) => {
      if (width > 370 || index % 2 === 0 || index === values.length - 1) ctx.fillText(entry.label, x(index), height - 5);
    });

    ['sales', 'purchases'].forEach((key, seriesIndex) => {
      if (!values.length) return;
      const [stroke, fill] = chartColors[seriesIndex];
      const curve = () => {
        ctx.moveTo(x(0), y(values[0][key]));
        for (let i = 1; i < values.length; i += 1) {
          const previous = x(i - 1);
          const midpoint = (previous + x(i)) / 2;
          ctx.bezierCurveTo(midpoint, y(values[i - 1][key]), midpoint, y(values[i][key]), x(i), y(values[i][key]));
        }
      };

      if (seriesIndex === 0 && values.length > 1) {
        const gradient = ctx.createLinearGradient(0, top, 0, top + vertical);
        gradient.addColorStop(0, fill);
        gradient.addColorStop(1, 'rgba(45, 101, 227, 0)');
        ctx.beginPath();
        curve();
        ctx.lineTo(x(values.length - 1), y(0));
        ctx.lineTo(x(0), y(0));
        ctx.closePath();
        ctx.fillStyle = gradient;
        ctx.fill();
      }
      ctx.beginPath();
      curve();
      ctx.lineWidth = 2.5;
      ctx.strokeStyle = stroke;
      ctx.stroke();

      if (active >= 0 || values.length === 1) {
        const index = Math.max(0, active);
        ctx.beginPath();
        ctx.arc(x(index), y(values[index][key]), 4.3, 0, Math.PI * 2);
        ctx.fillStyle = '#fff';
        ctx.fill();
        ctx.stroke();
      }
    });
  }

  function selectPeriod() {
    values = series.slice(-Number(select.value));
    active = -1;
    tooltip.hidden = true;
    readout.textContent = values.map(entry => `${entry.label}. Sales ${money(entry.sales)}. Purchases ${money(entry.purchases)}.`).join(' ');
    draw();
  }

  function showPoint(index) {
    if (!values.length || !plot) return;
    active = Math.max(0, Math.min(index, values.length - 1));
    draw();
    const entry = values[active];
    tooltip.innerHTML = `<strong>${entry.label}</strong><span>Sales <b>${money(entry.sales)}</b></span><span>Purchases <b>${money(entry.purchases)}</b></span>`;
    tooltip.style.left = `${Math.max(0, Math.min(plot.x(active) - 66, plot.right - 138))}px`;
    tooltip.hidden = false;
  }

  canvas.addEventListener('pointermove', event => {
    if (!plot) return;
    const position = event.clientX - canvas.getBoundingClientRect().left;
    const index = values.reduce((best, _, i) => Math.abs(plot.x(i) - position) < Math.abs(plot.x(best) - position) ? i : best, 0);
    if (index !== active) showPoint(index);
  });
  function hidePoint() {
    active = -1;
    tooltip.hidden = true;
    draw();
  }
  canvas.addEventListener('pointerleave', hidePoint);
  canvas.addEventListener('blur', hidePoint);
  canvas.addEventListener('keydown', event => {
    if (!['ArrowLeft', 'ArrowRight'].includes(event.key)) return;
    event.preventDefault();
    showPoint(active < 0 ? 0 : active + (event.key === 'ArrowRight' ? 1 : -1));
  });
  select.onchange = selectPeriod;
  container.addEventListener('fm:show', draw);
  requestAnimationFrame(() => {
    if (container.isConnected) selectPeriod();
  });
}

export function renderDashboard(ui) {
  const {demo, go, escape: safe, icon, money, date} = ui;
  const session = demo.session();
  const profile = demo.profile();
  const canSeeFinance = demo.can('accounting') ||
    ['dashboard', 'coding', 'financial', 'inventory', 'reports'].some(section => demo.can(section, 'financial'));
  const canSeeInventory = demo.can('inventory');
  const summary = demo.dashboard();
  const dashboard = canSeeFinance ? summary : null;
  const canSeeLPD = canSeeFinance && demo.can('accounting');
  const chartData = dashboard?.series ?? [];
  const sessionName = session.name || session.username;
  const greeting = sessionName.split(/\s+/)[0];
  const financialYear = profile.financialYear || demo.settings().financialYear;
  const currency = demo.settings().currency || 'PKR';
  const balance = (value) => `<span class="metric-currency">${safe(currency)}</span>${amountFormat.format(value || 0)}`;
  const kpis = [
    ['Total sales', dashboard?.sales, 'arrow-up-right', 'blue', 'This month'],
    ['Total purchases', dashboard?.purchases, 'shopping-cart', 'purple', 'This month'],
    ['Receivables', dashboard?.receivables, 'arrow-down-left', 'teal', 'Customer balances'],
    ['Payables', dashboard?.payables, 'arrow-up-right', 'amber', 'Supplier balances']
  ];

  const shortcuts = [
    ['inventory.sale', 'Credit sale', 'New customer invoice', 'receipt', 'blue'],
    ['inventory.purchase', 'Purchase', 'Record supplier stock', 'package', 'purple'],
    ['financial.receipt', 'Cash receipt', 'Record a payment in', 'arrow-down-left', 'teal'],
    ['financial.payment', 'Cash payment', 'Record a payment out', 'wallet', 'amber']
  ].filter(([route]) => demo.can(route.split('.')[0], 'add'));

  const activity = summary.activity
    .filter(document => demo.can(documentRoute(document.kind).split('.')[0]))
    .slice(0, 6);
  const lowStock = canSeeInventory ? summary.lowStock : [];
  const dueSoon = canSeeLPD ? demo.overdue().filter(row => row.status === 'due-soon') : [];
  const alerts = [
    ...(canSeeLPD ? [[
      'lpd', 'alert-circle', dashboard.overdueCount ? 'red' : 'teal',
      dashboard.overdueCount ? `${dashboard.overdueCount} overdue invoice${dashboard.overdueCount === 1 ? '' : 's'}` : 'No overdue invoices',
      dashboard.overdueCount ? `${money(dashboard.overdueAmount)} past due` : 'Late Payment Delay'
    ], [
      'lpd', 'clock', 'amber',
      `${dueSoon.length} payment${dueSoon.length === 1 ? '' : 's'} due soon`,
      dueSoon.length ? 'Review payments approaching their due date' : 'Your upcoming payments are clear'
    ]] : []),
    ...(canSeeInventory ? [[
      'coding.items', 'package', 'blue', `${lowStock.length} low stock item${lowStock.length === 1 ? '' : 's'}`,
      lowStock.length ? lowStock.slice(0, 2).map(item => item.name).join(', ') : 'All items are above minimum stock'
    ]] : [])
  ].filter(([route]) => route !== 'coding.items' || demo.can('coding'));

  const node = element('section', 'page dashboard', `
    <div class="dashboard-title page-header">
      <div><h1>Dashboard</h1><p>Sales, stock and payment activity at ${safe(profile.name || profile.businessName)}</p></div>
      <span class="date-tag">${icon('calendar', 16)} ${safe(date(new Date(), {weekday: 'short'}))}</span>
    </div>
    <section class="dashboard-banner" aria-label="FM Solutions">
      <div class="banner-content"><div class="brand-kicker"><span></span> FM SOLUTIONS</div>
        <h2>Welcome back, ${safe(greeting)}.</h2><p>Developed by Fahad Masood <span aria-hidden="true">·</span> <a href="tel:03263833835">0326 3833835</a></p>
      </div>
    </section>
    ${dashboard ? `<div class="metric-grid" aria-label="Business totals">
      ${kpis.map(([label, value, symbol, shade, caption]) => `<div class="card metric">
        <div class="metric-top"><h3>${label}</h3><span class="feature-icon tint-${shade}">${icon(symbol, 17)}</span></div>
        <div class="metric-value numeric">${balance(value)}</div><p>${caption}</p>
      </div>`).join('')}
    </div>` : ''}
    <div class="dashboard-middle${dashboard ? '' : ' dashboard-middle-simple'}">
      ${dashboard ? `<section class="card flow-card" aria-labelledby="flow-heading">
        <div class="card-header">
          <div><h2 id="flow-heading">Sales &amp; purchases</h2><p>Monthly activity <span aria-hidden="true">·</span> ${safe(currency)}</p></div>
          <select class="chart-period" aria-label="Chart period"><option value="7">Last 7 months</option><option value="3">Last 3 months</option><option value="1">This month</option></select>
        </div>
        <div class="chart-legend"><span><i></i>Sales</span><span><i></i>Purchases</span></div>
        <div class="chart-area">
          <canvas tabindex="0" aria-label="Monthly sales and purchases chart. Use the arrow keys to inspect each month." aria-describedby="chart-values"></canvas>
          <div class="chart-tooltip" role="status" hidden></div>
          <p id="chart-values" class="chart-readout"></p>
        </div>
      </section>` : ''}
      <section class="card actions-card">
        <div class="card-header"><div><h2>Quick actions</h2><p>Start a transaction</p></div>${icon('plus', 19)}</div>
        ${shortcuts.length ? `<div class="quick-grid">
          ${shortcuts.map(([route, label, description, symbol, shade]) => `<button class="quick-action" type="button" data-go="${route}" data-new="true">
            <span class="feature-icon tint-${shade}">${icon(symbol, 17)}</span>
            <strong>${label}</strong><small>${description}</small><span class="quick-arrow">${icon('arrow-up-right', 13)}</span>
          </button>`).join('')}
        </div>` : '<p class="empty">No transaction entry permissions.</p>'}
      </section>
    </div>
    <div class="dashboard-bottom">
      <section class="card activity-card">
        <div class="card-header"><div><h2>Recent transactions</h2><p>Latest documents across this business</p></div>
          ${demo.can('financial') ? `<button class="link-button" type="button" data-go="financial">Transactions ${icon('arrow-right',14)}</button>` : ''}
        </div>
        ${activity.length ? `<div class="table-wrap"><table class="data-table dashboard-table"><thead><tr>
          <th scope="col">Document</th><th scope="col">Party</th><th scope="col">Date</th><th scope="col" class="numeric">Amount</th>
        </tr></thead><tbody>${activity.map(document => `<tr>
          <td><button class="document-link" type="button" data-go="${documentRoute(document.kind)}" data-id="${safe(document.id)}"><span class="table-icon">${icon('file-text', 15)}</span><span><strong>${safe(document.number)}</strong><small>${safe(typeLabel(document.kind))}</small></span></button></td>
          <td><span>${safe(document.party || '—')}</span>${document.partyUr ? `<bdi class="table-urdu urdu" lang="ur" dir="rtl">${safe(document.partyUr)}</bdi>` : ''}</td>
          <td class="activity-date">${safe(date(document.date, {year: undefined}))}</td>
          <td class="numeric activity-amount">${safe(money(document.total))}</td>
        </tr>`).join('')}</tbody></table></div>` : '<p class="empty">No transactions to show.</p>'}
      </section>
      <section class="card alert-card">
        <div class="card-header"><div><h2>Needs attention</h2><p>Payment reminders and stock alerts</p></div>${icon('bell', 18)}</div>
        <div class="alert-items">
          ${alerts.map(([route, symbol, shade, label, caption]) => `<button class="alert-item" type="button" data-go="${route}">
            <span class="feature-icon tint-${shade}">${icon(symbol, 16)}</span>
            <span><strong>${safe(label)}</strong><small>${safe(caption)}</small></span>${icon('chevron-right', 14)}
          </button>`).join('') || '<p class="empty">No alerts to show.</p>'}
        </div>
      </section>
    </div>
    ${dashboard ? `
    <section class="card position-card">
      <div class="card-header"><div><h2>Financial position</h2>${financialYear ? `<p>Financial year ${safe(financialYear)}</p>` : ''}</div>${icon('chart-column',18)}</div>
      <dl class="position-grid">
        ${[
          ['Cash in hand', dashboard.cash], ['Stock value', dashboard.stockValue], ['Outstanding payments', dashboard.outstanding],
          ['This month’s profit', dashboard.monthProfit], ['Yearly profit', dashboard.yearProfit]
        ].map(([label, value]) => `<div><dt>${label}</dt><dd>${safe(money(value))}</dd></div>`).join('')}
      </dl>
    </section>` : ''}`);

  if (dashboard) {
    const flowCard = node.querySelector('.flow-card');
    renderChart(flowCard, chartData, money);
    node.addEventListener('fm:show', () => flowCard.dispatchEvent(new Event('fm:show')));
  }
  node.addEventListener('click', event => {
    const button = event.target.closest('[data-go]');
    if (button) go(button.dataset.go, button.dataset.id ? {id: button.dataset.id} :
      button.dataset.new ? {fresh: true} : button.dataset.go === 'coding.items' ? {lowStock: true} : undefined);
  });
  ui.setActions({add: shortcuts[0] ? () => go(shortcuts[0][0], {fresh: true}) : undefined, refresh: ui.refresh});
  return node;
}
