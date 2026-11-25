import {demo, MIN_PASSWORD_LENGTH, MAX_PASSWORD_LENGTH} from './demo.js';
import {date, money, today, escape, element, icon, mark, modal, confirm, toast} from './ui.js';
import {documentRoute, renderDashboard, typeLabel} from './dashboard.js';
import {renderEntries} from './entries.js';
import {renderSecondary} from './secondary.js';

const groups = [
  {id: 'dashboard', title: 'Dashboard', symbol: 'dashboard'},
  {id: 'coding', title: 'Coding', symbol: 'folder-code', children: [
    ['parties', 'Party accounts'], ['items', 'Items'], ['cities', 'Cities'],
    ['categories', 'Item categories'], ['units', 'Units of measurement'], ['groups', 'Account groups'],
    ['warehouses', 'Warehouses'], ['cash', 'Cash accounts'], ['banks', 'Banks'],
    ['methods', 'Payment methods'], ['salesmen', 'Salesmen'], ['suppliers', 'Suppliers'], ['customers', 'Customers']
  ]},
  {id: 'financial', title: 'Financial Transactions', symbol: 'wallet', children: [
    ['receipt', 'Cash receipt voucher'], ['payment', 'Cash payment voucher'], ['opening', 'Opening balances'],
    ['journal', 'Journal voucher'], ['transfer', 'Account transfer']
  ]},
  {id: 'inventory', title: 'Inventory', symbol: 'package', children: [
    ['purchase', 'Purchase'], ['counter', 'Counter sale'], ['sale', 'Sale credit'],
    ['purchase-return', 'Purchase return'], ['sale-return', 'Sale return'],
    ['breakage-receive', 'Breakage receive'], ['breakage-dispatch', 'Breakage dispatch']
  ]},
  {id: 'accounting', title: 'Accounting', symbol: 'book-open', children: [
    ['ledger', 'Party ledger'], ['trial', 'Trial balance'], ['sub-trial', 'Sub trial balance'], ['profit', 'Profit & loss']
  ]},
  {id: 'lpd', title: 'Late Payments', symbol: 'clock'},
  {id: 'reports', title: 'Reports', symbol: 'chart-column', children: [
    ['financial', 'Financial reports'], ['inventory', 'Inventory reports']
  ]},
  {id: 'setup', title: 'Setup', symbol: 'settings', system: true, children: [
    ['profile', 'Business profile'], ['settings', 'Business preferences'], ['users', 'Users & permissions'],
    ['security', 'Security & activity'], ['backups', 'Backup & restore']
  ]},
  {id: 'help', title: 'Help', symbol: 'help-circle', system: true}
];
const rootDefaults = {accounting: 'accounting.ledger', reports: 'reports.financial', setup: 'setup.profile'};
const categories = new Map(groups.map(group => [group.id, group]));
const app = document.querySelector('#application');
const version = '0.1.0';
const desktop = window.fmDesktop || {};
const opened = new Map();
const expanded = new Set();
const pkClock = new Intl.DateTimeFormat('en-PK', {
  timeZone: 'Asia/Karachi', hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false
});
let current = '';
let shell = null;
let activePopup = null;
let loginAutoSizer = null;
let loginResizeFrame = 0;
let lastLoginWindowSize = '';
let desktopResizeQueue = Promise.resolve();

function groupId(route) {
  return route.split('.')[0];
}
function label(route) {
  const [group, child] = route.split('.');
  return categories.get(group)?.children?.find(([id]) => id === child)?.[1] ?? categories.get(group)?.title ?? route;
}
function sectionsAllowed(route) {
  const section = groupId(route);
  if (['dashboard', 'help'].includes(section)) return true;
  return demo.can(section === 'lpd' ? 'accounting' : section);
}
function initials(name) {
  return String(name || '?').trim().split(/\s+/).slice(0, 2).map(word => word[0]).join('').toUpperCase();
}
function formattedMoney(value, precision) {
  const settings = demo.settings();
  const decimals = Number(precision ?? settings.decimalPrecision ?? settings.precision ?? 2);
  const currency = settings.currency || 'PKR';
  try {
    return money(value, currency, decimals);
  } catch {
    return money(value);
  }
}
function remember(key, value) {
  const name = `fm.login.${key}`;
  if (value === undefined) return localStorage.getItem(name) || '';
  if (value) localStorage.setItem(name, value);
  else localStorage.removeItem(name);
  return value;
}

function stopLoginAutoSizer() {
  loginAutoSizer?.disconnect();
  loginAutoSizer = null;
  if (loginResizeFrame) cancelAnimationFrame(loginResizeFrame);
  loginResizeFrame = 0;
  lastLoginWindowSize = '';
}

function resizeDesktopWindow(request) {
  if (!desktop.windowToken) return Promise.resolve();
  const operation = desktopResizeQueue.catch(() => {}).then(async () => {
    const response = await fetch('/window', {
      method: 'POST',
      headers: {'X-FM-Desktop': desktop.windowToken, 'Content-Type': 'application/json'},
      body: JSON.stringify(request)
    });
    if (!response.ok) throw new Error(`Could not resize the application window (${response.status}).`);
  });
  desktopResizeQueue = operation;
  return operation;
}

function clearWorkspace() {
  stopLoginAutoSizer();
  document.querySelectorAll('dialog[open]').forEach(dialog => dialog.close());
  shell = null;
  activePopup = null;
  current = '';
  opened.clear();
  expanded.clear();
  document.querySelector('#toasts').replaceChildren();
}

function renderLogin() {
  clearWorkspace();
  document.title = 'FM Solutions | Sign in';
  const clients = demo.clients();
  const formView = element('main', 'login-view', `
    <div class="login-frame">
      <aside class="login-art">
        <div class="login-brand">${mark()}<div><strong>FM Solutions</strong><small>Wholesale management</small></div></div>
        <div><h2>Wholesale Management &amp; Accounting</h2><footer>Developed by Fahad Masood<br><a href="tel:03263833835">0326 3833835</a></footer></div>
      </aside>
      <section class="login-form-side" aria-labelledby="login-heading">
        <h1 id="login-heading">Sign in</h1>
        <p>Select your business and enter your credentials.</p>
        <form class="login-form">
          <label class="field"><span>Business</span><select name="business" required>${clients.map(client => `<option value="${escape(client.id)}">${escape(client.name)}</option>`).join('')}</select></label>
          <label class="field"><span>Username</span><input name="username" autocomplete="username" required maxlength="80" placeholder="Enter username"></label>
          <div class="field"><label for="login-password">Password</label><div class="password-field">
            <input id="login-password" name="password" type="password" autocomplete="current-password" required placeholder="Enter password">
            <button class="btn btn-ghost btn-icon" data-password type="button" aria-label="Show password" aria-pressed="false">${icon('eye', 18)}</button>
          </div></div>
          <div class="login-options"><label class="field-check"><input name="remember" type="checkbox"> Remember username</label></div>
          <p class="inline-error" role="alert" hidden></p>
          <button class="btn btn-primary login-submit" type="submit">Sign in ${icon('arrow-right', 17)}</button>
        </form>
        <div class="demo-login"><span><strong>Demo account</strong> · admin / demo123</span><button type="button" class="link-button" data-demo>Use demo</button></div>
      </section>
    </div>`);
  app.replaceChildren(formView);
  const loginFrame = formView.querySelector('.login-frame');
  const resizeLoginWindow = () => {
    loginResizeFrame = 0;
    const size = `${loginFrame.offsetWidth}x${loginFrame.offsetHeight}`;
    if (size === lastLoginWindowSize) return;
    lastLoginWindowSize = size;
    resizeDesktopWindow({
      action: 'resize-login',
      width: loginFrame.offsetWidth,
      height: loginFrame.offsetHeight,
      viewportWidth: window.innerWidth,
      viewportHeight: window.innerHeight
    }).catch(failure => {
      console.error(failure);
      toast(failure.message, 'error');
    });
  };
  loginAutoSizer = new ResizeObserver(() => {
    if (!loginResizeFrame) loginResizeFrame = requestAnimationFrame(resizeLoginWindow);
  });
  loginAutoSizer.observe(loginFrame);
  loginResizeFrame = requestAnimationFrame(resizeLoginWindow);
  const form = formView.querySelector('form');
  const {username, password, business, remember: saveName} = form.elements;
  if (clients.some(client => client.id === remember('business'))) business.value = remember('business');
  username.value = remember('username');
  saveName.checked = Boolean(username.value);
  const error = form.querySelector('.inline-error');
  const submit = form.querySelector('[type="submit"]');
  const demoButton = formView.querySelector('[data-demo]');
  const updateDemoHint = () => {
    demoButton.parentElement.hidden = demo.hasSuperAdmin(business.value);
  };
  updateDemoHint();
  business.addEventListener('change', updateDemoHint);
  demoButton.onclick = () => {
    username.value = 'admin';
    password.value = 'demo123';
    error.hidden = true;
    submit.focus();
  };
  formView.querySelector('[data-password]').onclick = event => {
    const button = event.currentTarget;
    const showing = password.type === 'password';
    password.type = showing ? 'text' : 'password';
    button.innerHTML = icon(showing ? 'eye-off' : 'eye', 18);
    button.setAttribute('aria-label', showing ? 'Hide password' : 'Show password');
    button.setAttribute('aria-pressed', String(showing));
  };
  password.addEventListener('keydown', event => {
    if (event.key === 'Enter' && !event.isComposing) {
      event.preventDefault();
      form.requestSubmit();
    }
  });
  form.onsubmit = async event => {
    event.preventDefault();
    if (submit.disabled) return;
    const controls = [...form.elements, demoButton];
    controls.forEach(control => { control.disabled = true; });
    form.setAttribute('aria-busy', 'true');
    error.hidden = true;
    try {
      await demo.signIn(business.value, username.value, password.value);
      remember('username', saveName.checked ? username.value : '');
      remember('business', saveName.checked ? business.value : '');
      stopLoginAutoSizer();
      await desktopResizeQueue.catch(() => {});
      let resizeFailure = null;
      try {
        await resizeDesktopWindow({action: 'maximize'});
      } catch (failure) {
        resizeFailure = failure;
      }
      renderShell();
      if (resizeFailure) toast(resizeFailure.message, 'error');
    } catch (failure) {
      error.textContent = failure.message;
      error.hidden = false;
    } finally {
      controls.forEach(control => { control.disabled = false; });
      form.removeAttribute('aria-busy');
      if (!error.hidden && form.isConnected) {
        password.focus();
        password.select();
      }
    }
  };
  business.focus({preventScroll: true});
}

function navigation() {
  const renderGroup = (group) => {
    const isExpanded = expanded.has(group.id);
    if (!sectionsAllowed(group.id)) {
      return `<div class="nav-row"><button class="nav-link" type="button" disabled aria-label="Setup, administrator access required">
        <span class="nav-icon">${icon('lock', 17)}</span>Setup
      </button></div>`;
    }
    const links = group.children?.map(([key, name]) => {
      const route = `${group.id}.${key}`;
      return `<button class="nav-link" type="button" data-nav="${route}">${escape(name)}</button>`;
    }).join('');
    return `<div class="nav-block">
      <div class="nav-row${links ? ' nav-root' : ''}" data-nav-row="${group.id}">
        <button class="nav-link" type="button" data-nav="${group.id}"><span class="nav-icon">${icon(group.symbol, 17)}</span>${escape(group.title)}</button>
        ${links ? `<button class="nav-toggle" type="button" data-toggle="${group.id}" aria-label="${escape(group.title)} sections" aria-controls="nav-${group.id}" aria-expanded="${isExpanded}">${icon('chevron-right', 14)}</button>` : ''}
      </div>
      ${links ? `<div class="nav-children" id="nav-${group.id}" ${isExpanded ? '' : 'hidden'}>${links}</div>` : ''}
    </div>`;
  };
  const showLockedSetup = demo.settings().hideSetup === false;
  const visible = groups.filter(group => sectionsAllowed(group.id) || (group.id === 'setup' && showLockedSetup));
  return `<p class="nav-label">Workspace</p>${visible.filter(group => !group.system).map(renderGroup).join('')}
    <p class="nav-label secondary-label">System</p>${visible.filter(group => group.system).map(renderGroup).join('')}`;
}

function renderShell(initialRoute = 'dashboard') {
  const session = demo.session();
  if (!session) {
    renderLogin();
    return;
  }
  clearWorkspace();
  const name = session.name || session.username;
  const role = session.roleLabel || session.role || '';
  shell = element('div', 'workspace', `
    <aside class="sidebar">
      <div class="side-brand">${mark()}<div><strong>FM Solutions</strong><small>Wholesale &amp; Accounting</small></div></div>
      <nav class="side-navigation" aria-label="Main navigation">${navigation()}</nav>
      <div class="side-footer"><div class="side-client"><span class="side-client-icon">${icon('building', 17)}</span>
        <div><strong data-business></strong><small data-client></small></div>
      </div></div>
    </aside>
    <div class="workspace-main">
      <header class="workspace-header">
      <div class="topbar">
        <div class="top-left">
          <button class="btn btn-ghost btn-icon mobile-toggle" type="button" data-mobile aria-label="Main navigation" aria-expanded="false">${icon('menu',19)}</button>
          <div class="top-breadcrumb"><span data-crumb>Workspace</span>${icon('chevron-right',13)}<strong data-route-title>Dashboard</strong></div>
        </div>
        <div class="top-right">
          <button class="btn global-search" data-search type="button" aria-label="Search workspace">${icon('search', 16)}<span>Search workspace</span><kbd>Ctrl K</kbd></button>
          <div class="popup-wrapper">
            <button type="button" class="btn btn-ghost btn-icon notice-button" data-popup="notices" aria-label="Notifications" aria-expanded="false">${icon('bell',19)}<i data-notice-dot hidden></i></button>
            <div class="popup notice-popup" data-menu="notices" hidden></div>
          </div>
          <div class="popup-wrapper">
            <button type="button" class="btn btn-ghost window-button" data-popup="window" aria-label="Window" title="Window" aria-expanded="false">${icon('panels',18)}<span>Window</span></button>
            <div class="popup" data-menu="window" hidden></div>
          </div>
          <div class="top-rule" aria-hidden="true"></div>
          <div class="popup-wrapper">
            <button type="button" class="avatar-button" data-popup="user" aria-label="User menu" aria-expanded="false">
              <span class="avatar">${escape(initials(name))}</span><span class="avatar-meta"><strong class="avatar-name">${escape(name)}</strong><small class="avatar-role">${escape(role)} · ${escape(session.username)}</small></span>
              ${icon('chevron-down', 13)}
            </button>
            <div class="popup" data-menu="user" hidden>
              <h3>${escape(name)}</h3><p class="popup-detail">@${escape(session.username)} · ${escape(role)}</p>
              <hr class="popup-separator">
              <button type="button" data-change-password>${icon('lock',16)} Change password</button>
              <button type="button" data-logout>${icon('log-out',16)} Sign out or switch business</button>
            </div>
          </div>
        </div>
      </div>
      <div class="statusbar" aria-label="Workspace status">
        <span><i class="session-dot" aria-hidden="true"></i><span data-status-client></span></span>
        <span>${icon('calendar',13)} <span data-fy></span></span>
        <span>${icon('clock',13)} <time data-clock></time> PKT</span>
        <span>${escape(desktop.ip || 'Local preview')} <b aria-hidden="true">·</b> v${version}</span>
      </div>
      <div class="open-tabs" aria-label="Open sections" hidden></div>
      </header>
      <main id="workspace-content" aria-label="Workspace"></main>
    </div>`);
  app.replaceChildren(shell);
  shell.querySelector('.side-navigation').addEventListener('click', event => {
    const trigger = event.target.closest('[data-toggle], [data-nav]');
    if (!trigger) return;
    if ('toggle' in trigger.dataset) toggleGroup(trigger.dataset.toggle);
    else {
      const route = trigger.dataset.nav;
      if (!route.includes('.') && categories.get(route)?.children) toggleGroup(route, true);
      go(route);
    }
  });
  shell.querySelector('.topbar').addEventListener('click', event => {
    const button = event.target.closest('button');
    if (!button) return;
    if ('mobile' in button.dataset) {
      button.setAttribute('aria-expanded', String(shell.classList.toggle('menu-open')));
    } else if ('search' in button.dataset) {
      closePopup();
      openSearch();
    } else if ('popup' in button.dataset) {
      togglePopup(button);
    } else if ('changePassword' in button.dataset) {
      closePopup(true);
      openPasswordChange();
    } else if ('logout' in button.dataset) {
      closePopup();
      signOut();
    } else if ('windowAction' in button.dataset) {
      const action = button.dataset.windowAction;
      closePopup();
      windowAction(action);
    } else if ('noticeRoute' in button.dataset) {
      closePopup();
      go(button.dataset.noticeRoute, button.dataset.noticeRoute === 'coding.items' ? {lowStock: true} : undefined);
    }
  });
  syncIdentity();
  go('dashboard');
  if (initialRoute !== 'dashboard') go(initialRoute);
}

function syncIdentity() {
  const profile = demo.profile();
  const settings = demo.settings();
  const session = demo.session();
  const client = demo.clients().find(entry => entry.id === session.clientId);
  const business = profile.name || profile.businessName || client?.name || session.clientId;
  const clientId = profile.code || profile.clientCode || session.clientId;
  shell.querySelector('[data-business]').textContent = business;
  shell.querySelector('[data-client]').textContent = `Client ${String(clientId).toUpperCase()}`;
  shell.querySelector('[data-status-client]').textContent = `${business} · ${clientId}`;
  shell.querySelector('[data-fy]').textContent = `FY ${profile.financialYear || settings.financialYear || '—'}`;
  updateClock();
}

function toggleGroup(id, force) {
  const button = shell.querySelector(`[data-toggle="${id}"]`);
  if (!button) return;
  const next = force ?? !expanded.has(id);
  if (next) expanded.add(id);
  else expanded.delete(id);
  button.setAttribute('aria-expanded', String(next));
  shell.querySelector(`#nav-${id}`).hidden = !next;
}

function syncNavigation() {
  const id = groupId(current);
  shell.querySelector('[data-crumb]').textContent = current.includes('.') ? label(id) : 'Workspace';
  shell.querySelector('[data-route-title]').textContent = label(current);
  shell.querySelectorAll('[data-nav-row]').forEach(row => {
    row.classList.toggle('selected', row.dataset.navRow === id);
    row.classList.toggle('current', row.dataset.navRow === current);
  });
  shell.querySelectorAll('[data-nav]').forEach(button => {
    const isCurrent = button.dataset.nav === current;
    button.classList.toggle('current', isCurrent && current.includes('.'));
    if (isCurrent) button.setAttribute('aria-current', 'page');
    else button.removeAttribute('aria-current');
  });
  shell.classList.remove('menu-open');
  shell.querySelector('[data-mobile]').setAttribute('aria-expanded', 'false');

  const tabbar = shell.querySelector('.open-tabs');
  tabbar.hidden = opened.size < 2;
  tabbar.replaceChildren(...[...opened.values()].map(tab => {
    const active = tab.route === current;
    const row = element('div', `work-tab${active ? ' active' : ''}`, `
      <button type="button" ${active ? 'aria-current="page"' : ''}>${escape(label(tab.route))}${tab.dirty ? ' ·' : ''}</button>
      ${tab.route === 'dashboard' ? '' : `<button type="button" aria-label="Close ${escape(label(tab.route))}">${icon('x',12)}</button>`}`);
    row.firstElementChild.onclick = () => go(tab.route);
    if (row.children.length > 1) row.lastElementChild.onclick = () => closeTab(tab.route);
    return row;
  }));
}

function createTab(route, params = {}) {
  const tab = {route, params, actions: {}, node: null, dirty: false, scroll: 0, lastFocus: null};
  const ui = {
    go, params, demo, toast, confirm, icon, escape, date, today, modal,
    money: formattedMoney,
    refresh: () => refreshTab(tab),
    canSwitchSession: () => mayDiscard([...opened.values()]),
    switchSession,
    setActions: actions => { tab.actions = actions; },
    setDirty: dirty => { tab.dirty = Boolean(dirty); }
  };
  try {
    if (route === 'dashboard') tab.node = renderDashboard(ui);
    else if (['coding', 'financial', 'inventory'].includes(groupId(route))) tab.node = renderEntries(route, ui);
    else tab.node = renderSecondary(route, ui);
    if (!(tab.node instanceof HTMLElement)) throw new Error('This section could not be loaded.');
  } catch (error) {
    console.error(error);
    tab.node = element('section', 'page', `<div class="page-header"><h1>${escape(label(route))}</h1></div><div class="inline-error" role="alert">${escape(error.message)}</div>`);
  }
  tab.node.hidden = true;
  tab.node.dataset.section = route;
  tab.node.addEventListener('focusin', event => { tab.lastFocus = event.target; });
  shell.querySelector('#workspace-content').append(tab.node);
  opened.set(route, tab);
  return tab;
}

async function mayDiscard(tabs) {
  const changed = tabs.filter(tab => tab?.dirty);
  if (!changed.length) return true;
  const list = changed.map(tab => label(tab.route)).join(', ');
  return confirm(`Discard unsaved changes in ${list}?`, {title: 'Unsaved changes', confirmLabel: 'Discard changes', danger: true});
}

async function go(request, params, reload = false) {
  if (!shell || !demo.session()) return;
  const route = rootDefaults[request] || request;
  if (!sectionsAllowed(route)) {
    toast('Your role does not have access to this section.', 'error');
    return;
  }
  const tab = opened.get(route);
  const dashboardNeedsRefresh = current && route === 'dashboard' && route !== current;
  const differentRecord = params && (params.fresh || JSON.stringify(params) !== JSON.stringify(tab?.params));
  const replace = reload || dashboardNeedsRefresh || differentRecord;
  if (tab?.dirty && replace && !(await mayDiscard([tab]))) return;
  if (current && current !== route) {
    const section = groupId(route);
    if (groupId(current) !== section) {
      for (const id of expanded) if (id !== section) toggleGroup(id, false);
    }
    const previous = opened.get(current);
    if (previous) {
      previous.scroll = window.scrollY;
      previous.node.hidden = true;
    }
  }
  if (replace) tab?.node.remove();
  const next = tab && !replace ? tab : createTab(route, params ?? tab?.params);
  next.node.hidden = false;
  current = route;
  if (route.includes('.')) toggleGroup(groupId(route), true);
  document.title = `FM Solutions | ${label(route)}`;
  syncIdentity();
  syncNavigation();
  syncNoticeDot();
  if (opened.size > 1) window.scrollTo(0, next.scroll);
  next.node.dispatchEvent(new Event('fm:show'));
  if (!document.querySelector('dialog[open]') && !next.node.contains(document.activeElement)) {
    const target = next.lastFocus instanceof HTMLElement && next.node.contains(next.lastFocus)
      ? next.lastFocus : next.node.querySelector('h1');
    if (target?.tagName === 'H1') target.tabIndex = -1;
    target?.focus({preventScroll: true});
  }
}

async function refreshTab(tab) {
  await go(tab.route, tab.params, true);
}

async function closeTab(route) {
  const tab = opened.get(route);
  if (!tab || route === 'dashboard') return;
  if (tab.dirty && !(await mayDiscard([tab]))) return;
  tab.node.remove();
  opened.delete(route);
  if (route === current) await go('dashboard');
  else syncNavigation();
}

async function closeAllTabs() {
  const tabs = [...opened.values()].filter(tab => tab.route !== 'dashboard');
  if (tabs.some(tab => tab.dirty) && !(await mayDiscard(tabs))) return;
  tabs.forEach(tab => {
    tab.node.remove();
    opened.delete(tab.route);
  });
  await go('dashboard');
}

async function signOut() {
  const tabs = [...opened.values()];
  if (tabs.some(tab => tab.dirty) && !(await mayDiscard(tabs))) return;
  demo.signOut();
  renderLogin();
}

function switchSession(route) {
  const session = demo.session();
  if (session && remember('business') === session.clientId) remember('username', session.username);
  renderShell(route);
}

function openPasswordChange() {
  const content = element('form', 'password-change-form', `
    <label class="field"><span>Current password</span><input name="currentPassword" type="password" autocomplete="current-password" required autofocus></label>
    <label class="field"><span>New password</span><input name="newPassword" type="password" autocomplete="new-password" minlength="${MIN_PASSWORD_LENGTH}" maxlength="${MAX_PASSWORD_LENGTH * 2}" required>
      <small>Use at least ${MIN_PASSWORD_LENGTH} characters.</small></label>
    <label class="field"><span>Confirm new password</span><input name="confirmPassword" type="password" autocomplete="new-password" maxlength="${MAX_PASSWORD_LENGTH * 2}" required></label>
    <p class="inline-error" role="alert" hidden></p>
    <div class="dialog-actions"><button type="button" class="btn btn-secondary" data-cancel>Cancel</button>
      <button type="submit" class="btn btn-primary">Save password <kbd>F6</kbd></button></div>`);
  const {dialog, close} = modal({title: 'Change password', content});
  const form = content.elements;
  const error = content.querySelector('.inline-error');
  const submit = content.querySelector('[type="submit"]');
  const cancel = content.querySelector('[data-cancel]');
  const closeButton = dialog.querySelector('.dialog-header button');
  cancel.onclick = close;
  dialog.addEventListener('cancel', event => {
    if (submit.disabled) event.preventDefault();
  });
  content.addEventListener('keydown', event => {
    if (event.key !== 'F6' && !((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 's')) return;
    event.preventDefault();
    content.requestSubmit();
  });
  content.addEventListener('submit', async event => {
    event.preventDefault();
    if (submit.disabled) return;
    error.hidden = true;
    if (form.newPassword.value !== form.confirmPassword.value) {
      error.textContent = 'The new passwords do not match.';
      error.hidden = false;
      form.confirmPassword.focus();
      return;
    }
    const controls = [...form, closeButton];
    controls.forEach(control => { control.disabled = true; });
    try {
      await demo.changePassword(form.currentPassword.value, form.newPassword.value);
      close();
      toast('Password changed.');
    } catch (failure) {
      error.textContent = failure.message;
      error.hidden = false;
    } finally {
      controls.forEach(control => { control.disabled = false; });
    }
  });
}

function updateClock() {
  const clock = shell?.querySelector('[data-clock]');
  if (clock) {
    clock.dateTime = new Date().toISOString();
    clock.textContent = `${date(new Date())} · ${pkClock.format(new Date())}`;
  }
}

function notifications() {
  const notices = [];
  if (demo.can('accounting')) {
    const payments = demo.overdue();
    const overdue = payments.filter(row => row.status === 'overdue');
    const soon = payments.filter(row => row.status === 'due-soon');
    if (overdue.length) notices.push(['lpd', 'alert-circle', `${overdue.length} overdue invoice${overdue.length === 1 ? '' : 's'}`, `${formattedMoney(overdue.reduce((total, row) => total + row.outstanding, 0))} past due`]);
    if (soon.length) notices.push(['lpd', 'clock', `${soon.length} payment${soon.length === 1 ? '' : 's'} due soon`, 'View outstanding customer invoices']);
  }
  if (demo.can('coding') && demo.can('inventory')) {
    const low = demo.list('items').filter(item => item.active !== false && item.stock <= item.minimumStock);
    if (low.length) notices.push(['coding.items', 'package', `${low.length} low stock item${low.length === 1 ? '' : 's'}`, low.slice(0, 3).map(item => item.name).join(', ')]);
  }
  return notices;
}

function syncNoticeDot() {
  const dot = shell.querySelector('[data-notice-dot]');
  try {
    dot.hidden = !notifications().length;
  } catch (error) {
    dot.hidden = true;
    console.error(error);
  }
}

function closePopup(restore = false) {
  if (!activePopup) return;
  activePopup.menu.hidden = true;
  activePopup.trigger.setAttribute('aria-expanded', 'false');
  if (restore) activePopup.trigger.focus();
  activePopup = null;
}

const windowItems = [
  ['minimize', 'Minimize', 'minimize'], ['maximize', 'Maximize', 'maximize'], ['restore', 'Restore', 'panels'],
  ['fullscreen', 'Full screen', 'maximize'], ['switch', 'Switch open sections', 'layers'],
  ['close-section', 'Close section', 'x'], ['close-all', 'Close all open sections', 'archive'], ['close-window', 'Close window', 'log-out']
];

function togglePopup(button) {
  if (activePopup?.trigger === button) {
    closePopup(true);
    return;
  }
  closePopup();
  const name = button.dataset.popup;
  const menu = shell.querySelector(`[data-menu="${name}"]`);
  if (name === 'notices') {
    const rows = notifications();
    menu.innerHTML = `<h3>Notifications</h3><hr class="popup-separator">${rows.map(([route, symbol, title, detail]) => `
      <button class="notice-line" type="button" data-notice-route="${route}">${icon(symbol,16)}<span><strong>${escape(title)}</strong><small>${escape(detail)}</small></span></button>`).join('') || '<p class="popup-detail">You are up to date.</p>'}`;
  } else if (name === 'window') {
    menu.innerHTML = '<h3>Window</h3>' + windowItems.map(([key, title, symbol], index) => `
      ${index === 4 ? '<hr class="popup-separator">' : ''}<button type="button" data-window-action="${key}">${icon(symbol,16)}${title}</button>`).join('');
  }
  menu.hidden = false;
  button.setAttribute('aria-expanded', 'true');
  activePopup = {menu, trigger: button};
  menu.querySelector('button')?.focus();
}

async function windowAction(action) {
  if (action === 'close-section') return closeTab(current);
  if (action === 'close-all') return closeAllTabs();
  if (action === 'switch') {
    const list = element('div', 'global-results');
    for (const tab of opened.values()) {
      const button = element('button', 'global-result', `${icon('panels',17)} <span>${escape(label(tab.route))}</span>${icon('chevron-right', 15)}`);
      button.type = 'button';
      if (tab.route === current) button.setAttribute('aria-current', 'page');
      list.append(button);
      button.onclick = () => { box.close(); go(tab.route); };
    }
    const box = modal({title: 'Open sections', content: list});
    return;
  }
  if (action === 'fullscreen') {
    try {
      if (document.fullscreenElement) await document.exitFullscreen();
      else await document.documentElement.requestFullscreen();
    } catch (error) {
      toast(error.message, 'error');
    }
    return;
  }
  if (action === 'close-window') {
    if (!(await mayDiscard([...opened.values()]))) return;
    opened.forEach(tab => { tab.dirty = false; });
    demo.signOut();
  }
  if (action === 'restore' && document.fullscreenElement) await document.exitFullscreen();
  if (desktop.windowToken) {
    try {
      const response = await fetch('/window', {
        method: 'POST', headers: {'X-FM-Desktop': desktop.windowToken, 'Content-Type': 'text/plain'}, body: action
      });
      if (response.ok) return;
    } catch {}
  }
  if (action === 'maximize') shell.classList.add('focus-mode');
  if (action === 'restore') {
    shell.classList.remove('focus-mode');
  }
  if (action === 'minimize') toast('Use the title bar to minimize the application.', 'info');
  if (action === 'close-window') {
    window.close();
    if (!window.closed) renderLogin();
  }
}

function openSearch() {
  if (!shell || document.querySelector('dialog[open]')) return;
  const content = element('div', 'workspace-search-dialog', `
    <label class="field"><span>Parties, items, invoices and vouchers</span><input type="search" placeholder="Type a name, code or document number" class="search-dialog-input" autofocus></label>
    <div class="global-results"></div>`);
  const box = modal({title: 'Search workspace', content});
  const input = content.querySelector('input');
  const list = content.querySelector('.global-results');
  const entries = [];
  if (demo.can('coding')) {
    for (const [kind, title, symbol] of [['parties', 'Party account', 'users'], ['items', 'Item', 'package']]) {
      entries.push(...demo.list(kind).map(record => ({
        primary: record.name, urdu: record.urdu, detail: `${title} · ${record.code}`, symbol, route: `coding.${kind}`, id: record.id,
        match: `${record.name} ${record.urdu} ${record.code} ${record.barcode || ''}`
      })));
    }
  }
  if (demo.can('financial') || demo.can('inventory')) {
    entries.push(...demo.documents().filter(document => sectionsAllowed(documentRoute(document.kind))).map(document => ({
      primary: document.number, detail: `${typeLabel(document.kind)} · ${date(document.date)}`, symbol: 'file-text', route: documentRoute(document.kind), id: document.id,
      match: `${document.number} ${document.reference || ''} ${document.narration || ''} ${document.party || ''} ${typeLabel(document.kind)}`
    })));
  }
  const normalize = value => value.normalize('NFC').toLocaleLowerCase('en-PK');
  entries.forEach(entry => entry.match = normalize(entry.match));
  let selected = -1;
  let matches = [];

  function pick(index) {
    const result = matches[index];
    if (!result) return;
    box.close();
    go(result.route, {id: result.id});
  }

  function renderResults() {
    const words = normalize(input.value).trim().split(/\s+/).filter(Boolean);
    matches = words.length ? entries.filter(entry => words.every(word => entry.match.includes(word))).slice(0, 16) : [];
    selected = -1;
    list.innerHTML = matches.length ? matches.map((entry, index) => `
      <button type="button" class="global-result" data-index="${index}">
        <span class="feature-icon tint-blue">${icon(entry.symbol,16)}</span><span><strong>${escape(entry.primary)} ${entry.urdu ? `<bdi class="urdu" dir="rtl" lang="ur">${escape(entry.urdu)}</bdi>` : ''}</strong><small>${escape(entry.detail)}</small></span>
        ${icon('chevron-right',15)}
      </button>`).join('') : `<p class="empty" role="status">${words.length ? 'No matches in this workspace.' : 'Search in English or Urdu.'}</p>`;
  }

  input.oninput = renderResults;
  input.onkeydown = event => {
    if (!matches.length) return;
    if (event.key === 'Enter') {
      event.preventDefault();
      pick(Math.max(0, selected));
    } else if (['ArrowDown', 'ArrowUp'].includes(event.key)) {
      event.preventDefault();
      selected = (selected + (event.key === 'ArrowDown' ? 1 : selected < 0 ? 0 : -1) + matches.length) % matches.length;
      [...list.children].forEach((button, index) => button.classList.toggle('search-selected', index === selected));
      list.children[selected].scrollIntoView({block: 'nearest'});
    }
  };
  list.onclick = event => {
    const result = event.target.closest('[data-index]');
    if (result) pick(Number(result.dataset.index));
  };
  renderResults();
}

function enterNext(event) {
  const field = event.target;
  const form = field.closest('form');
  if (!form || !field.matches('input:not([type=button]):not([type=submit]):not([type=file]), select')) return;
  const focusable = [...form.querySelectorAll('input:not([type=hidden]):not([type=button]):not([type=submit]), select, textarea')].filter(control =>
    !control.disabled && !control.readOnly && control.tabIndex >= 0 && control.getClientRects().length
  );
  if (!focusable.includes(field)) return;
  const next = focusable[focusable.indexOf(field) + 1];
  if (next) {
    event.preventDefault();
    next.focus();
    if (next.matches('input[type=text], input[type=number]')) next.select();
  } else if (form.querySelector('[type=submit]')) {
    event.preventDefault();
    form.requestSubmit();
  }
}

document.addEventListener('click', event => {
  if (activePopup && !event.target.closest('.popup-wrapper')) closePopup();
  if (shell?.classList.contains('menu-open') && !event.target.closest('.sidebar') && !event.target.closest('[data-mobile]')) {
    shell.classList.remove('menu-open');
    shell.querySelector('[data-mobile]').setAttribute('aria-expanded', 'false');
  }
});

document.addEventListener('keydown', event => {
  if (event.defaultPrevented || event.isComposing) return;
  const modifier = event.ctrlKey || event.metaKey;
  if (event.key === 'Enter' && !modifier && !event.altKey) enterNext(event);
  if (event.defaultPrevented || !shell || document.querySelector('dialog[open]')) return;
  if (event.key === 'Escape' && activePopup) {
    event.preventDefault();
    closePopup(true);
    return;
  }
  if (activePopup && ['ArrowDown', 'ArrowUp'].includes(event.key)) {
    const buttons = [...activePopup.menu.querySelectorAll('button:not(:disabled)')];
    if (!buttons.length) return;
    event.preventDefault();
    const index = buttons.indexOf(document.activeElement);
    buttons[(index + (event.key === 'ArrowDown' ? 1 : -1) + buttons.length) % buttons.length].focus();
    return;
  }
  if (modifier && event.key.toLowerCase() === 'k') {
    event.preventDefault();
    openSearch();
    return;
  }
  if (modifier && ['Tab', 'PageDown', 'PageUp'].includes(event.key)) {
    event.preventDefault();
    const tabs = [...opened.keys()];
    const forward = event.key !== 'PageUp' && !event.shiftKey;
    go(tabs[(tabs.indexOf(current) + tabs.length + (forward ? 1 : -1)) % tabs.length]);
    return;
  }
  const action = modifier
    ? {s: 'save', f: 'search'}[event.key.toLowerCase()]
    : {F2: 'search', F3: 'add', F4: 'edit', F5: 'refresh', F6: 'save', F7: 'print', F8: 'remove', Escape: 'escape'}[event.key];
  if (!action) return;
  const tab = opened.get(current);
  const run = tab?.actions[action] || {search: openSearch, refresh: () => refreshTab(tab), escape: () => closeTab(current)}[action];
  if (!run) return;
  event.preventDefault();
  if (!event.repeat) run();
});

for (const name of ['pointerdown', 'keydown']) {
  document.addEventListener(name, () => {
    if (shell) demo.touch?.();
  }, {passive: true});
}
window.addEventListener('beforeunload', event => {
  if ([...opened.values()].some(tab => tab.dirty)) event.preventDefault();
});
window.addEventListener('resize', () => {
  opened.get(current)?.node.dispatchEvent(new Event('fm:show'));
});
setInterval(() => {
  if (!shell) return;
  if (!demo.session()) {
    renderLogin();
    toast('Session ended. Sign in to continue.', 'info');
  } else updateClock();
}, 1000);
if (demo.session()) renderShell();
else renderLogin();
