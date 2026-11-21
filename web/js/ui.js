const shapes = {
  dashboard: '<rect x="3" y="3" width="7" height="9" rx="1.4"/><rect x="14" y="3" width="7" height="5" rx="1.4"/><rect x="14" y="12" width="7" height="9" rx="1.4"/><rect x="3" y="16" width="7" height="5" rx="1.4"/>',
  'folder-code': '<path d="M3 7V5a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7Z"/><path d="m10 10-3 3 3 3m4-6 3 3-3 3"/>',
  wallet: '<path d="M20 8V6a2 2 0 0 0-2-2H6a3 3 0 0 0 0 6h13a2 2 0 0 1 2 2v6a2 2 0 0 1-2 2H6a3 3 0 0 1-3-3V7"/><path d="M21 13h-4a2 2 0 0 0 0 4h4m-4-2h.01"/>',
  package: '<path d="m12 3 9 5v8l-9 5-9-5V8l9-5Zm0 18v-8m9-5-9 5-9-5m4-2.7 9 5"/>',
  'book-open': '<path d="M12 5v16m0-16C8 2.7 4 3 2 4v15c3-1 6-1 10 2 4-3 7-3 10-2V4c-2-1-6-1.3-10 1Z"/>',
  'book': '<path d="M5 19V5c0-1.1.9-2 2-2h12v18H7a2 2 0 0 1 0-4h12m-10-9h6m-6 4h6"/>',
  'file-text': '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8l-6-6Zm0 0v6h6M8 13h8m-8 4h8"/>',
  'chart-column': '<path d="M4 3v17h17M9 15v-3m5 3V8m5 7V5"/>',
  settings: '<path d="M9.5 4.8 10 2h4l.5 2.8 2 .9L19 4.1l2.8 2.8-1.6 2.5.9 2 2.9.6v4l-2.9.6-.9 2 1.6 2.5L19 24l-2.5-1.6-2 .9-.5 2.8h-4l-.5-2.8-2-.9L5 24l-2.8-2.8 1.6-2.5-.9-2L0 16v-4l2.9-.6.9-2-1.6-2.5L5 4.1l2.5 1.6 2-.9Z" transform="translate(2 0) scale(.83)"/><circle cx="12" cy="11.6" r="3"/>',
  'help-circle': '<circle cx="12" cy="12" r="9"/><path d="M9 9a3 3 0 1 1 4.3 2.7c-1.3.7-1.3 1.2-1.3 2.3m0 3h.01"/>',
  monitor: '<rect x="3" y="4" width="18" height="13" rx="2"/><path d="M8 21h8m-4-4v4"/>',
  search: '<circle cx="10.8" cy="10.8" r="6.2"/><path d="m16 16 4 4"/>',
  bell: '<path d="M18 9a6 6 0 0 0-12 0c0 6-3 6-3 8h18c0-2-3-2-3-8Zm-8 11a2 2 0 0 0 4 0"/>',
  'chevron-down': '<path d="m6 9 6 6 6-6"/>',
  'chevron-right': '<path d="m9 6 6 6-6 6"/>',
  'chevron-left': '<path d="m15 6-6 6 6 6"/>',
  'arrow-left': '<path d="M19 12H5m7 7-7-7 7-7"/>',
  'arrow-right': '<path d="M5 12h14m-7-7 7 7-7 7"/>',
  'arrow-up-right': '<path d="M7 17 17 7M7 7h10v10"/>',
  'arrow-down-left': '<path d="M17 7 7 17m10 0H7V7"/>',
  'arrow-down': '<path d="M12 5v14m-7-7 7 7 7-7"/>',
  'trending-up': '<path d="m3 17 6-6 4 4 7-8m-5 0h5v5"/>',
  'coins': '<circle cx="9" cy="9" r="6"/><path d="M15 9a6 6 0 1 1-6 6m0-9v6m-1-5h2m-2 4h2"/>',
  'banknote': '<rect x="2.5" y="6" width="19" height="12" rx="2"/><circle cx="12" cy="12" r="2.5"/><path d="M6 12h.01m12 0h.01"/>',
  building: '<path d="M4 21h16M6 21V7l6-4 6 4v14m-8 0v-4h4v4M9 9h.01m6 0h.01M9 13h.01m6 0h.01"/>',
  calendar: '<rect x="3" y="5" width="18" height="16" rx="2"/><path d="M3 10h18M8 3v4m8-4v4m-8 7h.01m4 0h.01m4 0h.01M8 17h.01m4 0h.01"/>',
  clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  receipt: '<path d="M5 3l2 1.5L9 3l3 1.5L15 3l2 1.5L19 3v18l-2-1.5-2 1.5-3-1.5L9 21l-2-1.5L5 21V3Zm3.5 6h7m-7 4h7m-7 4h4"/>',
  'shopping-cart': '<path d="M2 3h3l3 12h10l3-8H6m3 13h.01m8 0h.01"/>',
  alert: '<path d="M10.3 4.4a2 2 0 0 1 3.4 0l8 13.6a2 2 0 0 1-1.7 3H4a2 2 0 0 1-1.7-3l8-13.6ZM12 9v4m0 4h.01"/>',
  'alert-circle': '<circle cx="12" cy="12" r="9"/><path d="M12 8v4m0 4h.01"/>',
  'log-out': '<path d="M10 4H5a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h5m6-13 5 5-5 5m-8-5h13"/>',
  'user': '<circle cx="12" cy="8" r="3.5"/><path d="M4 21v-2a5 5 0 0 1 5-5h6a5 5 0 0 1 5 5v2"/>',
  users: '<path d="M3 20v-2a4 4 0 0 1 4-4h4a4 4 0 0 1 4 4v2m3-6a4 4 0 0 1 3 4v2M16 4a3.5 3.5 0 0 1 0 7"/><circle cx="9" cy="8" r="3.5"/>',
  eye: '<path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7-10-7-10-7Z"/><circle cx="12" cy="12" r="3"/>',
  'eye-off': '<path d="m3 3 18 18m-3.5-3.5A10.1 10.1 0 0 1 12 19c-6.5 0-10-7-10-7a17.9 17.9 0 0 1 4-4.7M10 5.2c.7-.1 1.3-.2 2-.2 6.5 0 10 7 10 7s-1 2-2.9 3.9m-9.2-5.8a3 3 0 0 0 4 4"/>',
  lock: '<rect x="5" y="10" width="14" height="11" rx="2"/><path d="M8 10V7a4 4 0 0 1 8 0v3m-4 5v2"/>',
  shield: '<path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10Z"/><path d="m8.5 11.5 2.4 2.5 4.6-5"/>',
  x: '<path d="m6 6 12 12M6 18 18 6"/>',
  check: '<path d="m5 12 4 4L19 6"/>',
  edit: '<path d="m15 5 4 4m-13 4L16 3a2.8 2.8 0 0 1 4 4L10 17l-5 1 1-5Zm-2 7h16"/>',
  printer: '<path d="M6 8V3h12v5m0 8h2a2 2 0 0 0 2-2v-4a2 2 0 0 0-2-2H4a2 2 0 0 0-2 2v4a2 2 0 0 0 2 2h2m0-3h12v8H6v-8Zm12-2h.01"/>',
  trash: '<path d="M3 6h18M8 6V3h8v3m3 0-1 15H6L5 6m5 4v7m4-7v7"/>',
  history: '<path d="M3 12a9 9 0 1 1 3 6.7M3 6v6h6m3-5v5l3 2"/>',
  refresh: '<path d="M20 7a9 9 0 0 0-15 2m0 0V4m0 5h5M4 17a9 9 0 0 0 15-2m0 0v5m0-5h-5"/>',
  save: '<path d="M18 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h12l4 4v12a2 2 0 0 1-2 2Zm-11 0v-7h10v7M7 3v5h9V3"/>',
  database: '<ellipse cx="12" cy="5" rx="8" ry="3"/><path d="M4 5v14c0 4 16 4 16 0V5M4 12c0 4 16 4 16 0"/>',
  download: '<path d="M12 3v12m-5-5 5 5 5-5M4 15v5h16v-5"/>',
  archive: '<rect x="3" y="3" width="18" height="5" rx="1"/><path d="M5 8v13h14V8m-9 4h4"/>',
  list: '<path d="M8 6h12M8 12h12M8 18h12M4 6h.01M4 12h.01M4 18h.01"/>',
  menu: '<path d="M4 6h16M4 12h16M4 18h16"/>',
  'more-horizontal': '<path d="M5 12h.01m7 0h.01m7 0h.01" stroke-width="3"/>',
  'maximize': '<path d="M8 3H3v5m13-5h5v5M3 16v5h5m13-5v5h-5"/>',
  'minimize': '<path d="M5 12h14"/>',
  'panels': '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M3 9h18m-12 0v11"/>',
  'globe': '<circle cx="12" cy="12" r="9"/><ellipse cx="12" cy="12" rx="4" ry="9"/><path d="M3 12h18"/>',
  phone: '<path d="M7 3H4a1 1 0 0 0-1 1c0 9.5 7.5 17 17 17a1 1 0 0 0 1-1v-3l-5-2-2 2a14 14 0 0 1-7-7l2-2-2-5Z"/>',
  'sliders': '<path d="M5 3v6m0 4v8M12 3v10m0 4v4M19 3v3m0 4v11M3 9h4m3 8h4m3-11h4"/>',
  keyboard: '<rect x="2" y="5" width="20" height="14" rx="2"/><path d="M6 9h.01m4 0h.01m4 0h.01m4 0h.01M6 13h.01m12 0h.01m-8 0h4m-6 3h8"/>',
  layers: '<path d="m12 3 9 5-9 5-9-5 9-5Zm-9 9 9 5 9-5M3 16l9 5 9-5"/>'
};
const aliases = {'layout-dashboard':'dashboard','bar-chart':'chart-column','chart':'chart-column','bank':'building','building-2':'building','check-circle':'check','alert-triangle':'alert','circle-help':'help-circle','pencil':'edit','trash-2':'trash','file':'file-text','printer-check':'printer','rotate-ccw':'history','box':'package','user-plus':'users','shield-check':'shield','circle-dollar-sign':'coins','credit-card':'wallet','map-pin':'globe','arrow-up':'trending-up','upload':'download','external-link':'arrow-up-right'};

export function icon(name, size = 20) {
  const shape = shapes[name] ?? shapes[aliases[name]] ?? shapes['file-text'];
  return `<svg aria-hidden="true" viewBox="0 0 24 24" width="${size}" height="${size}" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round">${shape}</svg>`;
}

export function escape(value) {
  return String(value ?? '').replace(/[&<>"']/g, character => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  }[character]));
}

export function money(value, currency = 'PKR', precision = 2) {
  return new Intl.NumberFormat('en-PK', {
    style: 'currency', currency, currencyDisplay: 'code', minimumFractionDigits: precision, maximumFractionDigits: precision
  }).format(Number(value) || 0);
}

export function today() {
  const formatter = new Intl.DateTimeFormat('en-CA', {timeZone: 'Asia/Karachi', year: 'numeric', month:'2-digit', day:'2-digit'});
  const parts = Object.fromEntries(formatter.formatToParts(new Date()).map(({type, value}) => [type, value]));
  return `${parts.year}-${parts.month}-${parts.day}`;
}

export function date(value, options = {}) {
  if (!value) return '—';
  const valueDate = value instanceof Date ? value : new Date(/^\d{4}-\d{2}-\d{2}$/.test(value) ? `${value}T00:00:00+05:00` : value);
  if (Number.isNaN(valueDate.valueOf())) return '—';
  return new Intl.DateTimeFormat('en-PK', {
    timeZone: 'Asia/Karachi', day: 'numeric', month: 'short', year: 'numeric', ...options
  }).format(valueDate);
}

export function element(tag, className, html = '') {
  const node = document.createElement(tag);
  node.className = className;
  node.innerHTML = html;
  return node;
}

export function mark() {
  return `<span class="brand-mark" aria-hidden="true"><svg viewBox="0 0 36 36" width="28" height="28" fill="none"><path d="M8 27V10h12v4h-8v3h6v4h-6v6H8Z" fill="currentColor"/><path d="m18 27 6-17h4l-6 17h-4Zm6 0 5-13h3l-4.5 13H24Z" fill="#A9C8FF"/></svg></span>`;
}

export function toast(message, level = 'success') {
  const node = element('div', `toast toast-${level}`, `${icon(level === 'success' ? 'check' : 'alert-circle', 18)}<span>${escape(message)}</span><button type="button" aria-label="Dismiss notification">${icon('x', 16)}</button>`);
  node.querySelector('button').onclick = () => node.remove();
  document.getElementById('toasts').append(node);
  window.setTimeout(() => node.remove(), 5300);
}

export function modal({title, content, wide = false}) {
  const active = document.activeElement;
  const dialog = element('dialog', `dialog${wide ? ' dialog-wide' : ''}`, `
    <div class="dialog-header"><h2>${escape(title)}</h2><button type="button" class="btn btn-ghost btn-icon" aria-label="Close dialog">${icon('x', 20)}</button></div>
    <div class="dialog-body"></div>`);
  const id = `dialog-${crypto.randomUUID()}`;
  const heading = dialog.querySelector('h2');
  heading.id = id;
  dialog.setAttribute('aria-labelledby', id);
  const body = dialog.querySelector('.dialog-body');
  if (content instanceof Node) body.append(content);
  else body.innerHTML = content;
  const close = () => dialog.close();
  dialog.querySelector('.dialog-header button').onclick = close;
  dialog.addEventListener('close', () => {
    dialog.remove();
    if (active?.isConnected) active.focus({preventScroll: true});
  }, {once: true});
  document.body.append(dialog);
  dialog.showModal();
  dialog.querySelector('[autofocus]')?.focus();
  return {dialog, close};
}

export function confirm(message, options = {}) {
  return new Promise(resolve => {
    const content = element('div', 'confirm-body', `
      <p>${escape(message)}</p>
      <div class="dialog-actions">
        <button class="btn btn-secondary" data-cancel autofocus type="button">${escape(options.cancelLabel ?? 'Cancel')}</button>
        <button class="btn ${options.danger ? 'btn-danger' : 'btn-primary'}" data-confirm type="button">${escape(options.confirmLabel ?? options.label ?? 'Confirm')}</button>
      </div>`);
    const {dialog, close} = modal({title: options.title ?? 'Confirm action', content});
    dialog.addEventListener('close', () => resolve(false), {once: true});
    content.querySelector('[data-cancel]').onclick = close;
    content.querySelector('[data-confirm]').onclick = () => {
      resolve(true);
      close();
    };
  });
}
