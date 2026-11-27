import { DOCUMENT_TYPES, addDays, monthDate, today } from './demo-seed.js';

const SCALE = 10000;
const MAX_AMOUNT = 10000000000;
const toMinor = (amount) => Math.round(amount * SCALE);
const toMajor = (amount) => amount / SCALE;
const fixed = (amount, precision) => Number(amount.toFixed(precision));
const norm = (value) => String(value ?? '').trim().toLowerCase();

const systemAccounts = [
  ['@inventory', 'G-1200', 'Inventory', 'کارآمد اسٹاک', 'Inventory', 'Asset'],
  ['@damaged', 'G-1210', 'Damaged Inventory', 'خراب اسٹاک', 'Inventory', 'Asset'],
  ['@purchase-tax', 'G-1250', 'Tax on Purchases', 'خریداری پر ٹیکس', 'Tax', 'Asset'],
  ['@walk-in', 'G-1290', 'Walk-in Sales Clearing', 'کاؤنٹر سیل', 'Receivables', 'Asset'],
  ['@sales-tax', 'G-2300', 'Tax on Sales', 'فروخت پر ٹیکس', 'Tax', 'Liability'],
  ['@opening', 'G-3000', 'Opening Balance Equity', 'ابتدائی بیلنس', 'Equity', 'Equity'],
  ['@sales', 'G-4000', 'Sales', 'فروخت', 'Sales', 'Sale'],
  ['@sales-return', 'G-4010', 'Sales Returns', 'واپسی فروخت', 'Sales', 'Sale Return'],
  ['@breakage-recovery', 'G-4100', 'Breakage Recovery', 'ٹوٹ پھوٹ کی وصولی', 'Other Income', 'Income'],
  ['@cogs', 'G-5000', 'Cost of Goods Sold', 'فروخت شدہ مال کی لاگت', 'Cost of Goods Sold', 'COGS'],
  ['@stock-variance', 'G-5010', 'Stock Cost Adjustment', 'اسٹاک لاگت کی ایڈجسٹمنٹ', 'Cost of Goods Sold', 'COGS'],
  ['@breakage-expense', 'G-5110', 'Breakage Expense', 'ٹوٹ پھوٹ کا خرچ', 'Operating Expenses', 'Expense'],
].map(([id, code, name, urdu, group, accountType]) => ({ id, code, name, urdu, group, accountType }));

export function numberValue(value, title, { min = 0, max = MAX_AMOUNT, integer = false } = {}) {
  const number = Number(value ?? 0);
  if (!Number.isFinite(number) || number < min || number > max || (integer && !Number.isInteger(number))) {
    const range = min > 0 ? `greater than zero and no more than ${max.toLocaleString('en-PK')}` : `between 0 and ${max.toLocaleString('en-PK')}`;
    throw new Error(`${title} must be ${integer ? 'a whole number ' : ''}${range}.`);
  }
  return number;
}

export function calculateDocumentTotals(data, precision = 2) {
  const factor = 10 ** precision;
  const round = (amount) => Math.round((amount + Number.EPSILON) * factor) / factor;
  let subtotal = 0;
  let lineDiscount = 0;
  const netLines = (data.lines ?? []).map((line, index) => {
    const prefix = `Line ${index + 1}`;
    const quantity = fixed(numberValue(line.quantity, `${prefix} quantity`), 4);
    const rate = fixed(numberValue(line.rate, `${prefix} rate`), 4);
    const gross = round(numberValue(quantity * rate, `${prefix} total`));
    const discount = round(numberValue(line.discount, `${prefix} discount`));
    if (discount > gross) throw new Error(`${prefix} discount exceeds the line amount.`);
    const net = round(gross - discount);
    subtotal += gross;
    lineDiscount += discount;
    const tax = numberValue(line.tax, `${prefix} tax percentage`, { max: 100 });
    return { ...line, quantity, rate, discount, tax, gross, net };
  });
  const discount = round(numberValue(data.discount, 'Invoice discount'));
  const net = round(subtotal - lineDiscount);
  if (discount > net) throw new Error('Invoice discount exceeds the discounted subtotal.');
  let remainingBase = net;
  let remainingDiscount = discount;
  let tax = 0;
  const lines = netLines.map((line) => {
    const allocatedDiscount = remainingBase ? round(remainingDiscount * line.net / remainingBase) : 0;
    remainingDiscount = round(remainingDiscount - allocatedDiscount);
    remainingBase = round(remainingBase - line.net);
    const netAfterDiscount = round(line.net - allocatedDiscount);
    const taxAmount = round(netAfterDiscount * line.tax / 100);
    tax += taxAmount;
    return { ...line, allocatedDiscount, netAfterDiscount, taxAmount, total: round(line.net + taxAmount) };
  });
  const total = round(net - discount + tax);
  return { lines, subtotal: round(subtotal), lineDiscount: round(lineDiscount), tax: round(tax), discount, total: numberValue(total, 'Grand total') };
}

function accountCatalog(state) {
  const groups = new Map(state.masters.groups.map((record) => [record.id, record.name]));
  const parties = state.masters.parties.map((account) => ({ ...account, group: groups.get(account.groupId) || account.accountType || 'General Accounts' }));
  const cash = state.masters.cash.map((account) => ({ ...account, group: 'Cash Accounts', accountType: 'Cash' }));
  const banks = state.masters.banks.map((account) => ({ ...account, group: 'Bank Accounts', accountType: 'Bank' }));
  return new Map([...systemAccounts, ...parties, ...cash, ...banks].map((account) => [account.id, account]));
}

function stockKey(itemId, warehouseId, damaged) {
  return `${itemId}|${warehouseId}|${damaged ? 'damaged' : 'good'}`;
}

function sumLots(lots) {
  return lots.reduce((total, lot) => ({ quantity: total.quantity + lot.quantity, value: total.value + lot.value }), { quantity: 0, value: 0 });
}

function receiveStock(lots, quantity, cost) {
  let remaining = quantity;
  let remainingCost = cost;
  let value = 0;
  for (const lot of lots) {
    if (lot.quantity >= 0 || remaining <= 0) continue;
    const used = Math.min(-lot.quantity, remaining);
    const recoveredValue = Math.round(lot.value * used / lot.quantity);
    const purchasedValue = Math.round(remainingCost * used / remaining);
    lot.quantity += used;
    lot.value += recoveredValue;
    remaining -= used;
    remainingCost -= purchasedValue;
    value += recoveredValue;
  }
  if (remaining > 0) lots.push({ quantity: remaining, value: remainingCost });
  return value + remainingCost;
}

function issueStock(lots, quantity, fallbackRate, valuation, allowNegative, label) {
  const available = sumLots(lots);
  if (!allowNegative && quantity > available.quantity) {
    throw new Error(`${label} has only ${Math.max(0, toMajor(available.quantity))} in the selected warehouse.`);
  }
  if (valuation === 'weightedAverage' && available.quantity > 0) {
    lots.splice(0, lots.length, available);
  }
  let remaining = quantity;
  let cost = 0;
  for (const lot of lots) {
    if (remaining <= 0 || lot.quantity <= 0) continue;
    const used = Math.min(lot.quantity, remaining);
    const lineCost = Math.round(lot.value * used / lot.quantity);
    lot.value -= lineCost;
    lot.quantity -= used;
    remaining -= used;
    cost += lineCost;
  }
  if (remaining > 0) {
    const estimate = Math.round(remaining * fallbackRate);
    lots.push({ quantity: -remaining, value: -estimate });
    cost += estimate;
  }
  return cost;
}

function referencedCost(previous, itemId, quantity) {
  const movements = previous?.movements.filter((movement) => movement.itemId === itemId && movement.quantity < 0) ?? [];
  const issued = movements.reduce((sum, movement) => sum - movement.quantity, 0);
  const value = movements.reduce((sum, movement) => sum - movement.value, 0);
  return issued > 0 ? Math.round(value * quantity / issued) : null;
}

export function replay(state) {
  const accounts = accountCatalog(state);
  const items = new Map(state.masters.items.map((item) => [item.id, item]));
  const warehouses = new Map(state.masters.warehouses.map((warehouse) => [warehouse.id, warehouse]));
  const defaultWarehouse = state.masters.warehouses[0]?.id;
  const balances = new Map();
  const openings = new Map();
  const stock = new Map();
  const posted = [];
  const documentsById = new Map();
  const documentsByNumber = new Map();
  const returned = new Map();
  const partyDocuments = new Map();
  const ledgerEntries = new Map();

  function lotsFor(itemId, warehouseId, damaged = false) {
    const key = stockKey(itemId, warehouseId, damaged);
    if (!stock.has(key)) stock.set(key, { itemId, warehouseId, damaged, lots: [] });
    return stock.get(key).lots;
  }

  function post(doc, rows) {
    const totals = new Map();
    for (const [id, value] of rows) {
      if (value === 0) continue;
      if (!accounts.has(id) || !Number.isSafeInteger(value)) throw new Error('An accounting entry is invalid.');
      if (!totals.has(id)) totals.set(id, { accountId: id, debit: 0, credit: 0 });
      const entry = totals.get(id);
      if (value > 0) entry.debit += value;
      else entry.credit -= value;
    }
    const entries = [...totals.values()];
    if (entries.reduce((sum, row) => sum + row.debit - row.credit, 0) !== 0) {
      throw new Error(`${doc.number} has unbalanced debit and credit.`);
    }
    for (const entry of entries) {
      const movement = entry.debit - entry.credit;
      balances.set(entry.accountId, (balances.get(entry.accountId) || 0) + movement);
      if (doc.kind === 'opening') openings.set(entry.accountId, (openings.get(entry.accountId) || 0) + movement);
      if (!ledgerEntries.has(entry.accountId)) ledgerEntries.set(entry.accountId, []);
      ledgerEntries.get(entry.accountId).push({ doc, ...entry });
    }
    return entries;
  }

  const baseline = { id: 'sample-opening', number: 'OPENING', kind: 'opening', date: '', narration: 'Opening balance' };
  const initial = [];
  for (const account of accounts.values()) {
    const amount = toMinor(account.openingBalance || 0) * (norm(account.openingType) === 'credit' ? -1 : 1);
    initial.push([account.id, amount], ['@opening', -amount]);
  }
  for (const item of items.values()) {
    const quantity = toMinor(numberValue(item.openingStock, `${item.name} opening stock`));
    if (!quantity) continue;
    const cost = Math.round(quantity * numberValue(item.openingRate ?? item.purchasePrice, `${item.name} opening rate`));
    const warehouseId = item.openingWarehouseId || defaultWarehouse;
    if (!warehouses.has(warehouseId)) throw new Error(`The opening warehouse for ${item.name} is missing.`);
    receiveStock(lotsFor(item.id, warehouseId), quantity, cost);
    initial.push(['@inventory', cost], ['@opening', -cost]);
  }
  post(baseline, initial);

  const byDate = [...state.documents].sort((left, right) => left.date.localeCompare(right.date) || left.order - right.order);
  for (const data of byDate) {
    if (data.status === 'cancelled') continue;
    const definition = DOCUMENT_TYPES[data.kind];
    if (!definition) throw new Error(`Unknown document type in ${data.number}.`);
    const doc = { ...data, title: definition.name, balance: 0, movements: [], total: 0, paid: 0 };
    const entries = [];
    const party = accounts.get(doc.partyId);
    const group = party?.accountType;
    const partyAccount = doc.partyId || '@walk-in';
    const cash = accounts.get(doc.cashId);
    const precision = doc.precision ?? state.settings.decimalPrecision;
    const amount = toMinor(fixed(numberValue(doc.amount, 'Amount'), precision));
    const paid = toMinor(fixed(numberValue(data.paid, 'Paid amount'), precision));
    const stockDocument = definition.section === 'inventory';
    const damaged = ['breakage-receive', 'breakage-dispatch'].includes(doc.kind);
    const warehouse = warehouses.get(doc.warehouseId || defaultWarehouse);
    const requireCash = (account) => {
      if (!['Cash', 'Bank'].includes(account?.accountType)) throw new Error('Select a cash or bank account.');
    };
    const requireParty = () => {
      if (!party) throw new Error('Select a party account.');
    };
    const requireAmount = () => {
      if (amount <= 0) throw new Error('Enter an amount greater than zero.');
    };

    function adjustStock(line, signedQuantity, lineValue = 0) {
      const item = items.get(line.itemId);
      if (!item) throw new Error('Select an item for each line.');
      if (!warehouse) throw new Error('Select a warehouse.');
      if (!signedQuantity) throw new Error(`Enter a quantity greater than zero for ${item.name}.`);
      const lots = lotsFor(item.id, warehouse.id, damaged);
      const value = signedQuantity > 0
        ? receiveStock(lots, signedQuantity, lineValue)
        : -issueStock(lots, -signedQuantity, line.costBasis ?? item.purchasePrice, doc.valuation, doc.allowNegative, item.name);
      doc.movements.push({ itemId: item.id, warehouseId: warehouse.id, damaged, quantity: signedQuantity, value });
      return value;
    }

    if (stockDocument) {
      if (!Array.isArray(doc.lines) || !doc.lines.length) throw new Error('Add at least one item.');
      const totals = calculateDocumentTotals(doc, precision);
      const total = toMinor(totals.total);
      const tax = toMinor(totals.tax);
      const net = total - tax;
      if (paid > total) throw new Error('Paid amount exceeds the document total.');
      if (paid > 0) requireCash(cash);
      if (['purchase', 'purchase-return'].includes(doc.kind) && group !== 'Supplier') throw new Error('Select a supplier account.');
      if (doc.kind === 'sale' && group !== 'Customer') throw new Error('Select a customer account.');
      if (['counter', 'sale-return'].includes(doc.kind) && total > paid && group !== 'Customer') throw new Error('Select a customer account for the outstanding balance.');
      if (damaged && paid > 0) throw new Error('Record cash separately for this breakage movement.');
      const original = doc.reference && documentsByNumber.get(norm(doc.reference));
      if (['sale-return', 'purchase-return'].includes(doc.kind) && doc.referenceInvoiceId) {
        if (!original || original.id !== doc.referenceInvoiceId || original.partyId !== doc.partyId) {
          throw new Error(`${doc.number} requires its linked invoice to remain posted for the same party.`);
        }
        const remaining = new Map();
        for (const line of original.lines) remaining.set(line.itemId, (remaining.get(line.itemId) || 0) + toMinor(line.quantity));
        for (const line of totals.lines) {
          const key = `${original.id}|${line.itemId}`;
          const quantity = (returned.get(key) || 0) + toMinor(line.quantity);
          if (quantity > (remaining.get(line.itemId) || 0)) throw new Error(`The return quantity for ${items.get(line.itemId)?.name || 'this item'} exceeds the linked invoice.`);
          returned.set(key, quantity);
        }
      }
      let stockValue = 0;
      for (const line of totals.lines) {
        const quantity = toMinor(line.quantity);
        if (['purchase', 'breakage-receive'].includes(doc.kind)) {
          stockValue += adjustStock(line, quantity, toMinor(line.netAfterDiscount));
        } else if (doc.kind === 'sale-return') {
          const matched = ['sale', 'counter'].includes(original?.kind) && original.partyId === doc.partyId ? original : null;
          const cost = referencedCost(matched, line.itemId, quantity) ?? Math.round(quantity * (line.costBasis ?? items.get(line.itemId)?.purchasePrice ?? 0));
          stockValue += adjustStock(line, quantity, cost);
        } else {
          stockValue += adjustStock(line, -quantity);
        }
      }
      Object.assign(doc, totals, { total: toMajor(total), paid: toMajor(paid), balance: damaged ? 0 : toMajor(total - paid) });
      if (doc.kind === 'purchase') {
        entries.push(['@inventory', stockValue], ['@stock-variance', net - stockValue], ['@purchase-tax', tax], [partyAccount, -total]);
        if (paid) entries.push([partyAccount, paid], [doc.cashId, -paid]);
      } else if (doc.kind === 'purchase-return') {
        entries.push([partyAccount, total], ['@inventory', stockValue], ['@stock-variance', -net - stockValue], ['@purchase-tax', -tax]);
        if (paid) entries.push([partyAccount, -paid], [doc.cashId, paid]);
      } else if (['sale', 'counter'].includes(doc.kind)) {
        entries.push([partyAccount, total], ['@sales', -net], ['@sales-tax', -tax], ['@cogs', -stockValue], ['@inventory', stockValue]);
        if (paid) entries.push([doc.cashId, paid], [partyAccount, -paid]);
      } else if (doc.kind === 'sale-return') {
        entries.push(['@sales-return', net], ['@sales-tax', tax], [partyAccount, -total], ['@inventory', stockValue], ['@cogs', -stockValue]);
        if (paid) entries.push([partyAccount, paid], [doc.cashId, -paid]);
      } else {
        const spread = doc.kind === 'breakage-receive' && doc.breakageTreatment !== 'expense' ? '@breakage-recovery' : '@breakage-expense';
        entries.push(['@damaged', stockValue], [spread, -stockValue]);
      }
    } else if (['receipt', 'payment'].includes(doc.kind)) {
      requireAmount();
      requireParty();
      requireCash(cash);
      const incoming = doc.kind === 'receipt' ? amount : -amount;
      entries.push([doc.cashId, incoming], [doc.partyId, -incoming]);
      doc.total = toMajor(amount);
    } else if (doc.kind === 'journal' || doc.kind === 'transfer') {
      requireAmount();
      const debit = doc.kind === 'transfer' ? doc.toId : doc.debitId;
      const credit = doc.kind === 'transfer' ? doc.fromId : doc.creditId;
      if (!debit || !credit || debit === credit) throw new Error('Select two different accounts.');
      if (doc.kind === 'transfer') {
        requireCash(accounts.get(debit));
        requireCash(accounts.get(credit));
      }
      entries.push([debit, amount], [credit, -amount]);
      doc.total = toMajor(amount);
    } else if (doc.kind === 'opening') {
      if (norm(doc.openingKind) === 'inventory') {
        const quantity = toMinor(numberValue(doc.quantity, 'Opening quantity', { min: 0.0001 }));
        const rate = numberValue(doc.rate, 'Opening rate');
        const cost = adjustStock({ itemId: doc.itemId }, quantity, Math.round(quantity * rate));
        entries.push(['@inventory', cost], ['@opening', -cost]);
        doc.total = toMajor(cost);
      } else {
        requireAmount();
        const accountId = doc.accountId || doc.partyId || doc.cashId;
        const posting = norm(doc.openingType) === 'credit' ? -amount : amount;
        entries.push([accountId, posting], ['@opening', -posting]);
        doc.total = toMajor(amount);
      }
    }

    const displayAccount = party || accounts.get(doc.accountId || doc.debitId || doc.toId) || (doc.kind === 'counter' ? null : cash);
    doc.party = displayAccount?.name || (doc.kind === 'counter' ? 'Walk-in Customer' : (doc.kind === 'opening' ? items.get(doc.itemId)?.name : '')) || '—';
    doc.partyUr = displayAccount?.urdu || (doc.kind === 'counter' ? 'کاؤنٹر خریدار' : '') || '';
    if (doc.kind === 'sale' || doc.kind === 'counter') {
      doc.terms = numberValue(data.terms ?? party?.paymentTerms ?? state.settings.paymentTerms, 'Payment terms', { max: 3650, integer: true });
      doc.dueDate = addDays(doc.date, doc.terms);
    }
    doc.entries = post(doc, entries);
    posted.push(doc);
    documentsById.set(doc.id, doc);
    documentsByNumber.set(norm(doc.number), doc);
    if (doc.partyId) {
      if (!partyDocuments.has(doc.partyId)) partyDocuments.set(doc.partyId, []);
      partyDocuments.get(doc.partyId).push(doc);
    }
  }
  const quantities = new Map();
  let stockValue = 0;
  for (const bucket of stock.values()) {
    const { quantity, value } = sumLots(bucket.lots);
    bucket.quantity = quantity;
    stockValue += value;
    const key = `${bucket.itemId}|${bucket.damaged}`;
    quantities.set(key, (quantities.get(key) || 0) + quantity);
  }
  return { accounts, balances, openings, stock, quantities, stockValue, posted, documentsById, partyDocuments, ledgerEntries };
}

export function stockSummary(book, itemId, warehouseId, damaged = false) {
  const quantity = warehouseId ? book.stock.get(stockKey(itemId, warehouseId, damaged))?.quantity : book.quantities.get(`${itemId}|${damaged}`);
  return toMajor(quantity || 0);
}

export function enforceNonnegativeBalances(book) {
  for (const bucket of book.stock.values()) {
    if (bucket.quantity < 0) throw new Error('This change would leave negative warehouse stock. Add stock or enable negative stock in Setup.');
  }
}

export function accountBalance(book, accountId) {
  return toMajor(book.balances.get(accountId) || 0);
}

export function publicDocument(doc) {
  return {
    ...doc,
    movements: doc.movements?.map((row) => ({ ...row, quantity: toMajor(row.quantity), value: toMajor(row.value) })),
    entries: doc.entries?.map((row) => ({ ...row, debit: toMajor(row.debit), credit: toMajor(row.credit) })),
  };
}

export function trialTotals(book) {
  const groupTotals = new Map();
  let debit = 0;
  let credit = 0;
  const rows = [...book.accounts.values()].map((account) => {
    const balance = book.balances.get(account.id) || 0;
    const dr = Math.max(0, balance);
    const cr = Math.max(0, -balance);
    const group = groupTotals.get(account.group) || { debit: 0, credit: 0 };
    group.debit += dr;
    group.credit += cr;
    groupTotals.set(account.group, group);
    debit += dr;
    credit += cr;
    return { id: account.id, code: account.code, name: account.name, urdu: account.urdu, group: account.group, accountType: account.accountType, debit: toMajor(dr), credit: toMajor(cr) };
  });
  const groups = [...groupTotals].map(([name, sum]) => ({ name, debit: toMajor(sum.debit), credit: toMajor(sum.credit) }));
  return { rows, groups, debit: toMajor(debit), credit: toMajor(credit) };
}

export function profitTotals(book, start, end, year) {
  const profit = { sales: 0, salesReturns: 0, costOfGoodsSold: 0, operatingExpenses: 0, otherIncome: 0 };
  const incomeTypes = {
    Sale: ['sales', -1], 'Sale Return': ['salesReturns', 1], COGS: ['costOfGoodsSold', 1], Expense: ['operatingExpenses', 1], Income: ['otherIncome', -1],
  };
  for (const doc of book.posted) {
    if (doc.date < start || doc.date >= end || doc.kind === 'opening') continue;
    for (const entry of doc.entries) {
      const type = book.accounts.get(entry.accountId)?.accountType;
      const mapping = incomeTypes[type];
      if (mapping) profit[mapping[0]] += (entry.debit - entry.credit) * mapping[1];
    }
  }
  profit.netSales = profit.sales - profit.salesReturns;
  profit.grossProfit = profit.netSales - profit.costOfGoodsSold;
  profit.netProfit = profit.grossProfit - profit.operatingExpenses + profit.otherIncome;
  return { year, ...Object.fromEntries(Object.entries(profit).map(([name, value]) => [name, toMajor(value)])) };
}

function allocate(invoices, amount, reference, field, invoiceId) {
  const referenceInvoice = reference && invoices.find((invoice) => invoiceId ? invoice.id === invoiceId : norm(invoice.number) === norm(reference));
  if (invoiceId && !referenceInvoice) return 0;
  const eligible = referenceInvoice ? [referenceInvoice] : invoices;
  for (const invoice of eligible) {
    const portion = Math.min(amount, invoice.total - invoice.paid - invoice.returned);
    invoice[field] += portion;
    amount -= portion;
    if (amount <= 0) break;
  }
  return referenceInvoice ? 0 : amount;
}

export function partyInvoices(book, accountId, asOf = today()) {
  const invoices = [];
  let cashCredit = 0;
  let returnCredit = 0;
  for (const doc of book.partyDocuments.get(accountId) || []) {
    if (doc.date > asOf) continue;
    if (doc.kind === 'sale' || doc.kind === 'counter') {
      const invoice = {
        id: doc.id, number: doc.number, date: doc.date, dueDate: doc.dueDate, terms: doc.terms,
        partyId: doc.partyId, party: doc.party, partyUr: doc.partyUr,
        total: toMinor(doc.total), paid: toMinor(doc.paid), returned: 0,
      };
      invoices.push(invoice);
      cashCredit = allocate([invoice], cashCredit, '', 'paid');
      returnCredit = allocate([invoice], returnCredit, '', 'returned');
    } else if (doc.kind === 'receipt') {
      cashCredit += allocate(invoices, toMinor(doc.total), doc.reference, 'paid', doc.referenceInvoiceId);
    } else if (doc.kind === 'sale-return') {
      returnCredit += allocate(invoices, toMinor(doc.balance), doc.reference, 'returned', doc.referenceInvoiceId);
    }
  }
  return invoices.map((invoice) => {
    const outstanding = invoice.total - invoice.paid - invoice.returned;
    const distance = Math.round((Date.parse(asOf) - Date.parse(invoice.dueDate)) / 86400000);
    const daysOverdue = outstanding > 0 ? Math.max(0, distance) : 0;
    const status = outstanding <= 0 ? 'paid' : distance > 0 ? 'overdue' : distance > -8 ? 'due-soon' : 'current';
    return { ...invoice, total: toMajor(invoice.total), paid: toMajor(invoice.paid), returned: toMajor(invoice.returned), outstanding: toMajor(outstanding), daysOverdue, status };
  });
}

export function allOutstanding(book) {
  return [...book.partyDocuments.keys()].flatMap((id) => partyInvoices(book, id)).filter((invoice) => invoice.outstanding > 0)
    .sort((left, right) => left.dueDate.localeCompare(right.dueDate) || left.party.localeCompare(right.party));
}

export function ledgerTotals(book, accountId) {
  const account = book.accounts.get(accountId);
  if (!account) throw new Error('Select an account to view its ledger.');
  const openingBalance = book.openings.get(accountId) || 0;
  let running = openingBalance;
  let received = 0;
  const rows = (book.ledgerEntries.get(accountId) || []).filter(({ doc }) => doc.kind !== 'opening').map(({ doc, debit, credit }) => {
    running += debit - credit;
    const payment = doc.kind === 'receipt' && doc.partyId === accountId ? credit : (doc.kind === 'sale' || doc.kind === 'counter') && doc.partyId === accountId ? toMinor(doc.paid) : 0;
    received += payment;
    return { id: doc.id, number: doc.number, date: doc.date, kind: doc.kind, narration: doc.narration || doc.title, debit: toMajor(debit), credit: toMajor(credit), balance: toMajor(running), paymentReceived: toMajor(payment) };
  });
  const invoices = partyInvoices(book, accountId);
  return { account, openingBalance: toMajor(openingBalance), balance: toMajor(running), received: toMajor(received), outstanding: toMajor(invoices.reduce((sum, invoice) => sum + toMinor(invoice.outstanding), 0)), rows, invoices };
}

export function dashboardTotals(state, book, requestedRange) {
  const date = today();
  const month = monthDate(date, 0);
  const nextMonth = monthDate(date, 1);
  const yearStart = Number(state.profile.financialYear.slice(0, 4));
  const series = [];
  const formatter = new Intl.DateTimeFormat('en-PK', { month: 'short', timeZone: 'UTC' });
  for (let offset = -6; offset <= 0; offset += 1) {
    const start = monthDate(date, offset);
    const end = monthDate(date, offset + 1);
    const totals = periodSalesPurchases(book, start, end);
    series.push({ label: formatter.format(new Date(start)), ...totals });
  }
  const period = typeof requestedRange === 'string' ? requestedRange.toLowerCase() : '';
  const quarter = ['quarter', 'last 3 months', '90 days'].includes(period);
  const yearly = ['year', 'financial year', 'this year'].includes(period);
  const start = yearly ? `${yearStart}-07-01` : quarter ? monthDate(date, -2) : month;
  const end = yearly ? `${yearStart + 1}-07-01` : nextMonth;
  const { sales, purchases } = periodSalesPurchases(book, start, end);
  const invoices = allOutstanding(book);
  const overdue = invoices.filter((invoice) => invoice.status === 'overdue');
  const invoiceTotal = (rows) => toMajor(rows.reduce((sum, row) => sum + toMinor(row.outstanding), 0));
  const accountTypeSum = (type, sign, positiveOnly = false) => toMajor([...book.accounts.values()].filter((account) => account.accountType === type)
    .reduce((sum, account) => {
      const net = sign * (book.balances.get(account.id) || 0);
      return sum + (positiveOnly ? Math.max(0, net) : net);
    }, 0));
  const lowStock = state.masters.items.filter((item) => item.active).map((item) => ({ ...item, stock: stockSummary(book, item.id) }))
    .filter((item) => item.stock <= item.minimumStock).sort((left, right) => left.stock - right.stock);
  return {
    sales, purchases, cash: accountTypeSum('Cash', 1), receivables: accountTypeSum('Customer', 1, true), payables: accountTypeSum('Supplier', -1, true),
    stockValue: toMajor(book.stockValue), monthProfit: profitTotals(book, month, nextMonth).netProfit,
    yearProfit: profitTotals(book, `${yearStart}-07-01`, `${yearStart + 1}-07-01`).netProfit,
    outstanding: invoiceTotal(invoices), overdueCount: overdue.length, overdueAmount: invoiceTotal(overdue),
    lowStock, activity: book.posted.slice(-12).reverse().map(publicDocument), series,
  };
}

function periodSalesPurchases(book, start, end) {
  let sales = 0;
  let purchases = 0;
  for (const doc of book.posted) {
    if (doc.date < start || doc.date >= end) continue;
    const amount = toMinor(doc.total);
    if (doc.kind === 'sale' || doc.kind === 'counter') sales += amount;
    if (doc.kind === 'sale-return') sales -= amount;
    if (doc.kind === 'purchase') purchases += amount;
    if (doc.kind === 'purchase-return') purchases -= amount;
  }
  return { sales: toMajor(sales), purchases: toMajor(purchases) };
}
