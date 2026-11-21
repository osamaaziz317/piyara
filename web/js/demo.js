import {
  CLIENTS, DOCUMENT_TYPES, OPERATIONS, SECTIONS, clerkPermissions, defaultSettings, fiscalYear, seed, today,
} from './demo-seed.js';
import {
  accountBalance, allOutstanding, calculateDocumentTotals, dashboardTotals, enforceNonnegativeBalances,
  ledgerTotals, numberValue, profitTotals, publicDocument, replay, stockSummary, trialTotals,
} from './demo-accounting.js';
import { createCredential, matchesCredential, validateNewPassword } from './credentials.js';

export { calculateDocumentTotals, DOCUMENT_TYPES, today };
export { MAX_PASSWORD_LENGTH, MIN_PASSWORD_LENGTH } from './credentials.js';

const STORAGE_PREFIX = 'fm-solutions:prototype:1';
const SESSION_KEY = `${STORAGE_PREFIX}:session`;
const SUPER_ADMIN_ROLE = 'Super Admin';
const TENANTS = new Map(CLIENTS.map((client) => [client.id, client]));
const accountsWithBalances = new Set(['parties', 'cash', 'banks']);
const masterPrefixes = {
  parties: 'A', customers: 'C', suppliers: 'S', items: 'IT', cities: 'CITY', categories: 'CAT',
  units: 'UN', groups: 'GR', warehouses: 'WH', cash: 'CA', banks: 'BA', methods: 'PM', salesmen: 'SM',
};
const accountPickerSections = ['financial', 'inventory', 'accounting'];
const pickerSections = {
  parties: accountPickerSections, cash: accountPickerSections, banks: accountPickerSections,
  items: ['financial', 'inventory'], warehouses: ['financial', 'inventory'], methods: ['financial'],
};
const masterFields = {
  parties: ['country', 'phone', 'cityCode', 'address', 'accountType', 'groupId', 'openingBalance', 'openingType', 'creditLimit', 'paymentTerms', 'remarks'],
  items: ['category', 'unit', 'purchasePrice', 'salePrice', 'openingStock', 'minimumStock', 'barcode', 'description'],
  cities: ['country'], warehouses: ['cityCode'], salesmen: ['phone', 'remarks'],
  cash: ['openingBalance', 'openingType', 'remarks'], banks: ['openingBalance', 'openingType', 'bankName', 'accountNumber', 'remarks'],
};
const accountTypes = ['Customer', 'Supplier', 'Expense', 'Income', 'Asset', 'Liability', 'Equity', 'Other'];
const clone = (value) => JSON.parse(JSON.stringify(value));
const clean = (value) => String(value ?? '').trim();
const lower = (value) => clean(value).toLowerCase();
const now = () => new Date().toISOString();
const stamp = () => Date.now();
const cache = new Map();
const fallbacks = { localStorage: new Map(), sessionStorage: new Map() };
let signInAttempt = 0;

function readStorage(storage, key) {
  return globalThis[storage]?.getItem(key) ?? fallbacks[storage].get(key) ?? null;
}

function writeStorage(storage, key, value) {
  if (globalThis[storage]) {
    if (value === null) globalThis[storage].removeItem(key);
    else globalThis[storage].setItem(key, value);
  } else if (value === null) {
    fallbacks[storage].delete(key);
  } else {
    fallbacks[storage].set(key, value);
  }
}

function put(clientId, data, book) {
  const raw = JSON.stringify(data);
  writeStorage('localStorage', `${STORAGE_PREFIX}:${clientId}`, raw);
  const entry = { raw, data, book };
  cache.set(clientId, entry);
  return entry;
}

function store(clientId) {
  if (!TENANTS.has(clientId)) throw new Error('Select a business to continue.');
  const key = `${STORAGE_PREFIX}:${clientId}`;
  const raw = readStorage('localStorage', key);
  if (!raw) return put(clientId, seed(TENANTS.get(clientId)));
  const cached = cache.get(clientId);
  if (cached?.raw === raw) return cached;
  const data = JSON.parse(raw);
  const entry = { raw, data, book: undefined };
  cache.set(clientId, entry);
  return entry;
}

function bookFor(entry) {
  entry.book ??= replay(entry.data);
  return entry.book;
}

function getToken() {
  const encoded = readStorage('sessionStorage', SESSION_KEY);
  return encoded ? JSON.parse(encoded) : null;
}

function setToken(token) {
  writeStorage('sessionStorage', SESSION_KEY, token ? JSON.stringify(token) : null);
}

function sessionFor(clientId, user) {
  return {
    id: globalThis.crypto.randomUUID(), clientId, userId: user.id, credentialVersion: user.credentialVersion,
    activeAt: stamp(), signedInAt: now(),
  };
}

function sameCredential(before, after) {
  return Boolean(before && after?.active && before.id === after.id && before.credentialVersion === after.credentialVersion
    && before.password === after.password);
}

function applyCredential(user, credential) {
  delete user.password;
  user.credential = credential;
  user.credentialVersion = globalThis.crypto.randomUUID();
}

function superAdmin(data) {
  return data.users.find((user) => user.id === data.superAdminId && user.role === SUPER_ADMIN_ROLE && user.active);
}

function availableOwnerUsername(users) {
  const taken = new Set(users.map(({ username }) => lower(username)));
  let username = 'superadmin';
  for (let suffix = 2; taken.has(username); suffix += 1) username = `superadmin${suffix}`;
  return username;
}

const isPrivileged = (user) => user.role === 'Administrator' || user.role === SUPER_ADMIN_ROLE;

function deviceLabel() {
  return globalThis.navigator?.platform || 'Desktop demo';
}

function context(required = true) {
  const token = getToken();
  if (token && TENANTS.has(token.clientId)) {
    const entry = store(token.clientId);
    const user = entry.data.users.find((record) => record.id === token.userId && record.active
      && (record.role !== SUPER_ADMIN_ROLE || entry.data.superAdminId === record.id));
    const timeout = entry.data.settings.sessionTimeout * 60000;
    if (user && user.credentialVersion === token.credentialVersion) {
      if (!timeout || stamp() - token.activeAt < timeout) return { token, entry, user };
      const data = clone(entry.data);
      auditLog(data, user.username, 'Session timed out', user.username);
      put(token.clientId, data, entry.book);
    }
    setToken(null);
  }
  if (required) throw new Error('Sign in to continue.');
  return null;
}

function allowed(user, section, action) {
  if (['dashboard', 'help', 'window'].includes(section) && action === 'view') return true;
  if (!SECTIONS.includes(section) || !OPERATIONS.includes(action)) return false;
  if (isPrivileged(user)) return true;
  const grants = user.permissions?.[section];
  return section !== 'setup' && (section === 'dashboard' || Boolean(grants?.view)) && Boolean(grants?.[action]);
}

function access(section, action = 'view') {
  const current = context();
  if (!allowed(current.user, section, action)) throw new Error(`Your role does not have ${action} access in ${section}.`);
  return current;
}

function refreshedSession(before) {
  const current = context();
  if (current.token.id !== before.token.id || !sameCredential(before.user, current.user)) {
    throw new Error('Your sign-in changed. Try again from the current session.');
  }
  return current;
}

function getId(data, prefix) {
  const next = data.nextId || 100;
  data.nextId = next + 1;
  return `${prefix}-${next}`;
}

function auditLog(data, username, action, record, before = null, after = null) {
  data.audit.unshift({ id: getId(data, 'event'), time: now(), user: username, action, record, before, after, device: deviceLabel() });
}

function loginLog(data, username, status) {
  data.login.unshift({ id: getId(data, 'login'), time: now(), username, status, device: deviceLabel() });
}

function edit(current, callback) {
  const data = clone(current.entry.data);
  let book;
  const rebuild = () => (book ??= replay(data));
  const value = callback(data, rebuild);
  rebuild();
  if (!data.settings.negativeStock) enforceNonnegativeBalances(book);
  const currentRaw = readStorage('localStorage', `${STORAGE_PREFIX}:${current.token.clientId}`);
  if (currentRaw !== current.entry.raw) throw new Error('This workspace changed in another window. Refresh and try again.');
  put(current.token.clientId, data, book);
  return value;
}

function collection(kind) {
  if (!Object.hasOwn(masterPrefixes, kind)) throw new Error('Unknown master record type.');
  return ['customers', 'suppliers'].includes(kind) ? 'parties' : kind;
}

function draftCode(data, kind) {
  const prefix = masterPrefixes[kind];
  const rows = data.masters[collection(kind)];
  const numbers = rows.map(({ code }) => {
    if (!code.toUpperCase().startsWith(`${prefix}-`)) return 0;
    const sequence = code.slice(prefix.length + 1);
    return /^\d{1,12}$/.test(sequence) ? Number(sequence) : 0;
  });
  return `${prefix}-${String(numbers.reduce((max, value) => Math.max(max, value), 0) + 1).padStart(3, '0')}`;
}

function draftNumber(data, kind) {
  const definition = DOCUMENT_TYPES[kind];
  if (!definition) throw new Error('Select a document type.');
  const prefix = data.settings.numbering[kind] || definition.prefix;
  const taken = new Set(data.documents.map(({ number }) => lower(number)));
  let sequence = data.sequences[kind] || 0;
  let number;
  do {
    sequence += 1;
    number = `${prefix}-${String(sequence).padStart(5, '0')}`;
  } while (taken.has(lower(number)));
  return { sequence, number };
}

function masterUsed(data, kind, record) {
  const masters = data.masters;
  if (kind === 'categories') return masters.items.some((item) => item.category === record.id || item.category === record.code);
  if (kind === 'units') return masters.items.some((item) => item.unit === record.id || item.unit === record.code);
  if (kind === 'cities') return [...masters.parties, ...masters.warehouses].some((row) => lower(row.cityCode) === lower(record.code));
  if (kind === 'groups') return masters.parties.some((row) => row.groupId === record.id);
  if (kind === 'warehouses' && masters.items.some((row) => row.openingWarehouseId === record.id && Number(row.openingStock))) return true;
  if (Number(record.openingStock) || Number(record.openingBalance)) return true;
  const references = {
    items: ['itemId'], warehouses: ['warehouseId'], parties: ['partyId', 'accountId', 'debitId', 'creditId'],
    cash: ['cashId', 'accountId', 'debitId', 'creditId', 'toId', 'fromId'],
    banks: ['cashId', 'accountId', 'debitId', 'creditId', 'toId', 'fromId'], methods: ['methodId'], salesmen: ['salesmanId'],
  };
  return data.documents.some((doc) => (references[kind] || []).some((key) => doc[key] === record.id)
    || (kind === 'items' && doc.lines?.some((line) => line.itemId === record.id)));
}

function validateMaster(data, list, kind, values, previous) {
  const fields = ['code', 'name', 'urdu', 'active', ...(masterFields[list] || [])];
  const row = { ...previous, ...Object.fromEntries(fields.filter((field) => Object.hasOwn(values, field)).map((field) => [field, values[field]])) };
  row.id = previous?.id || getId(data, list);
  row.code = clean(row.code || draftCode(data, kind));
  row.name = clean(row.name);
  row.urdu = clean(row.urdu);
  row.active = row.active !== false;
  if (!row.name || row.name.length > 160) throw new Error('Enter a name with up to 160 characters.');
  if (row.code.length > 32) throw new Error('Use up to 32 characters for the code.');
  if (data.masters[list].some(({ id, code }) => id !== row.id && lower(code) === lower(row.code))) throw new Error(`Code ${row.code} is already in use.`);
  if (list === 'parties') {
    row.accountType = kind === 'customers' ? 'Customer' : kind === 'suppliers' ? 'Supplier' : (row.accountType || 'Customer');
    if (!accountTypes.includes(row.accountType)) throw new Error('Select a valid account type.');
    if (previous && previous.accountType !== row.accountType && masterUsed(data, list, previous)) {
      throw new Error('An account type cannot change after it has an opening balance or transaction.');
    }
    row.country = clean(row.country) || 'Pakistan';
    row.cityCode = clean(row.cityCode);
    if (row.cityCode && !data.masters.cities.some((city) => lower(city.code) === lower(row.cityCode))) throw new Error('Enter an existing city code.');
    row.creditLimit = numberValue(row.creditLimit, 'Credit limit');
    row.paymentTerms = numberValue(row.paymentTerms ?? data.settings.paymentTerms, 'Payment terms', { integer: true, max: 3650 });
  }
  if (accountsWithBalances.has(list)) {
    row.openingBalance = numberValue(row.openingBalance, 'Opening balance');
    row.openingType = lower(row.openingType) === 'credit' ? 'Credit' : 'Debit';
  }
  if (list === 'items') {
    row.purchasePrice = numberValue(row.purchasePrice, 'Purchase price');
    row.salePrice = numberValue(row.salePrice, 'Sale price');
    row.openingStock = numberValue(row.openingStock, 'Opening stock');
    row.minimumStock = numberValue(row.minimumStock, 'Minimum stock');
    row.openingRate = previous && Number(previous.openingStock) > 0 ? previous.openingRate ?? previous.purchasePrice : row.purchasePrice;
    row.openingWarehouseId = previous?.openingWarehouseId || data.masters.warehouses.find((warehouse) => warehouse.active)?.id;
    row.barcode = clean(row.barcode);
    if (row.barcode && data.masters.items.some(({ id, barcode }) => id !== row.id && barcode === row.barcode)) throw new Error('This barcode is already in use.');
  }
  return row;
}

function dateValue(value) {
  const date = clean(value);
  if (!/^\d{4}-\d{2}-\d{2}$/.test(date) || !Number.isFinite(Date.parse(date)) || new Date(date).toISOString().slice(0, 10) !== date) {
    throw new Error('Enter a valid document date.');
  }
  return date;
}

const financialAccess = (user) => allowed(user, 'accounting', 'view') || SECTIONS.some((section) => allowed(user, section, 'financial'));

function forUser(doc, user) {
  if (financialAccess(user)) return doc;
  const { entries, movements, lines, ...visible } = doc;
  return { ...visible, lines: lines?.map(({ costBasis, ...line }) => line) };
}

function newDocument(data, kind, values, previous, username) {
  const definition = DOCUMENT_TYPES[kind];
  const candidate = { ...previous, ...values };
  // Pick values at the service boundary so UI-only state is never persisted in postings.
  const keys = ['partyId', 'cashId', 'accountId', 'warehouseId', 'methodId', 'itemId', 'debitId', 'creditId', 'fromId', 'toId', 'salesmanId',
    'reference', 'narration', 'openingKind', 'openingType', 'amount', 'paid', 'quantity', 'rate', 'discount', 'terms'];
  const record = Object.fromEntries(keys.filter((key) => candidate[key] !== undefined).map((key) => [key, candidate[key]]));
  const allocation = previous ? null : draftNumber(data, kind);
  if (allocation) data.sequences[kind] = allocation.sequence;
  const next = data.documents.reduce((largest, row) => Math.max(largest, row.order || 0), 0) + 1;
  Object.assign(record, {
    id: previous?.id || getId(data, 'document'), kind, number: previous?.number || allocation.number,
    date: dateValue(candidate.date), order: previous?.order || next, status: 'posted',
    valuation: previous?.valuation || data.settings.stockValuation, precision: previous?.precision ?? data.settings.decimalPrecision,
    breakageTreatment: previous?.breakageTreatment || data.settings.breakageTreatment, allowNegative: data.settings.negativeStock,
    createdAt: previous?.createdAt || now(), createdBy: previous?.createdBy || username, updatedAt: now(), updatedBy: username,
  });
  record.reference = clean(record.reference);
  record.narration = clean(record.narration);
  if (record.terms === '') delete record.terms;
  if (definition.section === 'inventory') {
    record.warehouseId ||= data.masters.warehouses.find((row) => row.active)?.id;
    record.lines = (candidate.lines ?? []).map(({ itemId, quantity, rate, discount, tax }, index) => {
      const prior = previous?.lines?.[index];
      const costBasis = (prior?.itemId === itemId ? prior.costBasis : undefined) ?? data.masters.items.find((item) => item.id === itemId)?.purchasePrice;
      return { itemId, quantity, rate, discount, tax, costBasis };
    });
  }
  return record;
}

function validateReferences(data, doc, previous) {
  const cashBanks = [...data.masters.cash, ...data.masters.banks];
  const allAccounts = [...data.masters.parties, ...cashBanks];
  const requiredReferences = [
    ['partyId', data.masters.parties, 'party'], ['cashId', cashBanks, 'cash or bank account'], ['accountId', allAccounts, 'account'],
    ['warehouseId', data.masters.warehouses, 'warehouse'], ['methodId', data.masters.methods, 'payment method'],
    ['itemId', data.masters.items, 'item'], ['debitId', allAccounts, 'debit account'], ['creditId', allAccounts, 'credit account'],
    ['fromId', cashBanks, 'source account'], ['toId', cashBanks, 'destination account'], ['salesmanId', data.masters.salesmen, 'salesman'],
  ];
  for (const [key, rows, label] of requiredReferences) {
    if (doc[key] && !rows.some((row) => row.id === doc[key] && (row.active || previous?.[key] === row.id))) throw new Error(`Select an available ${label}.`);
  }
  if (doc.lines?.some(({ itemId }) => !data.masters.items.some((row) => row.id === itemId && (row.active || previous?.lines?.some((line) => line.itemId === itemId))))) {
    throw new Error('Select an available item for each line.');
  }
  const kind = lower(doc.openingKind);
  if (doc.kind === 'opening' && kind !== 'inventory') {
    const accountId = doc.accountId || doc.partyId || doc.cashId;
    const relevant = { cash: data.masters.cash, bank: data.masters.banks, party: data.masters.parties,
      customer: data.masters.parties.filter((row) => row.accountType === 'Customer'), supplier: data.masters.parties.filter((row) => row.accountType === 'Supplier') };
    if (!relevant[kind]?.some((row) => row.id === accountId)) throw new Error('Select an account matching the balance type.');
  }
  const referenceKinds = { receipt: ['sale', 'counter'], 'sale-return': ['sale', 'counter'], 'purchase-return': ['purchase'] }[doc.kind];
  if (referenceKinds && doc.reference) {
    const invoice = data.documents.find((row) => lower(row.number) === lower(doc.reference));
    if (invoice) {
      const earlier = invoice.date < doc.date || (invoice.date === doc.date && invoice.order < doc.order);
      if (!referenceKinds.includes(invoice.kind) || invoice.partyId !== doc.partyId || invoice.status !== 'posted' || !earlier) {
        throw new Error('Select a matching invoice posted for this party before this transaction.');
      }
      doc.referenceInvoiceId = invoice.id;
    }
  }
  if (['receipt', 'payment'].includes(doc.kind) && !doc.methodId) {
    throw new Error('Select a payment method.');
  }
}

function publicUser({ id, username, name, role, active, permissions }) {
  return { id, username, name, role, active, permissions };
}

function fiscalStart(year) {
  const match = clean(year).match(/^(\d{4})(?:\s*[-–/]\s*(\d{2}|\d{4}))?$/);
  if (!match) throw new Error('Enter a financial year such as 2026–2027.');
  const start = Number(match[1]);
  const end = match[2]?.length === 2 ? Number(match[1].slice(0, 2) + match[2]) : Number(match[2]);
  if (start < 1900 || start > 9900 || (match[2] && end !== start + 1)) throw new Error('Enter a one-year financial period.');
  return start;
}

function checkedSettings(previous, values) {
  const settings = { ...previous, ...values, numbering: { ...previous.numbering, ...values.numbering } };
  settings.currency = clean(settings.currency).toUpperCase();
  if (!/^[A-Z]{3}$/.test(settings.currency)) throw new Error('Enter a three-letter currency such as PKR.');
  settings.decimalPrecision = numberValue(settings.decimalPrecision, 'Decimal precision', { integer: true, max: 4 });
  settings.paymentTerms = numberValue(settings.paymentTerms, 'Payment terms', { integer: true, max: 3650 });
  settings.creditLimit = numberValue(settings.creditLimit, 'Credit limit');
  settings.sessionTimeout = numberValue(settings.sessionTimeout, 'Session timeout', { integer: true, max: 1440 });
  const choices = { salePriceDefault: ['item', 'manual'], purchasePriceDefault: ['item', 'manual'], stockValuation: ['weightedAverage', 'fifo'],
    breakageTreatment: ['separate', 'expense'], invoiceFormat: ['standard', 'compact'] };
  for (const [name, options] of Object.entries(choices)) {
    if (!options.includes(settings[name])) throw new Error('Select a valid business preference.');
  }
  for (const kind of Object.keys(DOCUMENT_TYPES)) {
    const prefix = clean(settings.numbering[kind]).toUpperCase();
    if (!/^[A-Z0-9][A-Z0-9-]{0,9}$/.test(prefix)) throw new Error('Use 1–10 letters, numbers or hyphens in each document prefix.');
    settings.numbering[kind] = prefix;
  }
  return settings;
}

function checkedBackup(snapshot, clientId) {
  if (snapshot?.version !== 1 || snapshot.clientId !== clientId || snapshot.data?.profile?.id !== clientId) {
    throw new Error('Select a demo backup created for this business.');
  }
  const incoming = clone(snapshot.data);
  const kinds = [...new Set(Object.keys(masterPrefixes).map(collection))];
  const hasRows = (rows, key) => Array.isArray(rows) && rows.every((row) => typeof row?.[key] === 'string');
  if (!incoming.settings || !incoming.sequences || !Number.isSafeInteger(incoming.nextId) || incoming.nextId < 1
    || !hasRows(incoming.documents, 'number') || !hasRows(incoming.users, 'username')
    || !kinds.every((kind) => hasRows(incoming.masters?.[kind], 'code')) || !Array.isArray(incoming.audit) || !Array.isArray(incoming.login)) {
    throw new Error('The backup is missing part of the demo workspace.');
  }
  for (const doc of incoming.documents) dateValue(doc.date);
  if (new Set(incoming.documents.map((doc) => lower(doc.number))).size !== incoming.documents.length) throw new Error('The backup contains duplicate document numbers.');
  fiscalStart(incoming.profile.financialYear);
  incoming.settings = checkedSettings(defaultSettings(), incoming.settings);
  return incoming;
}

function keepCurrentUsers(incoming, current) {
  const owner = superAdmin(current);
  if (incoming.superAdminId != null && incoming.superAdminId !== owner?.id) {
    throw new Error('Only the existing Super Admin can hold that role during a restore.');
  }
  const claimedOwners = incoming.users.filter((user) => user.role === SUPER_ADMIN_ROLE);
  const roles = ['Administrator', 'Clerk', SUPER_ADMIN_ROLE];
  const invalidOwner = claimedOwners.some((user) => user.id !== owner?.id || lower(user.username) !== lower(owner?.username));
  const ownerCollision = owner && incoming.users.some((user) => user.id === owner.id && user.role !== SUPER_ADMIN_ROLE);
  if (invalidOwner || ownerCollision || claimedOwners.length > 1 || incoming.users.some((user) => !roles.includes(user.role))) {
    throw new Error('The backup contains an invalid user role or Super Admin identity.');
  }
  incoming.users = clone(current.users);
  if (owner) incoming.superAdminId = owner.id;
  else delete incoming.superAdminId;
}

export const demo = {
  clients() {
    return CLIENTS.map((client) => {
      const raw = readStorage('localStorage', `${STORAGE_PREFIX}:${client.id}`);
      if (!raw) return { ...client };
      const { id, name, urdu, city } = store(client.id).data.profile;
      return { ...client, id, name, urdu, city };
    });
  },

  async signIn(clientId, username, password) {
    const attempt = ++signInAttempt;
    const sessionId = getToken()?.id;
    username = clean(username).slice(0, 80);
    const before = store(clientId).data.users.find((user) => lower(user.username) === lower(username));
    const verified = await matchesCredential(before, password);
    if (signInAttempt !== attempt || getToken()?.id !== sessionId) throw new Error('The sign-in changed. Try again.');

    const entry = store(clientId);
    const data = clone(entry.data);
    const identity = data.users.find((user) => lower(user.username) === lower(username));
    const success = verified && sameCredential(before, identity) && (identity.role !== SUPER_ADMIN_ROLE || superAdmin(data)?.id === identity.id);
    loginLog(data, username, success ? 'Success' : 'Failed');
    auditLog(data, username, success ? 'Signed in' : 'Failed login', data.profile.name);
    put(clientId, data, entry.book);
    if (!success) throw new Error('The username or password is incorrect for this business.');
    setToken(sessionFor(clientId, identity));
    return demo.session();
  },

  session() {
    const current = context(false);
    if (!current) return null;
    const { id, clientId, signedInAt } = current.token;
    const { name, username, role } = current.user;
    return { id, clientId, userId: current.user.id, name, username, role, signedInAt, device: deviceLabel(), ip: null };
  },

  touch() {
    const current = context(false);
    if (current && stamp() - current.token.activeAt > 1000) setToken({ ...current.token, activeAt: stamp() });
  },

  signOut() {
    signInAttempt += 1;
    const current = context(false);
    if (current) {
      const data = clone(current.entry.data);
      auditLog(data, current.user.username, 'Signed out', data.profile.name);
      put(current.token.clientId, data, current.entry.book);
    }
    setToken(null);
  },

  async verifyPassword(password) {
    const before = context(false);
    if (!before || !(await matchesCredential(before.user, password))) return false;
    const current = context(false);
    return current?.token.id === before.token.id && sameCredential(before.user, current.user);
  },

  hasSuperAdmin(clientId) {
    if (!TENANTS.has(clientId) || !readStorage('localStorage', `${STORAGE_PREFIX}:${clientId}`)) return false;
    return Boolean(superAdmin(store(clientId).data));
  },

  superAdminStatus() {
    const { data } = context().entry;
    const owner = superAdmin(data);
    return { configured: Boolean(owner), username: owner?.username || availableOwnerUsername(data.users) };
  },

  async activateSuperAdmin({ name, currentPassword, password }) {
    const before = access('setup');
    if (superAdmin(before.entry.data)) throw new Error('This business already has a Super Admin.');
    if (before.user.role !== 'Administrator') throw new Error('Sign in as an administrator to activate the Super Admin.');
    const ownerName = clean(name);
    if (!ownerName || ownerName.length > 160) throw new Error('Enter the Super Admin’s full name with up to 160 characters.');
    validateNewPassword(password, currentPassword);
    if (!(await matchesCredential(before.user, currentPassword))) throw new Error('The current administrator password is incorrect.');
    const credential = await createCredential(password);
    const current = refreshedSession(before);
    if (current.user.role !== 'Administrator') throw new Error('Sign in as an administrator to activate the Super Admin.');

    const owner = edit(current, (data) => {
      if (superAdmin(data)) throw new Error('This business already has a Super Admin.');
      const username = availableOwnerUsername(data.users);
      const account = { id: getId(data, 'user'), username, name: ownerName, role: SUPER_ADMIN_ROLE, active: true, permissions: {} };
      applyCredential(account, credential);
      data.users.push(account);
      data.superAdminId = account.id;
      auditLog(data, current.user.username, 'Activated Super Admin', username, null, publicUser(account));
      for (const user of data.users) {
        if (!['admin', 'clerk'].includes(user.id) || user.password !== 'demo123' || user.credential) continue;
        const previous = publicUser(user);
        user.active = false;
        delete user.password;
        auditLog(data, account.username, 'Retired demo login', user.username, previous, publicUser(user));
      }
      return account;
    });
    setToken(sessionFor(current.token.clientId, owner));
    return demo.session();
  },

  async changePassword(currentPassword, newPassword) {
    const before = context();
    validateNewPassword(newPassword, currentPassword);
    if (!(await matchesCredential(before.user, currentPassword))) throw new Error('The current password is incorrect.');
    const credential = await createCredential(newPassword);
    const current = refreshedSession(before);
    const updated = edit(current, (data) => {
      const user = data.users.find(({ id }) => id === current.user.id);
      applyCredential(user, credential);
      auditLog(data, user.username, 'Changed own password', user.username);
      return user;
    });
    setToken({ ...current.token, credentialVersion: updated.credentialVersion, activeAt: stamp() });
    return demo.session();
  },

  revision() {
    const current = context();
    return `${current.token.clientId}:${current.entry.data.nextId}`;
  },

  can(section, action = 'view') {
    const current = context(false);
    return Boolean(current && allowed(current.user, section, action));
  },

  profile() {
    return clone(context().entry.data.profile);
  },

  settings() {
    return clone(context().entry.data.settings);
  },

  list(kind) {
    const current = context();
    const { data } = current.entry;
    const list = collection(kind);
    const codingAccess = allowed(current.user, 'coding', 'view');
    const pickerAccess = pickerSections[list]?.some((section) => allowed(current.user, section, 'view'));
    if (!codingAccess && !pickerAccess) throw new Error('Your role does not have access to these records.');
    const book = list === 'items' || accountsWithBalances.has(list) ? bookFor(current.entry) : null;
    const balances = financialAccess(current.user);
    const rows = data.masters[list].filter((row) => kind === 'customers' ? row.accountType === 'Customer' : kind === 'suppliers' ? row.accountType === 'Supplier' : true);
    const cityNames = new Map(data.masters.cities.map((city) => [lower(city.code), city]));
    return clone(rows.map((row) => {
      const { id, code, name, urdu, active } = row;
      const summary = { id, code, name, urdu, active };
      if (list === 'items') {
        const info = codingAccess ? row : { ...summary, category: row.category, unit: row.unit, purchasePrice: row.purchasePrice, salePrice: row.salePrice };
        return { ...info, stock: stockSummary(book, row.id), damagedStock: stockSummary(book, row.id, null, true) };
      }
      if (accountsWithBalances.has(list)) {
        const city = cityNames.get(lower(row.cityCode));
        const accountType = list === 'cash' ? 'Cash' : list === 'banks' ? 'Bank' : row.accountType;
        const info = codingAccess ? { ...row, accountType, city: city?.name, cityUr: city?.urdu } : { ...summary, accountType, paymentTerms: row.paymentTerms };
        const permitted = balances ? { openingBalance: row.openingBalance, balance: accountBalance(book, row.id) } : { openingBalance: 0, balance: 0 };
        return { ...info, ...permitted };
      }
      return codingAccess ? row : summary;
    }));
  },

  nextCode(kind) {
    return draftCode(access('coding').entry.data, kind);
  },

  saveMaster(kind, record) {
    const current = access('coding', record.id ? 'edit' : 'add');
    const list = collection(kind);
    return clone(edit(current, (data) => {
      const index = data.masters[list].findIndex(({ id }) => id === record.id);
      if (record.id && index < 0) throw new Error('This record no longer exists.');
      const previous = data.masters[list][index];
      const values = { ...record };
      if (accountsWithBalances.has(list) && !financialAccess(current.user)) {
        if (Number(values.openingBalance)) throw new Error('Your role cannot change opening balances.');
        delete values.openingBalance;
        delete values.openingType;
      }
      const row = validateMaster(data, list, kind, values, previous);
      if (previous) data.masters[list][index] = row;
      else data.masters[list].push(row);
      if (previous && list === 'cities' && previous.code !== row.code) {
        for (const address of [...data.masters.parties, ...data.masters.warehouses]) {
          if (lower(address.cityCode) === lower(previous.code)) address.cityCode = row.code;
        }
      }
      auditLog(data, current.user.username, previous ? 'Edited master' : 'Created master', `${row.code} · ${row.name}`, previous || null, row);
      return accountsWithBalances.has(list) && !financialAccess(current.user) ? { ...row, openingBalance: 0 } : row;
    }));
  },

  removeMaster(kind, id) {
    const current = access('coding', 'delete');
    const list = collection(kind);
    edit(current, (data) => {
      const row = data.masters[list].find((record) => record.id === id);
      if (!row) throw new Error('This master record no longer exists.');
      if (masterUsed(data, list, row)) throw new Error('This record has history or is in use. Edit it and set it to inactive to preserve that history.');
      data.masters[list] = data.masters[list].filter((record) => record.id !== id);
      auditLog(data, current.user.username, 'Deleted master', `${row.code} · ${row.name}`, row);
    });
  },

  stockFor(itemId, warehouseId, damaged = false) {
    const current = context();
    if (!['inventory', 'coding', 'financial'].some((section) => allowed(current.user, section, 'view'))) throw new Error('Your role does not have access to stock records.');
    return stockSummary(bookFor(current.entry), itemId, warehouseId, damaged);
  },

  nextNumber(kind) {
    const definition = DOCUMENT_TYPES[kind];
    if (!definition) throw new Error('Select a document type.');
    return draftNumber(access(definition.section).entry.data, kind).number;
  },

  documents(kind) {
    const current = context();
    if (kind && !DOCUMENT_TYPES[kind]) throw new Error('Unknown document type.');
    const book = bookFor(current.entry);
    const rows = current.entry.data.documents.filter((doc) => (!kind || doc.kind === kind) && allowed(current.user, DOCUMENT_TYPES[doc.kind].section, 'view'));
    return clone(rows.sort((left, right) => right.date.localeCompare(left.date) || right.order - left.order).map((doc) => forUser(
      doc.status === 'cancelled' ? { ...(doc.cancelledDocument || doc), status: 'cancelled', cancelledAt: doc.cancelledAt, cancelledBy: doc.cancelledBy }
        : publicDocument(book.documentsById.get(doc.id)), current.user
    )));
  },

  saveDocument(kind, values) {
    if (!DOCUMENT_TYPES[kind]) throw new Error('Select a document type.');
    const current = access(DOCUMENT_TYPES[kind].section, values.id ? 'edit' : 'add');
    const oldBook = bookFor(current.entry);
    return clone(edit(current, (data, rebuild) => {
      const previous = values.id && data.documents.find((doc) => doc.id === values.id && doc.kind === kind);
      if (values.id && !previous) throw new Error('This document no longer exists.');
      if (previous && previous.status !== 'posted') throw new Error('A cancelled transaction cannot be edited.');
      const doc = newDocument(data, kind, values, previous, current.user.username);
      validateReferences(data, doc, previous);
      if (previous) data.documents = data.documents.map((row) => row.id === previous.id ? doc : row);
      else data.documents.push(doc);
      const book = rebuild();
      const result = publicDocument(book.documentsById.get(doc.id));
      if (['sale', 'counter'].includes(kind) && result.balance > 0) {
        const party = book.accounts.get(doc.partyId);
        const limit = party?.creditLimit || data.settings.creditLimit;
        const newBalance = accountBalance(book, doc.partyId);
        if (limit > 0 && newBalance > limit && newBalance > accountBalance(oldBook, doc.partyId)) throw new Error(`This sale would exceed the credit limit for ${party.name}.`);
      }
      auditLog(data, current.user.username, previous ? 'Edited document' : 'Posted document', doc.number,
        previous ? publicDocument(oldBook.documentsById.get(previous.id)) : null, result);
      return forUser(result, current.user);
    }));
  },

  cancelDocument(id) {
    const current = context();
    const original = current.entry.data.documents.find((doc) => doc.id === id);
    if (!original || original.status === 'cancelled') throw new Error('Select a posted document to cancel.');
    if (!allowed(current.user, DOCUMENT_TYPES[original.kind].section, 'delete')) throw new Error('Your role cannot cancel this document.');
    const prior = publicDocument(bookFor(current.entry).documentsById.get(id));
    return clone(edit(current, (data) => {
      const index = data.documents.findIndex((doc) => doc.id === id);
      data.documents[index] = { ...original, status: 'cancelled', cancelledAt: now(), cancelledBy: current.user.username, cancelledDocument: prior };
      const result = { ...prior, status: 'cancelled', cancelledAt: data.documents[index].cancelledAt, cancelledBy: current.user.username };
      auditLog(data, current.user.username, 'Cancelled document', original.number, prior, result);
      return forUser(result, current.user);
    }));
  },

  dashboard(range) {
    const current = context();
    const snapshot = dashboardTotals(current.entry.data, bookFor(current.entry), range);
    snapshot.activity = snapshot.activity.filter((doc) => allowed(current.user, DOCUMENT_TYPES[doc.kind].section, 'view')).map((doc) => forUser(doc, current.user));
    if (!allowed(current.user, 'inventory', 'view')) snapshot.lowStock = [];
    if (!financialAccess(current.user)) {
      const privateFields = ['sales', 'purchases', 'receivables', 'payables', 'cash', 'stockValue', 'monthProfit', 'yearProfit', 'outstanding', 'overdueCount', 'overdueAmount'];
      for (const field of privateFields) snapshot[field] = 0;
      snapshot.series = snapshot.series.map(({ label }) => ({ label, sales: 0, purchases: 0 }));
    }
    return clone(snapshot);
  },

  ledger(accountId) {
    const current = access('accounting');
    return clone(ledgerTotals(bookFor(current.entry), accountId));
  },

  trialBalance() {
    const current = access('accounting');
    return clone(trialTotals(bookFor(current.entry)));
  },

  profit(year) {
    const current = access('accounting');
    const first = fiscalStart(year || current.entry.data.profile.financialYear || fiscalYear());
    return clone(profitTotals(bookFor(current.entry), `${first}-07-01`, `${first + 1}-07-01`, `${first}–${first + 1}`));
  },

  overdue() {
    const current = access('accounting');
    return clone(allOutstanding(bookFor(current.entry)));
  },

  audit() {
    return clone(access('setup').entry.data.audit);
  },

  loginHistory() {
    return clone(access('setup').entry.data.login);
  },

  users() {
    return clone(access('setup').entry.data.users.map((user) => ({ ...publicUser(user), needsPassword: !user.credential && !user.password })));
  },

  async saveUser(values) {
    const before = access('setup');
    const { id, password } = values;
    const previous = before.entry.data.users.find((row) => row.id === id);
    if (id && !previous) throw new Error('This user no longer exists.');
    if (previous?.role === SUPER_ADMIN_ROLE || (id && id === before.entry.data.superAdminId)) {
      throw new Error('The Super Admin account is protected. Use Change password for its credentials.');
    }
    const roleValue = lower(values.role ?? previous?.role ?? 'Clerk');
    const role = { administrator: 'Administrator', admin: 'Administrator', clerk: 'Clerk' }[roleValue];
    if (!['Administrator', 'Clerk'].includes(role)) throw new Error('Select Administrator or Clerk for a managed user.');

    const username = clean(values.username ?? previous?.username);
    const name = clean(values.name ?? previous?.name);
    const active = (values.active ?? previous?.active) !== false;
    if (!/^[A-Za-z0-9][A-Za-z0-9._-]{0,49}$/.test(username)) throw new Error('Use 1–50 letters, numbers, periods, underscores or hyphens for the username.');
    if (!name || name.length > 160) throw new Error('Enter the user’s full name with up to 160 characters.');
    const grants = values.permissions ?? previous?.permissions ?? clerkPermissions();
    const permissions = role === 'Administrator' ? {} : Object.fromEntries(SECTIONS.map((section) => [section, Object.fromEntries(OPERATIONS.map((operation) => (
      [operation, Boolean(grants[section]?.[operation])]
    )))]));
    const changingPassword = password !== undefined && password !== '';
    if (changingPassword && id === before.user.id) throw new Error('Use Change password to update your own sign-in.');
    if (!previous || (active && !previous.credential && !previous.password) || changingPassword) validateNewPassword(password);
    const credential = changingPassword ? await createCredential(password) : null;

    const current = refreshedSession(before);
    if (!allowed(current.user, 'setup', 'view')) throw new Error('Administrator access is required to manage users.');
    return clone(edit(current, (data) => {
      const index = data.users.findIndex((row) => row.id === id);
      if (JSON.stringify(data.users[index]) !== JSON.stringify(previous)) throw new Error('This user changed while you were editing. Reopen the user and try again.');
      const record = { ...previous, id: previous?.id || getId(data, 'user'), username, name, role, active, permissions };
      if (data.users.some((row) => row.id !== record.id && lower(row.username) === lower(username))) throw new Error('This username is already in use.');
      const accessChanged = previous && (previous.username !== username || previous.role !== role || previous.active !== active
        || (role === 'Clerk' && JSON.stringify(previous.permissions) !== JSON.stringify(permissions)));
      if (credential) applyCredential(record, credential);
      else if (accessChanged) record.credentialVersion = globalThis.crypto.randomUUID();
      if (previous) data.users[index] = record;
      else data.users.push(record);
      if (!data.users.some((user) => isPrivileged(user) && user.active)) throw new Error('Keep at least one active administrator.');
      auditLog(data, current.user.username, previous ? 'Updated user' : 'Created user', record.username, previous ? publicUser(previous) : null, publicUser(record));
      return publicUser(record);
    }));
  },

  saveProfile(values) {
    const current = access('setup');
    return clone(edit(current, (data) => {
      const previous = data.profile;
      const profile = { ...previous };
      for (const key of ['name', 'urdu', 'owner', 'address', 'phone', 'country', 'city', 'logo', 'financialYear']) {
        if (values[key] !== undefined) profile[key] = clean(values[key]);
      }
      if (!profile.name) throw new Error('Enter a business name.');
      const first = fiscalStart(profile.financialYear);
      profile.financialYear = `${first}–${first + 1}`;
      data.profile = profile;
      auditLog(data, current.user.username, 'Updated business profile', profile.name, { ...previous, logo: Boolean(previous.logo) }, { ...profile, logo: Boolean(profile.logo) });
      return profile;
    }));
  },

  saveSettings(values) {
    const current = access('setup');
    return clone(edit(current, (data) => {
      const previous = data.settings;
      data.settings = checkedSettings(previous, values);
      auditLog(data, current.user.username, 'Updated business settings', data.profile.name, previous, data.settings);
      return data.settings;
    }));
  },

  snapshot() {
    const current = access('setup');
    const data = clone(current.entry.data);
    data.users = data.users.map(publicUser);
    return { version: 1, clientId: current.token.clientId, data };
  },

  restore(snapshot) {
    const current = access('setup');
    const data = checkedBackup(snapshot, current.token.clientId);
    keepCurrentUsers(data, current.entry.data);
    data.nextId = Math.max(data.nextId, current.entry.data.nextId);
    const book = replay(data);
    if (!data.settings.negativeStock) enforceNonnegativeBalances(book);
    auditLog(data, current.user.username, 'Restored workspace backup', data.profile.name);
    put(current.token.clientId, data, book);
    return demo.profile();
  },
};
