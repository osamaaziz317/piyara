import { validateNewPassword } from './credentials.js';
import { MAX_PASSWORD_LENGTH, MIN_PASSWORD_LENGTH } from './demo.js';
import { emptyRow, options, secondaryPage } from './secondary-components.js';

const numberingFields = [
  ['receipt', 'Cash receipt', 'CRV'], ['payment', 'Cash payment', 'CPV'],
  ['opening', 'Opening balance', 'OB'], ['journal', 'Journal voucher', 'JV'],
  ['transfer', 'Account transfer', 'TRF'], ['purchase', 'Purchase', 'PUR'],
  ['counter', 'Counter sale', 'CS'], ['sale', 'Sale credit', 'SC'],
  ['purchase-return', 'Purchase return', 'PR'], ['sale-return', 'Sale return', 'SR'],
  ['breakage-receive', 'Breakage receive', 'BR'], ['breakage-dispatch', 'Breakage dispatch', 'BD'],
];

const permissionSections = [
  ['coding', 'Coding'], ['financial', 'Financial transactions'], ['inventory', 'Inventory'],
  ['accounting', 'Accounting and late payments'], ['reports', 'Reports'], ['setup', 'Setup'],
];
const permissionActions = [['view', 'View'], ['add', 'Add'], ['edit', 'Edit'], ['delete', 'Delete'], ['print', 'Print'], ['export', 'Export'], ['financial', 'Financial information']];
let setupUnlockedFor = null;

function textField(ui, name, label, value, attributes = '') {
  return `<label class="field"><span>${label}</span><input name="${name}" value="${ui.escape(value ?? '')}" ${attributes}></label>`;
}

function selectField(ui, name, label, choices, value) {
  return `<label class="field"><span>${label}</span><select name="${name}">${options(ui, choices, value)}</select></label>`;
}

function checkbox(name, label, checked, detail = '') {
  return `<label class="sp-check-field"><input name="${name}" type="checkbox" ${checked ? 'checked' : ''}><span>${label}${detail ? `<small>${detail}</small>` : ''}</span></label>`;
}

function saveBar(label) {
  return `<div class="sp-form-footer"><p class="inline-error" role="alert" hidden></p><button type="submit" class="btn btn-primary">${label} <kbd>F6</kbd></button></div>`;
}

function formPanel(title, contents, description = '') {
  return `<section class="card sp-panel"><div class="sp-panel-heading"><div><h2>${title}</h2>${description ? `<p>${description}</p>` : ''}</div></div><div class="sp-panel-body">${contents}</div></section>`;
}

function bindForm(form, ui, save, actions = {}) {
  const error = form.querySelector('.inline-error');
  form.addEventListener('submit', async event => {
    event.preventDefault();
    const submit = form.querySelector('[type=submit]');
    if (submit.disabled) return;
    const dialog = form.closest('dialog');
    const closeButton = dialog?.querySelector('.dialog-header button');
    const stopClose = event => event.preventDefault();
    error.hidden = true;
    submit.disabled = true;
    if (closeButton) closeButton.disabled = true;
    dialog?.addEventListener('cancel', stopClose);
    try {
      await save(new FormData(form));
    } catch (failure) {
      error.textContent = failure.message;
      error.hidden = false;
    } finally {
      submit.disabled = false;
      if (closeButton) closeButton.disabled = false;
      dialog?.removeEventListener('cancel', stopClose);
    }
  });
  if (actions !== null) {
    form.addEventListener('input', () => ui.setDirty?.(true));
    ui.setActions({ refresh: ui.refresh, save: () => form.requestSubmit(), ...actions });
  }
}

function complete(ui, message) {
  ui.setDirty?.(false);
  ui.toast(message);
  ui.refresh();
}

function renderProfile(ui) {
  const page = secondaryPage(ui, 'Business profile', 'Set the details shown in the workspace and on your documents.');
  const profile = ui.demo.profile();
  let logo = /^data:image\/(png|jpeg|webp);base64,/i.test(profile.logo ?? '') ? profile.logo : '';
  page.insertAdjacentHTML('beforeend', `<form class="sp-stack sp-setup-form">
    ${formPanel('Business details', `<div class="form-grid sp-form-grid">
      ${textField(ui, 'name', 'Business name', profile.name, 'required maxlength="100" autocomplete="organization"')}
      ${textField(ui, 'urdu', 'Business name in Urdu', profile.urdu, 'dir="rtl" lang="ur" maxlength="100"')}
      ${textField(ui, 'owner', 'Owner name', profile.owner, 'required maxlength="100" autocomplete="name"')}
      ${textField(ui, 'phone', 'Phone number', profile.phone, 'type="tel" maxlength="40" autocomplete="tel"')}
      ${textField(ui, 'country', 'Country', profile.country, 'required maxlength="60" autocomplete="country-name"')}
      ${textField(ui, 'city', 'City', profile.city, 'required maxlength="60" autocomplete="address-level2"')}
      <label class="field sp-field-wide"><span>Business address</span><textarea name="address" rows="2" maxlength="360" dir="auto">${ui.escape(profile.address)}</textarea></label>
      ${textField(ui, 'financialYear', 'Financial year', profile.financialYear, 'required maxlength="9" placeholder="2026–2027" pattern="[0-9]{4}(?:-|–)[0-9]{4}" title="Enter a range such as 2026–2027"')}
      <div class="sp-profile-id"><span>Client identification</span><strong class="sp-code">${ui.escape(profile.id)}</strong></div>
    </div>`)}
    ${formPanel('Business logo', `<div class="sp-logo-settings"><div class="sp-logo-preview" aria-label="Business logo preview"></div><div class="sp-logo-fields">
      <label class="field"><span>Upload logo</span><input type="file" accept="image/png,image/jpeg,image/webp" name="logoFile"></label>
      <small class="muted">PNG, JPG, or WebP up to 256 KB.</small>
      <button class="btn btn-ghost" type="button" data-remove-logo>Remove logo</button>
    </div></div>`)}
    ${saveBar('Save profile')}
  </form>`);
  const form = page.querySelector('form');
  const input = form.elements.logoFile;
  const preview = form.querySelector('.sp-logo-preview');
  const remove = form.querySelector('[data-remove-logo]');
  let logoRequest = 0;
  let logoRead = Promise.resolve();
  const showLogo = () => {
    preview.innerHTML = logo ? `<img src="${ui.escape(logo)}" alt="Business logo">` : ui.icon('building', 28);
    remove.disabled = !logo;
  };
  showLogo();
  input.addEventListener('change', () => {
    const selected = input.files[0];
    const request = ++logoRequest;
    if (!selected) return;
    const valid = /^(image\/png|image\/jpeg|image\/webp)$/.test(selected.type) && selected.size <= 256 * 1024;
    input.setCustomValidity(valid ? '' : 'Choose a PNG, JPG, or WebP image up to 256 KB.');
    if (!valid) {
      input.reportValidity();
      return;
    }
    const reader = new FileReader();
    logoRead = new Promise(resolve => {
      reader.addEventListener('load', () => {
        if (request === logoRequest) {
          logo = String(reader.result);
          showLogo();
        }
        resolve();
      });
      reader.addEventListener('error', () => {
        if (request === logoRequest) ui.toast('The logo file could not be read.', 'error');
        resolve();
      });
      reader.readAsDataURL(selected);
    });
  });
  remove.addEventListener('click', () => {
    logoRequest += 1;
    input.value = '';
    input.setCustomValidity('');
    logo = '';
    ui.setDirty?.(true);
    showLogo();
  });
  bindForm(form, ui, async values => {
    const { logoFile, ...text } = Object.fromEntries(values);
    const [start, end] = text.financialYear.split(/[-–]/).map(Number);
    if (end !== start + 1) throw new Error('Enter two consecutive years, for example 2026–2027.');
    await logoRead;
    ui.demo.saveProfile({ ...profile, ...text, financialYear: `${start}–${end}`, logo });
    complete(ui, 'Business profile saved.');
  });
  return page;
}

function renderSettings(ui) {
  const page = secondaryPage(ui, 'Business settings', 'Set stock controls, invoice defaults and document numbering for this business.');
  const settings = ui.demo.settings();
  const prices = [['item', 'Use item rate'], ['manual', 'Enter manually']];
  page.insertAdjacentHTML('beforeend', `<form class="sp-stack sp-setup-form">
    ${formPanel('Inventory', `<div class="form-grid sp-form-grid">
      ${selectField(ui, 'stockValuation', 'Stock valuation method', [['weightedAverage', 'Weighted average'], ['fifo', 'First in, first out (FIFO)']], settings.stockValuation)}
      ${selectField(ui, 'breakageTreatment', 'Breakage accounting', [['separate', 'Separate damaged stock'], ['expense', 'Expense damaged stock']], settings.breakageTreatment)}
      ${selectField(ui, 'salePriceDefault', 'Default sale price', prices, settings.salePriceDefault)}
      ${selectField(ui, 'purchasePriceDefault', 'Default purchase price', prices, settings.purchasePriceDefault)}
      <div class="sp-field-wide">${checkbox('negativeStock', 'Allow negative stock', settings.negativeStock, 'Allow documents that bring sellable stock below zero.')}</div>
    </div><div class="sp-settings-links"><button type="button" class="btn btn-secondary" data-coding="units">Units of measurement ${ui.icon('chevron-right', 15)}</button><button type="button" class="btn btn-secondary" data-coding="categories">Item categories ${ui.icon('chevron-right', 15)}</button></div>`)}
    ${formPanel('Invoices and payment terms', `<div class="form-grid sp-form-grid">
      ${selectField(ui, 'invoiceFormat', 'Invoice format', [['standard', 'Standard'], ['compact', 'Compact']], settings.invoiceFormat)}
      ${textField(ui, 'currency', 'Currency code', settings.currency, 'required minlength="3" maxlength="3" pattern="[A-Za-z]{3}" placeholder="PKR"')}
      ${textField(ui, 'decimalPrecision', 'Decimal precision', settings.decimalPrecision, 'required type="number" min="0" max="4" step="1"')}
      ${textField(ui, 'creditLimit', 'Default credit limit', settings.creditLimit, 'required type="number" min="0" step="0.0001"')}
      ${textField(ui, 'paymentTerms', 'Default payment terms (days)', settings.paymentTerms, 'required type="number" min="0" max="3650" step="1"')}
    </div>`)}
    ${formPanel('Document numbering', `<div class="form-grid sp-form-grid sp-prefix-grid">
      ${numberingFields.map(([key, label, prefix]) => textField(ui, `prefix-${key}`, label, settings.numbering?.[key] ?? prefix, 'required maxlength="10" pattern="[A-Za-z0-9](?:[A-Za-z0-9]|-){0,9}" autocapitalize="characters"')).join('')}
    </div>`, 'Set the prefix for new documents using letters, numbers or hyphens.')}
    ${saveBar('Save settings')}
  </form>`);
  const form = page.querySelector('form');
  for (const button of page.querySelectorAll('[data-coding]')) {
    button.addEventListener('click', () => ui.go(`coding.${button.dataset.coding}`));
  }
  bindForm(form, ui, values => {
    const numbering = Object.fromEntries(numberingFields.map(([key]) => [key, values.get(`prefix-${key}`).trim().toUpperCase()]));
    const currency = values.get('currency').toUpperCase();
    ui.demo.saveSettings({ ...ui.demo.settings(), numbering, currency,
      stockValuation: values.get('stockValuation'), breakageTreatment: values.get('breakageTreatment'),
      salePriceDefault: values.get('salePriceDefault'), purchasePriceDefault: values.get('purchasePriceDefault'),
      invoiceFormat: values.get('invoiceFormat'), negativeStock: values.has('negativeStock'),
      decimalPrecision: Number(values.get('decimalPrecision')), creditLimit: Number(values.get('creditLimit')),
      paymentTerms: Number(values.get('paymentTerms')),
    });
    complete(ui, 'Business settings saved.');
  });
  return page;
}

function permissionMatrix(existing = {}) {
  return `<fieldset class="sp-permissions"><legend>Section permissions</legend>
    <div class="table-wrap"><table class="data-table">
      <thead><tr><th scope="col">Section</th>${permissionActions.map(([, label]) => `<th scope="col">${label}</th>`).join('')}</tr></thead>
      <tbody>${permissionSections.map(([section, title]) => `<tr><th scope="row">${title}</th>
        ${permissionActions.map(([action, label]) => {
          const unavailable = section === 'setup' || (action === 'financial' && !['financial', 'accounting', 'reports'].includes(section));
          const checked = !unavailable && (existing[section]?.[action] ?? (action === 'view' && section === 'coding'));
          return `<td>${unavailable ? '<span aria-hidden="true" class="muted">—</span>' : `<input name="${section}.${action}" type="checkbox" aria-label="${title}, ${label}" ${checked ? 'checked' : ''}>`}</td>`;
        }).join('')}
      </tr>`).join('')}</tbody>
    </table></div>
  </fieldset>`;
}

function bindDialogSave(form) {
  form.addEventListener('keydown', event => {
    if (event.key !== 'F6' && !((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 's')) return;
    event.preventDefault();
    event.stopPropagation();
    form.requestSubmit();
  });
}

function editUser(ui, user) {
  const content = document.createElement('form');
  content.className = 'sp-stack sp-user-form';
  const self = user && user.id === ui.demo.session()?.userId;
  const passwordField = self
    ? '<div class="field"><span>Password</span><small>Change your password from the user menu.</small></div>'
    : textField(ui, 'password', user ? 'New password (optional)' : 'Password', '', `type="password" maxlength="${MAX_PASSWORD_LENGTH * 2}" autocomplete="new-password" ${user ? '' : 'required'}`);
  content.innerHTML = `
    <div class="form-grid sp-form-grid">
      ${textField(ui, 'name', 'Full name', user?.name, 'required maxlength="160"')}
      ${textField(ui, 'username', 'Username', user?.username, 'required maxlength="50" autocomplete="off"')}
      ${selectField(ui, 'role', 'User role', [['Clerk', 'Clerk'], ['Administrator', 'Administrator']], user?.role ?? 'Clerk')}
      ${passwordField}
      <div class="sp-field-wide">${checkbox('active', 'Active user', user?.active ?? true)}</div>
    </div>
    ${permissionMatrix(user?.permissions)}
    <p class="muted sp-admin-permission-note" hidden>Administrators can access all sections and operations.</p>
    ${saveBar(user ? 'Save user' : 'Create user')}`;
  const { close } = ui.modal({ title: user ? 'Edit user' : 'Create user', content, wide: true });
  const role = content.elements.role;
  const active = content.elements.active;
  const password = content.elements.password;
  const matrix = content.querySelector('.sp-permissions');
  const update = () => {
    const admin = role.value === 'Administrator';
    matrix.hidden = admin;
    matrix.disabled = admin;
    content.querySelector('.sp-admin-permission-note').hidden = !admin;
  };
  role.addEventListener('change', update);
  update();
  if (user?.needsPassword && password) {
    const passwordLabel = password.closest('.field').querySelector('span');
    const updatePassword = () => {
      password.required = active.checked;
      passwordLabel.textContent = active.checked ? 'New password (required to activate)' : 'New password (optional)';
    };
    active.addEventListener('change', updatePassword);
    updatePassword();
  }
  bindForm(content, ui, async values => {
    const permissions = Object.fromEntries(permissionSections.map(([section]) => [section,
      Object.fromEntries(permissionActions.map(([action]) => [action, values.has(`${section}.${action}`)])),
    ]));
    const password = values.get('password');
    await ui.demo.saveUser({ id: user?.id, name: values.get('name').trim(), username: values.get('username').trim(),
      role: values.get('role'), active: values.has('active'), permissions, ...(password ? { password } : {}),
    });
    close();
    complete(ui, user ? 'User updated.' : 'User created.');
  }, null);
  bindDialogSave(content);
  requestAnimationFrame(() => content.elements.name.focus());
}

function activateOwner(ui, session, username) {
  const content = document.createElement('form');
  content.className = 'sp-stack sp-user-form';
  content.innerHTML = `
    <dl class="sp-audit-meta"><dt>Owner username</dt><dd><strong class="sp-code">${ui.escape(username)}</strong></dd></dl>
    <p class="muted">Setting up the owner account deactivates the built-in demo accounts for this business if they still use the password <strong>demo123</strong>.</p>
    <div class="form-grid sp-form-grid">
      ${textField(ui, 'currentPassword', 'Current administrator password', '', 'type="password" required autocomplete="current-password" autofocus')}
      ${textField(ui, 'name', 'Owner full name', session.name, 'required maxlength="160" autocomplete="name"')}
      ${textField(ui, 'password', 'New password', '', `type="password" required maxlength="${MAX_PASSWORD_LENGTH * 2}" autocomplete="new-password" aria-describedby="owner-password-help"`)}
      ${textField(ui, 'confirmPassword', 'Confirm new password', '', `type="password" required maxlength="${MAX_PASSWORD_LENGTH * 2}" autocomplete="new-password"`)}
      <small class="sp-field-wide muted" id="owner-password-help">Choose a password of ${MIN_PASSWORD_LENGTH} to ${MAX_PASSWORD_LENGTH} characters.</small>
    </div>
    ${saveBar('Activate Super Admin')}`;
  const { close } = ui.modal({ title: 'Set up Super Admin', content, wide: true });
  bindForm(content, ui, async values => {
    const password = values.get('password');
    const currentPassword = values.get('currentPassword');
    try {
      validateNewPassword(password, currentPassword);
    } catch (failure) {
      content.elements.password.select();
      throw failure;
    }
    if (password !== values.get('confirmPassword')) {
      content.elements.confirmPassword.select();
      throw new Error('The new passwords must match.');
    }
    if (!(await ui.canSwitchSession())) return;
    await ui.demo.activateSuperAdmin({ name: values.get('name').trim(), currentPassword, password });
    close();
    await ui.switchSession('setup.users');
    ui.toast('Super Admin activated.');
  }, null);
  bindDialogSave(content);
}

function viewOwner(ui, user) {
  const content = document.createElement('div');
  content.className = 'sp-stack';
  content.innerHTML = `
    <dl class="sp-audit-meta"><dt>Name</dt><dd>${ui.escape(user.name)}</dd><dt>Username</dt><dd>${ui.escape(user.username)}</dd><dt>Role</dt><dd>Super Admin</dd><dt>Status</dt><dd>${user.active ? 'Active' : 'Inactive'}</dd></dl>
    <p class="muted">The Super Admin can change their own password from the user menu.</p>`;
  ui.modal({ title: 'Owner account', content });
}

function renderUsers(ui) {
  const page = secondaryPage(ui, 'Users and permissions', 'Manage company logins, roles and access to individual operations.',
    `<button type="button" class="btn btn-primary" data-add-user>${ui.icon('plus')} New user</button>`);
  const users = ui.demo.users();
  const session = ui.demo.session();
  const owner = ui.demo.superAdminStatus();
  const add = () => editUser(ui);
  const isOwner = user => user?.role === 'Super Admin';
  const openUser = user => isOwner(user) ? viewOwner(ui, user) : editUser(ui, user);
  let selected = users[0];
  let deactivating = false;
  page.insertAdjacentHTML('beforeend', `
    ${session?.role === 'Administrator' && !owner.configured ? formPanel('Business ownership', `<div class="sp-owner-setup"><p class="muted">Create a password-protected owner account for this business.</p><button class="btn btn-secondary" type="button" data-activate-owner>${ui.icon('shield')} Set up Super Admin</button></div>`) : ''}
    <section class="card sp-panel sp-table-panel">
      <div class="sp-panel-heading sp-responsive-heading"><h2>Users</h2>
        <label class="field sp-inline-field"><span class="sp-sr-only">Find a user</span><input type="search" name="user-search" placeholder="Find a user"></label>
      </div>
      <div class="table-wrap"><table class="data-table">
        <thead><tr><th scope="col">Name</th><th scope="col">Username</th><th scope="col">Role</th><th scope="col">Status</th><th scope="col"><span class="sp-sr-only">Actions</span></th></tr></thead>
        <tbody></tbody>
      </table></div>
    </section>`);
  const search = page.querySelector('[name=user-search]');
  const body = page.querySelector('tbody');
  const isSelf = user => user.username === session?.username || user.id === session?.userId;
  const update = () => {
    const query = search.value.trim().toLowerCase();
    const visible = users.filter(user => `${user.name} ${user.username} ${user.role}`.toLowerCase().includes(query));
    if (!visible.includes(selected)) selected = visible[0];
    body.innerHTML = visible.map(user => `<tr data-user-id="${ui.escape(user.id)}">
      <td><strong>${ui.escape(user.name)}</strong>${isSelf(user) ? '<small class="sp-cell-caption">Signed in</small>' : ''}</td>
      <td><span class="sp-code">${ui.escape(user.username)}</span></td><td>${ui.escape(user.role)}</td>
      <td><span class="chip sp-payment-chip ${user.active ? 'sp-payment-current' : 'sp-neutral-chip'}">${user.active ? 'Active' : 'Inactive'}</span></td>
      <td><div class="sp-row-actions"><button type="button" class="btn btn-ghost" data-user-action="${isOwner(user) ? 'view' : 'edit'}">${isOwner(user) ? 'View' : 'Edit'}</button>
        ${user.active && !isSelf(user) && !isOwner(user) ? '<button type="button" class="btn btn-ghost sp-warn-action" data-user-action="deactivate">Deactivate</button>' : ''}</div></td>
    </tr>`).join('') || emptyRow(5, 'No users match.');
  };
  const deactivate = async user => {
    if (!user || !user.active || isSelf(user) || isOwner(user) || deactivating) return;
    deactivating = true;
    try {
      if (!(await ui.confirm(`Deactivate ${user.username}?`, { title: 'Deactivate user', confirmLabel: 'Deactivate', danger: true }))) return;
      await ui.demo.saveUser({ ...user, active: false });
      complete(ui, 'User deactivated.');
    } catch (error) {
      ui.toast(error.message, 'error');
    } finally {
      deactivating = false;
    }
  };
  page.querySelector('[data-activate-owner]')?.addEventListener('click', () => activateOwner(ui, session, owner.username));
  page.querySelector('[data-add-user]').addEventListener('click', add);
  search.addEventListener('input', update);
  body.addEventListener('focusin', event => {
    const row = event.target.closest('[data-user-id]');
    if (row) selected = users.find(user => String(user.id) === row.dataset.userId);
  });
  body.addEventListener('click', event => {
    const row = event.target.closest('[data-user-id]');
    if (!row) return;
    selected = users.find(user => String(user.id) === row.dataset.userId);
    const action = event.target.closest('[data-user-action]')?.dataset.userAction;
    if (action === 'edit' || action === 'view') openUser(selected);
    if (action === 'deactivate') deactivate(selected);
  });
  update();
  ui.setActions({ refresh: ui.refresh, add, edit: () => { if (selected) openUser(selected); }, remove: () => deactivate(selected), search: () => search.focus() });
  return page;
}

function activityTime(ui, timestamp) {
  const date = new Date(timestamp);
  if (Number.isNaN(date.getTime())) return ui.escape(timestamp || '—');
  const time = new Intl.DateTimeFormat('en-PK', {
    timeZone: 'Asia/Karachi', timeStyle: 'medium',
  }).format(date);
  return ui.escape(`${ui.date(date)} ${time}`);
}

function showAudit(ui, row) {
  const content = document.createElement('div');
  content.className = 'sp-stack';
  const record = typeof row.record === 'string' ? row.record : JSON.stringify(row.record ?? '');
  content.innerHTML = `
    <dl class="sp-audit-meta"><dt>Timestamp</dt><dd>${activityTime(ui, row.time)}</dd><dt>User</dt><dd>${ui.escape(row.user)}</dd><dt>Action</dt><dd>${ui.escape(row.action)}</dd><dt>Record</dt><dd>${ui.escape(record || '—')}</dd><dt>Device</dt><dd>${ui.escape(row.device || '—')}</dd></dl>
    <div class="sp-audit-values">${[['Previous values', row.before], ['Updated values', row.after]].map(([label, value]) => `<div><h3>${label}</h3><pre>${ui.escape(value == null ? '—' : JSON.stringify(value, null, 2))}</pre></div>`).join('')}</div>`;
  ui.modal({ title: 'Activity detail', content, wide: true });
}

function renderSecurity(ui) {
  const settings = ui.demo.settings();
  const lock = settings.protectSetup ? `<button class="btn btn-secondary" type="button" data-lock-setup>${ui.icon('lock')} Lock Setup</button>` : '';
  const page = secondaryPage(ui, 'Security and activity', 'Manage session controls and review sign-ins and changes for this business.', lock);
  const logins = ui.demo.loginHistory();
  const audit = ui.demo.audit();
  page.insertAdjacentHTML('beforeend', `
    <form class="sp-stack sp-setup-form">
      ${formPanel('Access controls', `<div class="form-grid sp-form-grid">
        <label class="field"><span>Idle timeout (minutes)</span><input name="sessionTimeout" type="number" min="0" max="1440" step="1" value="${ui.escape(settings.sessionTimeout)}" required><small>Enter 0 to keep the session active until sign-out.</small></label>
        <div>${checkbox('hideSetup', 'Hide Setup from clerks', settings.hideSetup, 'Setup is available to administrators.')}</div>
        <div class="sp-field-wide">${checkbox('protectSetup', 'Require password to open Setup', settings.protectSetup, 'Ask the administrator to unlock Setup after signing in.')}</div>
      </div>`)}
      ${saveBar('Save security settings')}
    </form>
    <section class="card sp-panel sp-table-panel">
      <div class="sp-panel-heading"><div><h2>Login history</h2><p>Successful and failed sign-in attempts, in Pakistan time</p></div></div>
      <div class="table-wrap"><table class="data-table">
        <thead><tr><th scope="col">Timestamp</th><th scope="col">Username</th><th scope="col">Status</th><th scope="col">Device</th></tr></thead>
        <tbody>${logins.map(row => `<tr><td class="sp-nowrap">${activityTime(ui, row.time)}</td><td>${ui.escape(row.username)}</td><td>${ui.escape(row.status)}</td><td class="sp-device">${ui.escape(row.device || '—')}</td></tr>`).join('') || emptyRow(4, 'No sign-in attempts recorded.')}</tbody>
      </table></div>
    </section>
    <section class="card sp-panel sp-table-panel">
      <div class="sp-panel-heading sp-responsive-heading"><div><h2>Activity log</h2><p>Record changes, reversals and administration activity</p></div>
        <label class="field sp-inline-field"><span class="sp-sr-only">Search activity</span><input type="search" name="activity-search" placeholder="Find an activity"></label>
      </div>
      <div class="table-wrap"><table class="data-table">
        <thead><tr><th scope="col">Timestamp</th><th scope="col">User</th><th scope="col">Action</th><th scope="col">Record</th><th scope="col"><span class="sp-sr-only">Details</span></th></tr></thead><tbody data-activity></tbody>
      </table></div>
    </section>`);
  const search = page.querySelector('[name=activity-search]');
  const body = page.querySelector('[data-activity]');
  const update = () => {
    const query = search.value.trim().toLowerCase();
    const visible = audit.filter(row => `${row.user} ${row.action} ${JSON.stringify(row.record)}`.toLowerCase().includes(query));
    body.innerHTML = visible.map(row => `<tr><td class="sp-nowrap">${activityTime(ui, row.time)}</td><td>${ui.escape(row.user)}</td>
      <td>${ui.escape(row.action)}</td><td dir="auto">${ui.escape(typeof row.record === 'string' ? row.record : JSON.stringify(row.record ?? ''))}</td>
      <td><button class="btn btn-ghost" type="button" data-audit="${ui.escape(row.id)}">Details</button></td>
    </tr>`).join('') || emptyRow(5, 'No activity matches.');
  };
  search.addEventListener('input', update);
  body.addEventListener('click', event => {
    const button = event.target.closest('[data-audit]');
    const entry = button && audit.find(row => String(row.id) === button.dataset.audit);
    if (entry) showAudit(ui, entry);
  });
  update();
  page.querySelector('[data-lock-setup]')?.addEventListener('click', async () => {
    const form = page.querySelector('form');
    const data = new FormData(form);
    const changed = Number(data.get('sessionTimeout')) !== settings.sessionTimeout
      || data.has('hideSetup') !== settings.hideSetup || data.has('protectSetup') !== settings.protectSetup;
    if (changed && !(await ui.confirm('Discard unsaved changes and lock Setup?', { title: 'Lock Setup', confirmLabel: 'Lock Setup' }))) return;
    setupUnlockedFor = null;
    ui.setDirty?.(false);
    ui.refresh();
  });
  bindForm(page.querySelector('form'), ui, values => {
    const protectSetup = values.has('protectSetup');
    ui.demo.saveSettings({ ...ui.demo.settings(), sessionTimeout: Number(values.get('sessionTimeout')), hideSetup: values.has('hideSetup'), protectSetup });
    if (protectSetup !== Boolean(settings.protectSetup)) setupUnlockedFor = null;
    complete(ui, 'Security settings saved.');
  }, { search: () => search.focus() });
  return page;
}

function renderBackups(ui) {
  const page = secondaryPage(ui, 'Backup and restore', 'Download a copy of this business’s sample workspace or restore a saved copy.');
  const settings = ui.demo.settings();
  page.insertAdjacentHTML('beforeend', `
    <div class="sp-backup-layout">
      ${formPanel('Create a backup', `<div class="sp-stack"><p class="muted">The backup includes this client’s saved master data, transactions and settings.</p><div><button class="btn btn-primary" type="button" data-backup-download>${ui.icon('download')} Download backup</button></div></div>`)}
      ${formPanel('Restore from a file', `<div class="sp-stack"><p class="muted">Restoring replaces this client’s current sample workspace.</p><label class="field"><span>Backup file</span><input type="file" name="backup" accept=".json,application/json"></label><div><button class="btn btn-secondary" type="button" data-backup-restore disabled>Restore backup</button></div><p class="inline-error" data-restore-error role="alert" hidden></p></div>`)}
    </div>
    <form class="sp-stack sp-setup-form">
      ${formPanel('Backup schedule', `<div class="form-grid sp-form-grid">
        ${selectField(ui, 'backupSchedule', 'Frequency', [['off', 'Off'], ['daily', 'Daily'], ['weekly', 'Weekly']], settings.backupSchedule ?? 'off')}
        ${textField(ui, 'backupTime', 'Time (Pakistan)', settings.backupTime ?? '18:00', 'type="time" required')}
      </div>`, 'Save a preference for future scheduled backups after database integration.')}
      ${saveBar('Save schedule')}
    </form>`);
  const input = page.querySelector('[name=backup]');
  const restore = page.querySelector('[data-backup-restore]');
  const error = page.querySelector('[data-restore-error]');
  input.addEventListener('change', () => { restore.disabled = !input.files.length; error.hidden = true; });
  page.querySelector('[data-backup-download]').addEventListener('click', () => {
    try {
      const snapshot = ui.demo.snapshot();
      const blob = new Blob([JSON.stringify(snapshot, null, 2)], { type: 'application/json' });
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = `FM-Backup-${String(snapshot.clientId).replace(/[^a-z0-9-]/gi, '-')}-${ui.today()}.json`;
      link.click();
      window.setTimeout(() => URL.revokeObjectURL(url), 1000);
      ui.toast('Backup downloaded.');
    } catch (failure) {
      ui.toast(failure.message, 'error');
    }
  });
  restore.addEventListener('click', async () => {
    const file = input.files[0];
    if (!file) return;
    error.hidden = true;
    restore.disabled = true;
    try {
      if (file.size > 10 * 1024 * 1024) throw new Error('Choose a backup under 10 MB.');
      let data;
      try { data = JSON.parse(await file.text()); }
      catch { throw new Error('Choose a valid FM Solutions JSON backup.'); }
      if (!(await ui.confirm('Replace this client’s current workspace with the selected backup?', { title: 'Restore backup', confirmLabel: 'Restore', danger: true }))) return;
      ui.demo.restore(data);
      complete(ui, 'Workspace restored from backup.');
    } catch (failure) {
      error.textContent = failure.message;
      error.hidden = false;
    } finally {
      restore.disabled = false;
    }
  });
  bindForm(page.querySelector('form'), ui, values => {
    ui.demo.saveSettings({ ...ui.demo.settings(), backupSchedule: values.get('backupSchedule'), backupTime: values.get('backupTime') });
    complete(ui, 'Backup schedule preference saved.');
  });
  return page;
}

const setupScreens = {
  'setup.profile': renderProfile,
  'setup.settings': renderSettings,
  'setup.users': renderUsers,
  'setup.security': renderSecurity,
  'setup.backups': renderBackups,
};

function setupLocked(ui) {
  return ui.demo.settings().protectSetup && setupUnlockedFor !== ui.demo.session()?.id;
}

function renderUnlock(ui, unlock) {
  const page = secondaryPage(ui, 'Setup locked', 'Enter your administrator password to open business settings.');
  page.insertAdjacentHTML('beforeend', `<form class="card sp-stack sp-lock-card">
    <span class="sp-lock-icon">${ui.icon('lock', 24)}</span>
    ${textField(ui, 'password', 'Password', '', 'type="password" required autocomplete="current-password"')}
    ${saveBar('Unlock Setup')}
  </form>`);
  const form = page.querySelector('form');
  bindForm(form, ui, async values => {
    const sessionId = ui.demo.session()?.id;
    const verified = await ui.demo.verifyPassword(values.get('password'));
    if (sessionId !== ui.demo.session()?.id) return;
    if (!verified) {
      form.elements.password.select();
      throw new Error('Enter the password you used to sign in.');
    }
    setupUnlockedFor = sessionId;
    unlock();
  }, null);
  ui.setActions({ refresh: ui.refresh, save: () => form.requestSubmit() });
  requestAnimationFrame(() => form.elements.password.focus());
  return page;
}

export function renderSetup(route, ui) {
  if (!ui.demo.can('setup')) return secondaryPage(ui, 'Setup access', 'Sign in as an administrator to manage business settings.');
  const host = document.createElement('div');
  const render = setupScreens[route] ?? renderProfile;
  let content;
  let visibleMode;
  const createView = (renderer, unlock) => {
    const view = {};
    view.node = renderer({ ...ui, setActions: actions => { view.actions = actions; } }, unlock);
    return view;
  };
  const update = () => {
    const locked = Boolean(setupLocked(ui));
    if (visibleMode === locked) return;
    const view = locked ? createView(renderUnlock, update) : (content ??= createView(render));
    host.replaceChildren(view.node);
    ui.setActions(view.actions);
    visibleMode = locked;
  };
  host.addEventListener('fm:show', () => { if (host.isConnected) update(); });
  update();
  return host;
}
