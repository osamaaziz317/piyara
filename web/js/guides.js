export const reportGroups = {
  financial: {
    label: 'Financial reports',
    entries: [
      ['voucher-printing', 'Voucher printing'],
      ['cash-statement', 'Cash statement'],
      ['account-ledger', 'Account ledger'],
      ['trial-balance', 'Trial balance'],
      ['sub-trial-balance', 'Sub trial balance'],
      ['cash-recovery', 'Cash-wise recovery reports'],
      ['profit-loss', 'Profit and loss reports'],
    ],
  },
  inventory: {
    label: 'Inventory reports',
    entries: [
      ['stock-summary', 'Stock summary'],
      ['stock-value', 'Stock summary with value'],
      ['item-ledger', 'Item ledger'],
      ['purchase-details', 'Purchase / purchase return details'],
      ['sale-details', 'Sale / sale return details'],
    ],
  },
};

export const guides = [
  {
    question: 'Getting started',
    urduTitle: 'کام شروع کرنے کا طریقہ',
    answer: 'Choose your business on the sign-in screen. The dashboard shows sales, purchases, cash and stock, along with payment reminders for that business.',
    urdu: 'لاگ اِن کرتے وقت اپنا کاروبار منتخب کریں۔ ڈیش بورڈ پر اسی کاروبار کی فروخت، خریداری، نقدی، اسٹاک اور ادائیگی کی یاد دہانیاں نظر آئیں گی۔',
  },
  {
    question: 'Create a party account',
    urduTitle: 'پارٹی کا کھاتہ بنائیں',
    answer: 'Open Coding → Party accounts and choose Add. Enter the English and Urdu names, city code and account details. Set the opening balance and payment terms, then press F6 to save.',
    urdu: 'کوڈنگ میں پارٹی اکاؤنٹس کھولیں اور نیا ریکارڈ بنائیں۔ انگریزی اور اردو نام، شہر کا کوڈ اور کھاتے کی تفصیل درج کریں۔ ابتدائی بقایا اور ادائیگی کی مدت لکھ کر محفوظ کریں۔',
  },
  {
    question: 'Add a stock item',
    urduTitle: 'نیا آئٹم درج کریں',
    answer: 'Open Coding → Items and choose Add. Enter the item code, both names, unit, purchase and sale rates, opening stock and minimum stock. Press F6 to save.',
    urdu: 'کوڈنگ میں آئٹمز کھولیں۔ آئٹم کا کوڈ، دونوں نام، اکائی، خرید و فروخت کے نرخ اور ابتدائی و کم از کم اسٹاک درج کریں۔ پھر F6 سے محفوظ کریں۔',
  },
  {
    question: 'Make a sale',
    urduTitle: 'فروخت کا بل بنائیں',
    answer: 'Open Inventory → Sale credit for a credit invoice, or Counter sale for cash. Use Enter to choose the party and items. Enter quantity and rate for each row, check the total, then press F6 to save.',
    urdu: 'ادھار کے لیے سیل کریڈٹ اور نقد فروخت کے لیے کاؤنٹر سیل کھولیں۔ انٹر سے پارٹی اور آئٹم منتخب کریں۔ ہر آئٹم کی مقدار اور نرخ درج کریں، کل رقم دیکھیں اور بل محفوظ کریں۔',
  },
  {
    question: 'Record a purchase',
    urduTitle: 'خریداری درج کریں',
    answer: 'Open Inventory → Purchase. Choose the supplier, enter the item quantities and purchase rates, and record the amount already paid. Saving the invoice adds the quantities to stock.',
    urdu: 'انوینٹری میں خریداری کھولیں۔ سپلائر منتخب کریں اور آئٹمز کی مقدار، خریداری کے نرخ اور ادا شدہ رقم درج کریں۔ بل محفوظ کرنے پر سامان اسٹاک میں شامل ہو گا۔',
  },
  {
    question: 'Receive a payment',
    urduTitle: 'وصولی درج کریں',
    answer: 'Open Financial transactions → Cash receipt voucher. Select the party and receiving cash or bank account. Enter the amount, payment reference and narration, then save the voucher.',
    urdu: 'مالی لین دین میں وصولی کا واؤچر کھولیں۔ پارٹی اور رقم وصول کرنے والا نقد یا بینک کھاتہ منتخب کریں۔ رقم، حوالہ اور تفصیل لکھ کر واؤچر محفوظ کریں۔',
  },
  {
    question: 'Check a party ledger',
    urduTitle: 'پارٹی کا کھاتہ دیکھیں',
    answer: 'Open Accounting → Party ledger and choose an account. Review the opening balance, debits, credits and payments. Open Late payments to see which invoices are due.',
    urdu: 'اکاؤنٹنگ میں پارٹی لیجر کھول کر کھاتہ منتخب کریں۔ ابتدائی بقایا، ڈیبٹ، کریڈٹ اور وصولی دیکھیں۔ بقایا بلوں کی تاریخ دیکھنے کے لیے تاخیر والی ادائیگیاں کھولیں۔',
  },
];

export const faq = [
  {
    question: 'Where can I check daily entries, Jama and Baki?',
    answer: 'Recent transactions on the dashboard show the latest Roznamcha entries. Cash receipt vouchers record Jama. The party ledger and Late payments workspace show the remaining Baki.',
    urdu: 'ڈیش بورڈ پر روزنامچے کے تازہ اندراجات دیکھیں۔ جمع رقم کے لیے وصولی کا واؤچر استعمال کریں۔ باقی رقم پارٹی لیجر اور تاخیر والی ادائیگیوں میں نظر آتی ہے۔',
  },
  {
    question: 'Can I look up a party or item by its Urdu name?',
    answer: 'Yes. Search by code, English name or Urdu name in the record browser or selection dialog.',
    urdu: 'جی ہاں۔ ریکارڈ یا انتخاب کی فہرست میں کوڈ، انگریزی نام یا اردو نام سے تلاش کریں۔',
  },
  {
    question: 'What do the late payment colours mean?',
    answer: 'Green means the invoice is within terms. Amber means the due date is approaching. Red means an unpaid balance is overdue.',
    urdu: 'سبز کا مطلب ہے کہ ادائیگی کی مدت باقی ہے۔ زرد کا مطلب ہے کہ آخری تاریخ قریب ہے۔ سرخ بقایا رقم کی تاخیر ظاہر کرتا ہے۔',
  },
  {
    question: 'Where can I change who can see financial information?',
    answer: 'Sign in as an administrator and open Setup → Users and permissions. Choose the user and update their access.',
    urdu: 'ایڈمن کے طور پر لاگ اِن کریں، پھر سیٹ اپ میں صارفین اور اجازتیں کھولیں۔ متعلقہ صارف منتخب کر کے اجازتیں تبدیل کریں۔',
  },
  {
    question: 'How do I set up the Super Admin or change my password?',
    answer: 'To create the Super Admin for your business, sign in as an administrator and choose Set up Super Admin in Setup → Users and permissions. To change your own password, open the user menu and choose Change password.',
    urdu: 'اپنے کاروبار کا سپر ایڈمن بنانے کے لیے ایڈمن سے لاگ اِن کریں، پھر سیٹ اپ میں صارفین اور اجازتیں کھول کر سپر ایڈمن بنائیں۔ اپنا پاس ورڈ بدلنے کے لیے صارف مینو سے پاس ورڈ تبدیل کریں۔',
  },
];

export const shortcuts = [
  ['Enter', 'Next field or select'],
  ['Tab', 'Next field'],
  ['Shift + Tab', 'Previous field'],
  ['Esc', 'Close the current dialog or screen'],
  ['F2 / Ctrl + F', 'Search'],
  ['F3', 'Add record'],
  ['F4', 'Edit selected record'],
  ['F5', 'Refresh'],
  ['F6 / Ctrl + S', 'Save'],
  ['F7', 'Print'],
  ['F8', 'Delete when permitted'],
];
