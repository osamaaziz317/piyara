export const DOCUMENT_TYPES = {
  receipt: { name: 'Cash Receipt Voucher', prefix: 'CRV', section: 'financial' },
  payment: { name: 'Cash Payment Voucher', prefix: 'CPV', section: 'financial' },
  opening: { name: 'Opening Balance', prefix: 'OB', section: 'financial' },
  journal: { name: 'Journal Voucher', prefix: 'JV', section: 'financial' },
  transfer: { name: 'Account Transfer', prefix: 'TRF', section: 'financial' },
  purchase: { name: 'Purchase Invoice', prefix: 'PUR', section: 'inventory' },
  counter: { name: 'Counter Sale', prefix: 'CS', section: 'inventory' },
  sale: { name: 'Sale Credit', prefix: 'SC', section: 'inventory' },
  'purchase-return': { name: 'Purchase Return', prefix: 'PR', section: 'inventory' },
  'sale-return': { name: 'Sale Return', prefix: 'SR', section: 'inventory' },
  'breakage-receive': { name: 'Breakage Receive', prefix: 'BR', section: 'inventory' },
  'breakage-dispatch': { name: 'Breakage Dispatch', prefix: 'BD', section: 'inventory' },
};

export const SECTIONS = ['dashboard', 'coding', 'financial', 'inventory', 'accounting', 'reports', 'setup'];
export const OPERATIONS = ['view', 'add', 'edit', 'delete', 'print', 'export', 'financial'];

export const CLIENTS = [
  { id: 'FM-001', name: 'BIFMA Traders', urdu: 'بفما ٹریڈرز', city: 'Lahore', email: '', phone: '042 35760421' },
  { id: 'FM-002', name: 'Karachi Tyre Wholesale', urdu: 'کراچی ٹائر ہول سیل', city: 'Karachi', email: '', phone: '021 32774590' },
];

const dateFormatter = new Intl.DateTimeFormat('en-CA', {
  timeZone: 'Asia/Karachi', year: 'numeric', month: '2-digit', day: '2-digit',
});

export function today() {
  const parts = Object.fromEntries(dateFormatter.formatToParts(new Date()).map(({ type, value }) => [type, value]));
  return `${parts.year}-${parts.month}-${parts.day}`;
}

export function addDays(date, days) {
  const value = new Date(`${date}T00:00:00Z`);
  value.setUTCDate(value.getUTCDate() + days);
  return value.toISOString().slice(0, 10);
}

export function monthDate(date, offset, day = 1) {
  const [year, month] = date.split('-').map(Number);
  return new Date(Date.UTC(year, month - 1 + offset, day)).toISOString().slice(0, 10);
}

export function fiscalYear(date = today()) {
  const [year, month] = date.split('-').map(Number);
  const start = month < 7 ? year - 1 : year;
  return `${start}–${start + 1}`;
}

export function defaultSettings() {
  return {
    currency: 'PKR', decimalPrecision: 2, negativeStock: false, paymentTerms: 30,
    creditLimit: 0, salePriceDefault: 'item', purchasePriceDefault: 'item', stockValuation: 'weightedAverage',
    breakageTreatment: 'separate', invoiceFormat: 'standard', sessionTimeout: 30, hideSetup: true,
    protectSetup: false, backupSchedule: 'off', backupTime: '18:00',
    numbering: Object.fromEntries(Object.entries(DOCUMENT_TYPES).map(([kind, definition]) => [kind, definition.prefix])),
  };
}

const record = (id, code, name, urdu, extras = {}) => ({ id, code, name, urdu, active: true, ...extras });

function sampleMasters(tyres) {
  const cities = [
    record('city-lhr', 'LHR', 'Lahore', 'لاہور', { country: 'Pakistan' }),
    record('city-khi', 'KHI', 'Karachi', 'کراچی', { country: 'Pakistan' }),
    record('city-fsd', 'FSD', 'Faisalabad', 'فیصل آباد', { country: 'Pakistan' }),
    record('city-rwp', 'RWP', 'Rawalpindi', 'راولپنڈی', { country: 'Pakistan' }),
    record('city-mul', 'MUX', 'Multan', 'ملتان', { country: 'Pakistan' }),
    record('city-hyd', 'HYD', 'Hyderabad', 'حیدرآباد', { country: 'Pakistan' }),
  ];
  const groups = [
    record('group-customers', 'GR-001', 'Trade Receivables', 'وصولیاں'),
    record('group-suppliers', 'GR-002', 'Trade Payables', 'ادائیگیاں'),
    record('group-expenses', 'GR-003', 'Operating Expenses', 'اخراجات'),
    record('group-income', 'GR-004', 'Other Income', 'دیگر آمدنی'),
  ];
  const party = (id, code, name, urdu, accountType, options = {}) => record(id, code, name, urdu, {
    accountType, country: 'Pakistan', phone: '', cityCode: tyres ? 'KHI' : 'LHR', address: '',
    openingBalance: 0, openingType: accountType === 'Supplier' ? 'Credit' : 'Debit', creditLimit: 0,
    paymentTerms: 30, remarks: '',
    groupId: { Customer: 'group-customers', Supplier: 'group-suppliers', Expense: 'group-expenses', Income: 'group-income' }[accountType],
    ...options,
  });
  const parties = tyres ? [
    party('party-a', 'C-001', 'Coastal Auto Centre', 'کوسٹل آٹو سینٹر', 'Customer', { paymentTerms: 14, openingBalance: 80000 }),
    party('party-b', 'C-002', 'Raza Wheel House', 'رضا ویل ہاؤس', 'Customer', { cityCode: 'HYD', paymentTerms: 21 }),
    party('party-c', 'C-003', 'Seaview Fleet Services', 'سی ویو فلیٹ سروسز', 'Customer', { paymentTerms: 30 }),
    party('party-d', 'C-004', 'Mehr Tyre Centre', 'مہر ٹائر سینٹر', 'Customer', { paymentTerms: 7 }),
    party('supplier-a', 'S-001', 'Sindh Rubber Supply', 'سندھ ربڑ سپلائی', 'Supplier', { openingBalance: 123000 }),
    party('supplier-b', 'S-002', 'Pioneer Wheels', 'پایونیر ویلز', 'Supplier'),
  ] : [
    party('party-a', 'C-001', 'Ahmad Traders', 'احمد ٹریڈرز', 'Customer', { phone: '0300 555 0132', address: 'Shop 12, Shah Alam Market', paymentTerms: 14, openingBalance: 42800 }),
    party('party-b', 'C-002', 'Al Madina Store', 'المدینہ اسٹور', 'Customer', { phone: '0301 555 0146', cityCode: 'FSD', address: 'Rail Bazar', paymentTerms: 21 }),
    party('party-c', 'C-003', 'Khan Brothers', 'خان برادرز', 'Customer', { phone: '0302 555 0175', cityCode: 'RWP', paymentTerms: 30 }),
    party('party-d', 'C-004', 'Usman Distribution', 'عثمان ڈسٹری بیوشن', 'Customer', { phone: '0303 555 0188', cityCode: 'MUX', paymentTerms: 7 }),
    party('party-e', 'C-005', 'Raza Wholesale', 'رضا ہول سیل', 'Customer', { phone: '0304 555 0109', paymentTerms: 30, openingBalance: 26500 }),
    party('supplier-a', 'S-001', 'Pak Plastic Industries', 'پاک پلاسٹک انڈسٹریز', 'Supplier', { phone: '0305 555 0129', openingBalance: 117500 }),
    party('supplier-b', 'S-002', 'Eastern Moulders', 'ایسٹرن مولڈرز', 'Supplier', { phone: '0306 555 0120', cityCode: 'KHI', openingBalance: 45000 }),
  ];
  parties.push(
    party('expense-transport', 'E-001', 'Freight & Delivery', 'بار برداری کے اخراجات', 'Expense'),
    party('expense-office', 'E-002', 'Shop Expenses', 'دکان کے اخراجات', 'Expense'),
    party('income-commission', 'R-001', 'Commission Income', 'کمیشن آمدنی', 'Income'),
  );
  const categories = tyres ? [
    record('category-a', 'CAT-001', 'Passenger Tyres', 'کار کے ٹائر'),
    record('category-b', 'CAT-002', 'Motorcycle & Tubes', 'موٹر سائیکل اور ٹیوب'),
    record('category-c', 'CAT-003', 'Accessories', 'لوازمات'),
  ] : [
    record('category-a', 'CAT-001', 'Storage & Containers', 'اسٹوریج اور ڈبے'),
    record('category-b', 'CAT-002', 'Household Plastics', 'گھریلو پلاسٹک'),
    record('category-c', 'CAT-003', 'Packaging Supplies', 'پیکنگ کا سامان'),
  ];
  const units = [
    record('unit-pcs', 'PCS', 'Piece', 'عدد'),
    record('unit-ctn', 'CTN', 'Carton', 'کارٹن'),
    record('unit-pack', 'PKT', 'Pack', 'پیکٹ'),
    record('unit-pair', 'PAIR', 'Pair', 'جوڑا'),
  ];
  const item = (id, code, name, urdu, category, unit, purchasePrice, salePrice, openingStock, minimumStock, barcode) => record(id, code, name, urdu, {
    category, unit, purchasePrice, salePrice, openingStock, openingRate: purchasePrice, minimumStock, barcode, description: '', openingWarehouseId: 'warehouse-main',
  });
  const items = tyres ? [
    item('item-a', 'IT-001', 'RoadLine Tyre • 185/65 R15', 'روڈ لائن ٹائر', 'category-a', 'unit-pcs', 10500, 12500, 680, 18, '8901000000110'),
    item('item-b', 'IT-002', 'CityRide Tyre • 165/70 R13', 'سٹی رائیڈ ٹائر', 'category-a', 'unit-pcs', 7800, 9400, 420, 15, '8901000000120'),
    item('item-c', 'IT-003', 'Motorcycle Tyre • 2.75–18', 'موٹر سائیکل ٹائر', 'category-b', 'unit-pcs', 2400, 2850, 680, 20, '8901000000130'),
    item('item-d', 'IT-004', 'Truck Inner Tube • 20 in', 'ٹرک کی ٹیوب', 'category-b', 'unit-pcs', 3480, 4100, 270, 15, '8901000000140'),
    item('item-e', 'IT-005', 'Tyre Valve • TR413', 'ٹائر والو', 'category-c', 'unit-pack', 630, 815, 370, 25, '8901000000150'),
    item('item-f', 'IT-006', 'Tubeless Repair Kit', 'ٹیوب لیس مرمت کٹ', 'category-c', 'unit-pack', 520, 650, 8, 15, '8901000000160'),
    item('item-g', 'IT-007', 'Wheel Balancing Weights', 'وہیل بیلنس وزن', 'category-c', 'unit-ctn', 720, 870, 4, 12, '8901000000170'),
  ] : [
    item('item-a', 'IT-001', 'Stackable Storage Bin • 24 L', 'اسٹوریج بن ۲۴ لیٹر', 'category-a', 'unit-ctn', 1560, 1920, 2200, 40, '8901000000210'),
    item('item-b', 'IT-002', 'Heavy Duty Bucket • 20 L', 'مضبوط بالٹی ۲۰ لیٹر', 'category-b', 'unit-ctn', 1175, 1450, 1300, 45, '8901000000220'),
    item('item-c', 'IT-003', 'Airtight Food Box • 3 pc', 'ایئر ٹائٹ فوڈ باکس', 'category-a', 'unit-ctn', 1060, 1310, 1440, 35, '8901000000230'),
    item('item-d', 'IT-004', 'Plastic Chairs • Classic', 'پلاسٹک کی کرسی', 'category-b', 'unit-pcs', 1275, 1580, 550, 30, '8901000000240'),
    item('item-e', 'IT-005', 'Carry Bag • Medium', 'درمیانہ شاپر', 'category-c', 'unit-pack', 340, 425, 1500, 85, '8901000000250'),
    item('item-f', 'IT-006', 'Plastic Crate • Large', 'بڑی پلاسٹک کریٹ', 'category-a', 'unit-pcs', 660, 875, 8, 20, '8901000000260'),
    item('item-g', 'IT-007', 'Packaging Roll • 18 in', 'پیکنگ رول ۱۸ انچ', 'category-c', 'unit-pcs', 730, 910, 12, 30, '8901000000270'),
    item('item-h', 'IT-008', 'Water Cooler • 12 L', 'واٹر کولر ۱۲ لیٹر', 'category-b', 'unit-pcs', 1190, 1480, 5, 15, '8901000000280'),
  ];
  return {
    parties, items, cities, categories, groups, units,
    warehouses: [
      record('warehouse-main', 'WH-01', 'Main Warehouse', 'مرکزی گودام', { cityCode: tyres ? 'KHI' : 'LHR' }),
      record('warehouse-depot', 'WH-02', 'City Depot', 'شہری گودام', { cityCode: tyres ? 'HYD' : 'FSD' }),
    ],
    cash: [
      record('cash-counter', 'CA-001', 'Main Cash Counter', 'مرکزی کیش کاؤنٹر', { openingBalance: tyres ? 415000 : 318500, openingType: 'Debit' }),
      record('cash-petty', 'CA-002', 'Petty Cash', 'چھوٹی نقدی', { openingBalance: tyres ? 62000 : 37500, openingType: 'Debit' }),
    ],
    banks: [
      record('bank-main', 'BA-001', 'Meezan Business Account', 'میزان بزنس اکاؤنٹ', { bankName: 'Meezan Bank', openingBalance: 945000, openingType: 'Debit' }),
      record('bank-other', 'BA-002', 'HBL Current Account', 'ایچ بی ایل کرنٹ اکاؤنٹ', { bankName: 'HBL', openingBalance: 458500, openingType: 'Debit' }),
    ],
    methods: [
      record('method-cash', 'PM-001', 'Cash', 'نقد'), record('method-bank', 'PM-002', 'Bank Transfer', 'بینک ٹرانسفر'),
      record('method-cheque', 'PM-003', 'Cheque', 'چیک'),
    ],
    salesmen: [record('salesman-one', 'SM-001', tyres ? 'Bilal Raja' : 'Hamza Ali', tyres ? 'بلال راجہ' : 'حمزہ علی', { phone: '0312 555 0182' })],
  };
}

export function clerkPermissions() {
  const readOnly = { view: true, add: false, edit: false, delete: false, print: false, export: false, financial: false };
  return {
    dashboard: { ...readOnly }, coding: { ...readOnly }, financial: { ...readOnly, add: true, print: true },
    inventory: { ...readOnly, add: true, edit: true, print: true }, accounting: { ...readOnly, view: false },
    reports: { ...readOnly }, setup: { ...readOnly, view: false },
  };
}

export function seed(client) {
  const date = today();
  const tyres = client.id === 'FM-002';
  const masters = sampleMasters(tyres);
  const settings = defaultSettings();
  const documents = [];
  const sequences = { purchase: tyres ? 480 : 1036, sale: tyres ? 380 : 1032, counter: 348, receipt: 144, payment: 78 };

  function add(kind, date, data) {
    const sequence = (sequences[kind] || 0) + 1;
    sequences[kind] = sequence;
    const row = {
      ...data, id: `sample-${kind}-${sequence}`, number: `${settings.numbering[kind]}-${String(sequence).padStart(5, '0')}`,
      kind, date, order: documents.length + 1, status: 'posted', valuation: settings.stockValuation,
      precision: settings.decimalPrecision, breakageTreatment: settings.breakageTreatment,
    };
    documents.push(row);
    return row;
  }
  function line(index, pieces, purchased) {
    const item = masters.items[index];
    return { itemId: item.id, quantity: pieces, rate: purchased ? item.purchasePrice : item.salePrice, discount: 0, tax: 0 };
  }
  function linesForMix(month, purchased) {
    const volumes = [58, 80, 69, 115, 105, 137, 142];
    const volume = volumes[month];
    const factor = tyres ? (purchased ? 0.25 : 0.23) : (purchased ? 0.78 : 1);
    return [line(0, Math.max(1, Math.round(volume * factor * 2.3)), purchased),
      line(1, Math.max(1, Math.round(volume * factor * 1.75)), purchased),
      line(2, Math.max(1, Math.round(volume * factor * 1.2)), purchased),
      line(3, Math.max(1, Math.round(volume * factor * 0.73)), purchased),
      line(4, Math.max(1, Math.round(volume * factor * 1.6)), purchased)];
  }
  const warehouseId = 'warehouse-main';
  for (let month = 0; month < 7; month += 1) {
    const offset = month - 6;
    const day = Number(date.slice(-2));
    const dated = (dayOfMonth) => monthDate(date, offset, offset === 0 ? Math.min(day, dayOfMonth) : dayOfMonth);
    add('purchase', dated(2), { partyId: 'supplier-a', warehouseId, cashId: 'bank-main', paid: 0, lines: linesForMix(month, true), narration: 'Monthly warehouse replenishment' });
    const mainLines = linesForMix(month, false);
    const partyId = ['party-a', 'party-b', 'party-c', 'party-d'][month % 4];
    const total = mainLines.reduce((sum, row) => sum + row.rate * row.quantity, 0);
    add('sale', dated(5), {
      partyId, warehouseId, cashId: 'bank-main', paid: month < 4 ? total : Math.round(total * 0.64),
      terms: month === 6 ? 28 : 21, lines: mainLines, narration: 'Monthly wholesale order',
    });
    add('counter', dated(11), { warehouseId, cashId: 'cash-counter', paid: line(4, 25 + month * 8, false).rate * (25 + month * 8), lines: [line(4, 25 + month * 8, false)], narration: 'Counter order' });
    add('payment', dated(14), { partyId: 'supplier-a', cashId: 'bank-main', methodId: 'method-bank', amount: Math.round(total * 0.53), reference: '', narration: 'Supplier settlement' });
    add('payment', dated(17), { partyId: 'expense-transport', cashId: 'cash-petty', methodId: 'method-cash', amount: 15400 + month * 1180, narration: 'Freight and local deliveries' });
  }
  add('opening', monthDate(date, -6, 1), { openingKind: 'Inventory', itemId: 'item-g', warehouseId, quantity: 2, rate: masters.items[6].purchasePrice, narration: 'Opening stock count adjustment' });
  add('transfer', monthDate(date, -5, 1), { fromId: 'bank-main', toId: 'cash-petty', amount: 140000, narration: 'Petty cash float' });
  add('journal', monthDate(date, -1, 8), { debitId: 'expense-office', creditId: 'bank-main', amount: 9400, narration: 'Store supplies and stationery' });
  const purchase = add('purchase', addDays(date, -6), { partyId: 'supplier-b', warehouseId, paid: 0, lines: [line(0, tyres ? 12 : 96, true), line(3, tyres ? 21 : 73, true)], narration: 'Warehouse replenishment' });
  const originalSale = documents.find((doc) => doc.kind === 'sale' && doc.date.slice(0, 7) === monthDate(date, -1).slice(0, 7));
  add('sale-return', monthDate(date, -1, 24), { partyId: originalSale.partyId, warehouseId, paid: 0, lines: [line(1, 2, false)], reference: originalSale.number, referenceInvoiceId: originalSale.id, narration: 'Wrong size returned in original packaging' });
  add('purchase-return', addDays(date, -4), { partyId: purchase.partyId, warehouseId, paid: 0, lines: [line(3, 2, true)], reference: purchase.number, referenceInvoiceId: purchase.id, narration: 'Over-supplied stock returned' });
  add('sale', addDays(date, -22), { partyId: 'party-d', warehouseId, cashId: 'cash-counter', paid: 0, terms: 7, lines: [line(1, tyres ? 5 : 32, false), line(3, tyres ? 5 : 26, false)], narration: 'Credit order' });
  const partial = add('sale', addDays(date, -12), { partyId: 'party-a', warehouseId, cashId: 'bank-main', paid: 8500, terms: 14, lines: [line(0, tyres ? 5 : 47, false), line(1, tyres ? 3 : 38, false)], narration: 'Wholesale distribution order' });
  add('breakage-receive', addDays(date, -7), { partyId: 'party-c', warehouseId, paid: 0, lines: [{ ...line(0, 3, true), rate: 210 }], narration: 'Damaged stock received' });
  add('breakage-dispatch', addDays(date, -4), { partyId: 'supplier-b', warehouseId, paid: 0, lines: [{ ...line(0, 1, true), rate: 210 }], narration: 'One damaged unit sent to supplier' });
  add('receipt', addDays(date, -3), { partyId: 'party-a', cashId: 'bank-main', methodId: 'method-bank', amount: 58500, reference: partial.number, referenceInvoiceId: partial.id, narration: 'Partial invoice payment' });
  add('counter', addDays(date, -2), { warehouseId, cashId: 'cash-counter', paid: line(2, 16, false).rate * 16, lines: [line(2, 16, false)], narration: 'Walk-in counter sale' });
  add('payment', addDays(date, -1), { partyId: 'supplier-b', cashId: 'bank-main', methodId: 'method-bank', amount: 76000, reference: 'IBFT-87068', narration: 'Supplier payment' });
  add('purchase', date, { partyId: 'supplier-a', warehouseId, paid: 0, lines: [line(1, tyres ? 17 : 112, true)], narration: 'Stock replenishment' });
  add('sale', date, { partyId: 'party-c', warehouseId, cashId: 'cash-counter', paid: 0, terms: 30, lines: [line(0, tyres ? 4 : 53, false), line(4, tyres ? 8 : 91, false)], narration: 'Credit invoice' });

  return {
    profile: {
      id: client.id, name: client.name, urdu: client.urdu, owner: tyres ? 'Noman Raza' : 'Fahad Masood',
      address: tyres ? 'M. A. Jinnah Road, Karachi' : 'Shah Alam Market, Lahore', phone: client.phone,
      country: 'Pakistan', city: client.city, logo: '', financialYear: fiscalYear(date),
    },
    settings, masters, documents, sequences,
    users: [
      { id: 'admin', username: 'admin', password: 'demo123', name: tyres ? 'Noman Raza' : 'Fahad Masood', role: 'Administrator', active: true, permissions: {} },
      { id: 'clerk', username: 'clerk', password: 'demo123', name: tyres ? 'Bilal Raja' : 'Hamza Ali', role: 'Clerk', active: true, permissions: clerkPermissions() },
    ],
    audit: [{ id: 'seed-start', time: `${addDays(date, -1)}T05:00:00.000Z`, user: 'admin', action: 'Sample workspace prepared', record: client.name, before: null, after: null, device: 'Desktop demo' }],
    login: [], nextId: documents.length + 100,
  };
}
