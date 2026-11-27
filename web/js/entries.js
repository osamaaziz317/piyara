import { calculateDocumentTotals } from "./demo.js";
import { masterDefinitions, masterGroups, renderMaster } from "./masters.js";
import {
  bilingual, button, enterAdvance, errors, field, findMatch, icon, listTable, makePage,
  optionList, printRecord, setupPicker, tabFields, textInput, urdu
} from "./entry-ui.js";

const journalTypes = {
  receipt: { title: "Cash receipt voucher", urdu: "کیش رسید", description: "Record money received in a cash or bank account.", icon: "arrow-down" },
  payment: { title: "Cash payment voucher", urdu: "کیش ادائیگی", description: "Record money paid from a cash or bank account.", icon: "arrow-up" },
  opening: { title: "Opening balance", urdu: "ابتدائی بیلنس", description: "Enter starting account balances or opening stock.", icon: "book-open" },
  journal: { title: "Journal voucher", urdu: "جرنل واؤچر", description: "Record a balanced debit and credit in the general ledger.", icon: "book-open" },
  transfer: { title: "Account transfer", urdu: "اکاؤنٹ منتقلی", description: "Move funds between cash or bank accounts.", icon: "repeat" }
};

const tradeTypes = {
  purchase: { title: "Purchase", urdu: "خریداری", description: "Enter a supplier invoice and receive stock.", icon: "truck", party: "suppliers", partyLabel: "Supplier account", rate: "purchase", payment: "Paid amount" },
  counter: { title: "Counter sale", urdu: "نقد فروخت", description: "Ring up a sale and record cash received at the counter.", icon: "shopping-cart", party: "customers", partyLabel: "Customer (optional for fully paid sales)", rate: "sale", payment: "Cash received" },
  sale: { title: "Sale credit", urdu: "ادھار فروخت", description: "Create a credit invoice and track what the customer owes.", icon: "receipt", party: "customers", partyLabel: "Party account", rate: "sale", payment: "Initial payment" },
  "purchase-return": { title: "Purchase return", urdu: "خریداری واپسی", description: "Record stock returned and the supplier adjustment.", icon: "undo", party: "suppliers", partyLabel: "Supplier account", rate: "purchase", returnType: "Purchase invoice" },
  "sale-return": { title: "Sale return", urdu: "فروخت واپسی", description: "Receive returned stock and credit the customer’s account.", icon: "undo", party: "customers", partyLabel: "Customer account", rate: "sale", returnType: "Sale invoice" },
  "breakage-receive": { title: "Breakage receive", urdu: "ٹوٹا مال وصول", description: "Receive damaged goods into breakage stock.", icon: "package", party: "parties", partyLabel: "Party account", rate: "purchase", damaged: true },
  "breakage-dispatch": { title: "Breakage dispatch", urdu: "ٹوٹا مال روانگی", description: "Dispatch damaged goods and reduce breakage stock.", icon: "truck", party: "parties", partyLabel: "Party account", rate: "purchase", damaged: true }
};

const openingTypes = ["Party", "Supplier", "Customer", "Cash", "Bank", "Inventory"];
const openingLists = { Party: "parties", Supplier: "suppliers", Customer: "customers", Cash: "cash", Bank: "banks" };
const financialDescription = "Post receipts, payments, journals, transfers and opening balances.";
const inventoryDescription = "Handle sales, purchases, returns and damaged stock in one workspace.";
let nextFieldId = 0;

function formatEntryDate(value) {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(value ?? ""));
  return match ? `${match[3]}/${match[2]}/${match[1]}` : "";
}

function parseEntryDate(value) {
  const raw = String(value ?? "");
  const shorthand = /^\d{6}$/.test(raw);
  const match = shorthand ? null : /^(\d{2})\/(\d{2})\/(\d{4})$/.exec(raw);
  if (!shorthand && !match) return "";
  const day = shorthand ? raw.slice(0, 2) : match[1];
  const month = shorthand ? raw.slice(2, 4) : match[2];
  const year = shorthand ? `20${raw.slice(4, 6)}` : match[3];
  const parsed = new Date(0);
  parsed.setUTCFullYear(Number(year), Number(month) - 1, Number(day));
  parsed.setUTCHours(0, 0, 0, 0);
  if (parsed.getUTCFullYear() !== Number(year) || parsed.getUTCMonth() !== Number(month) - 1 || parsed.getUTCDate() !== Number(day)) return "";
  return `${year}-${month}-${day}`;
}

function entryDateError(value, today) {
  if (!value) return "";
  const parsed = parseEntryDate(value);
  if (!parsed) return "Enter a valid date in DD/MM/YYYY format or six-digit DDMMYY format.";
  if (parsed > today) return "Date cannot be later than today.";
  return "";
}

function catalog(route, ui) {
  const catalogs = {
    coding: { title: "Coding", urdu: "بنیادی ریکارڈ", description: "Maintain the records used in transactions across the business.", defs: masterDefinitions, groups: masterGroups },
    financial: { title: "Financial transactions", urdu: "مالی لین دین", description: financialDescription, defs: journalTypes,
      groups: [{ name: "Vouchers and balances", keys: Object.keys(journalTypes) }] },
    inventory: { title: "Inventory", urdu: "اسٹاک", description: inventoryDescription, defs: tradeTypes, groups: [
      { name: "Sales and purchases", keys: ["sale", "counter", "purchase"] },
      { name: "Returns and breakage", keys: ["sale-return", "purchase-return", "breakage-receive", "breakage-dispatch"] }
    ] }
  };
  const { title, urdu: nameUrdu, description, defs, groups } = catalogs[route];
  const page = makePage(ui, { title, nameUrdu, description, section: "Workspace" });
  const content = document.createElement("div");
  content.className = "entry-catalog";
  content.innerHTML = groups.map(group => `<section>
    <h2>${group.name}</h2>
    <div class="entry-module-grid">${group.keys.map(key => `
      <button class="card entry-module" type="button" data-route="${route}.${key}">
        <span class="entry-module-icon">${icon(ui, defs[key].icon, 20)}</span>
        <span class="entry-module-text"><strong>${defs[key].title}</strong><span>${defs[key].description}</span></span>
        ${icon(ui, "chevron-right", 17)}
      </button>`).join("")}</div>
    </section>`).join("");
  page.append(content);
  content.addEventListener("click", event => {
    const target = event.target.closest("[data-route]");
    if (target) ui.go(target.dataset.route);
  });
  return page;
}

function entryField(ui, label, name, value, { type = "text", step, min, max, required = false, wide = false, hint = "", readOnly = false, inputmode } = {}) {
  const numeric = type === "number";
  const date = type === "date";
  const hintId = hint ? `entry-hint-${++nextFieldId}` : "";
  const a11yHint = hint ? `aria-describedby="${hintId}"` : "";
  const extra = `type="${date ? "text" : type}" data-enter ${required ? "required" : ""} ${readOnly ? "readonly" : ""} ${a11yHint}
    ${numeric ? `inputmode="${inputmode ?? "decimal"}" step="${step ?? 0.01}" min="${min ?? 0}"${max != null ? ` max="${max}"` : ""}`
      : date ? 'inputmode="numeric" maxlength="10" placeholder="dd/mm/yyyy" autocomplete="off"'
        : 'maxlength="100" dir="auto"'}`;
  return field(label, `${textInput(name, value, ui, extra)}${hint ? `<small id="${hintId}" class="entry-field-hint">${hint}</small>` : ""}`, { required, wide });
}

function noteField(ui, value) {
  return field("Narration", `<textarea name="narration" data-enter rows="2" dir="auto" maxlength="500" placeholder="Add a note for this transaction">${ui.escape(value || "")}</textarea>`, { wide: true });
}

function lookupControl(ui, label, name, value, rows, { required = false, placeholder, wide = false, title } = {}) {
  const current = rows.find(row => String(row.id) === String(value));
  const body = current ? bilingual(current, ui) : `<span class="muted">${ui.escape(placeholder || `Select ${label.toLowerCase()}`)}</span>`;
  const labelId = `entry-label-${++nextFieldId}`;
  return `
    <div class="field${wide ? " entry-field-wide" : ""} entry-lookup" data-lookup="${name}" ${required ? "data-required" : ""}>
      <span id="${labelId}">${label}${required ? ' <span class="entry-required" aria-hidden="true">*</span>' : ""}</span>
      <input type="hidden" name="${name}" value="${ui.escape(value || "")}">
      <button type="button" data-enter data-picker="${name}" data-title="${ui.escape(title || label)}" class="entry-select" aria-labelledby="${labelId}" aria-haspopup="dialog">
        <span data-selection>${body}</span>${icon(ui, "search", 16)}
      </button>
    </div>`;
}

function accountDescription(ui, row) {
  if (!ui.demo.can("accounting", "financial") || row.balance == null) return row.accountType || "";
  const value = ui.money(Math.abs(row.balance));
  return row.balance ? `${value} ${row.balance < 0 ? "Cr" : "Dr"}` : value;
}

function rowsFor(ui, collection, current) {
  if (!collection) return [];
  const lists = collection === "ledger" ? ["parties", "cash", "banks"]
    : collection === "settlement" ? ["cash", "banks"] : [collection];
  return lists.flatMap(key => ui.demo.list(key)).filter(row => row.active || String(row.id) === String(current));
}

function openingFields(ui, type, record, step) {
  if (type === "Inventory") {
    return [
      lookupControl(ui, "Item", "itemId", record.itemId, rowsFor(ui, "items", record.itemId), { required: true }),
      lookupControl(ui, "Warehouse", "warehouseId", record.warehouseId, rowsFor(ui, "warehouses", record.warehouseId), { required: true }),
      entryField(ui, "Opening quantity", "quantity", record.quantity ?? "", { type: "number", step: 1, min: 1, required: true, inputmode: "numeric" }),
      entryField(ui, "Opening rate", "rate", record.rate ?? "", { type: "number", step: 1, required: true, inputmode: "numeric" })
    ].join("");
  }
  return [
    lookupControl(ui, `${type} account`, "accountId", record.accountId, rowsFor(ui, openingLists[type], record.accountId), { required: true }),
    field("Balance side", `<select name="openingType" data-enter required>${optionList(
      ["Debit", "Credit"].map(value => ({ id: value, name: value })),
      record.openingType || (type === "Supplier" ? "Credit" : "Debit"), ui
    )}</select>`, { required: true }),
    entryField(ui, "Opening amount", "amount", record.amount ?? "", { type: "number", step, min: step, required: true })
  ].join("");
}

function journalFields(ui, kind, record, step) {
  const lookup = (label, name, collection, required = true) => lookupControl(
    ui, label, name, record[name], rowsFor(ui, collection, record[name]), { required }
  );
  const amount = entryField(ui, "Amount", "amount", record.amount ?? "", { type: "number", step, min: step, required: true });
  const reference = entryField(ui, "Reference number", "reference", record.reference, {
    hint: kind === "receipt" ? "Use the invoice number to apply this receipt to a specific invoice." : ""
  });
  switch (kind) {
    case "receipt":
    case "payment":
      return `${lookup("Account name", "partyId", "parties")}${amount}${lookup("Payment method", "methodId", "methods")}
        ${lookup("Cash or bank account", "cashId", "settlement")}${reference}${noteField(ui, record.narration)}`;
    case "journal":
      return `${lookup("Debit account", "debitId", "ledger")}${lookup("Credit account", "creditId", "ledger")}${amount}${reference}${noteField(ui, record.narration)}`;
    case "transfer":
      return `${lookup("From account", "fromId", "settlement")}${lookup("To account", "toId", "settlement")}${amount}
        ${lookup("Payment method", "methodId", "methods", false)}${reference}${noteField(ui, record.narration)}`;
    case "opening": {
      const type = record.openingKind || "Party";
      return `${field("Opening balance for", `<select name="openingKind" data-enter required>${optionList(
        openingTypes.map(value => ({ id: value, name: value })), type, ui
      )}</select>`, { required: true })}<div class="entry-subfields" data-opening-fields>${openingFields(ui, type, record, step)}</div>
      ${noteField(ui, record.narration)}`;
    }
    default:
      return "";
  }
}

function documentParty(ui, record) {
  if (record.party) return { name: record.party, urdu: record.partyUr || "" };
  const id = record.partyId || record.accountId || record.debitId || record.fromId;
  return rowsFor(ui, "ledger", id).find(row => String(row.id) === String(id));
}

function statusMarkup(record) {
  const cancelled = record.status === "cancelled";
  return `<span class="chip entry-status ${cancelled ? "entry-status-gray" : "entry-status-green"}">${cancelled ? "Cancelled" : "Posted"}</span>`;
}

function documentMarkup(ui, record, def) {
  const party = documentParty(ui, record);
  const profile = ui.demo.profile();
  const kindName = ui.escape(def.title);
  const amount = value => ui.escape(ui.money(value));
  const items = record.lines?.length ? new Map(ui.demo.list("items").map(item => [String(item.id), item])) : new Map();
  const lineRows = record.lines?.map(line => {
    const item = items.get(String(line.itemId));
    return `<tr><td>${item ? bilingual(item, ui) : ui.escape(line.item || line.itemName || "Item")}</td>
      <td>${ui.escape(line.quantity)}</td><td>${amount(line.rate)}</td><td>${amount(line.discount || 0)}</td>
      <td>${ui.escape(line.tax || 0)}%</td><td>${amount(line.total ?? (line.quantity * line.rate))}</td></tr>`;
  }) || [];
  const ledgerFields = [
    ["Debit account", record.debitId], ["Credit account", record.creditId], ["Cash or bank account", record.cashId],
    ["From account", record.fromId], ["To account", record.toId], ["Payment method", record.methodId, "methods"],
    ["Warehouse", record.warehouseId, "warehouses"], ["Item", record.itemId, "items"]
  ].filter(([, id]) => id != null && id !== "").map(([label, id, collection = "ledger"]) => {
    const row = rowsFor(ui, collection, id).find(item => String(item.id) === String(id));
    return `<div><dt>${label}</dt><dd>${row ? bilingual(row, ui) : "—"}</dd></div>`;
  }).join("");
  return `
    <header class="entry-document-header">
      <div><h2>${ui.escape(profile.name || "FM Solutions")}</h2><p>${kindName} ${urdu(def.urdu, ui)}</p></div>
      <div class="entry-document-ref"><strong>${ui.escape(record.number)}</strong>${statusMarkup(record)}</div>
    </header>
    <dl class="entry-details-grid entry-document-meta">
      <div><dt>Date</dt><dd>${ui.escape(ui.date(record.date))}</dd></div>
      ${party ? `<div><dt>Account</dt><dd>${bilingual(party, ui)}</dd></div>` : ""}
      ${record.reference ? `<div><dt>Reference</dt><dd dir="auto">${ui.escape(record.reference)}</dd></div>` : ""}
      ${record.dueDate ? `<div><dt>Due date</dt><dd>${ui.escape(ui.date(record.dueDate))}</dd></div>` : ""}
      ${ledgerFields}
      ${record.quantity ? `<div><dt>Quantity</dt><dd>${ui.escape(record.quantity)}</dd></div><div><dt>Rate</dt><dd>${amount(record.rate)}</dd></div>` : ""}
      ${record.openingType ? `<div><dt>Balance side</dt><dd>${ui.escape(record.openingType)}</dd></div>` : ""}
    </dl>
    ${lineRows.length ? `<div class="table-wrap"><table class="data-table entry-table entry-document-lines">
      <thead><tr><th scope="col">Item</th><th scope="col">Qty</th><th scope="col">Rate</th><th scope="col">Discount</th><th scope="col">Tax</th><th scope="col">Amount</th></tr></thead>
      <tbody>${lineRows.join("")}</tbody></table></div>` : ""}
    <div class="entry-document-totals">
      ${record.discount > 0 ? `<div><span>Invoice discount</span><strong>${amount(record.discount)}</strong></div>` : ""}
      <div><span>Total</span><strong>${amount(record.total ?? record.amount ?? 0)}</strong></div>
      ${record.paid > 0 ? `<div><span>Paid</span><strong>${amount(record.paid)}</strong></div>` : ""}
      ${record.balance > 0 ? `<div><span>Balance</span><strong>${amount(record.balance)}</strong></div>` : ""}
    </div>
    ${record.narration ? `<p class="entry-document-note"><strong>Narration</strong><br><span dir="auto">${ui.escape(record.narration)}</span></p>` : ""}`;
}

function renderDocument(section, kind, ui) {
  const trade = tradeTypes[kind];
  const def = trade || journalTypes[kind];
  const allowed = action => ui.demo.can(section, action);
  const page = makePage(ui, {
    title: def.title, nameUrdu: def.urdu, description: def.description, section: trade ? "Inventory" : "Financial transactions",
    actions: button(ui, "browse", "Saved records", "history", "secondary", "F2")
  });
  const entry = document.createElement("div");
  entry.className = "entry-document";
  page.append(entry);
  let editing = null;
  let lastSaved = null;
  let editor = {};

  const initialRows = record => Array.isArray(record.lines) ? record.lines : [];
  const docList = () => ui.demo.documents(kind).slice().sort((a, b) =>
    String(b.date).localeCompare(String(a.date)) || String(b.number).localeCompare(String(a.number), undefined, { numeric: true })
  );

  function print(record) {
    if (!record || !allowed("print")) return;
    const profile = ui.demo.profile();
    const format = ui.demo.settings().invoiceFormat === "compact" ? "compact" : "standard";
    const contact = [profile.address, profile.phone].filter(Boolean).map(ui.escape).join(" · ");
    const hasLogo = /^data:image\/(?:png|jpe?g|webp);base64,[A-Za-z0-9+/=]+$/.test(profile.logo || "");
    printRecord(`<div class="entry-print-${format}">${hasLogo ? `<img class="entry-print-logo" src="${ui.escape(profile.logo)}" alt="">` : ""}
      ${documentMarkup(ui, record, def)}${contact ? `<p class="entry-print-contact">${contact}</p>` : ""}</div>`);
  }

  async function cancel(record, onDone) {
    if (!record || record.status === "cancelled" || !allowed("delete")) return;
    const confirmed = await ui.confirm(`Cancel ${record.number}? This reverses its entries and keeps the voucher in the activity history.`, {
      title: "Cancel transaction", danger: true, confirmLabel: "Cancel transaction", cancelLabel: "Keep transaction"
    });
    if (!confirmed) return;
    try {
      ui.demo.cancelDocument(record.id);
      if (lastSaved?.id === record.id) lastSaved = docList().find(row => row.id === record.id);
      onDone?.();
      ui.toast(`${record.number} cancelled.`);
    } catch (error) {
      ui.toast(error.message, "error");
    }
  }

  function detail(record, parent) {
    const content = document.createElement("div");
    content.className = "entry-detail";
    const posted = record.status !== "cancelled";
    content.innerHTML = `
      ${documentMarkup(ui, record, def)}
      <div class="entry-modal-actions">
        ${posted && allowed("delete") ? button(ui, "remove", "Cancel transaction", "trash", "ghost", "F8") : ""}
        <div>
          ${allowed("print") ? button(ui, "print", "Print", "printer", "secondary", "F7") : ""}
          ${posted && allowed("edit") ? button(ui, "edit", "Edit transaction", "edit", "primary", "F4") : ""}
        </div>
      </div>`;
    const modal = ui.modal({ title: "Transaction details", content, wide: true });
    const actions = {
      remove: () => cancel(record, () => { modal.close(); parent?.render(); }),
      print: () => print(record),
      edit: async () => {
        if (!posted || !allowed("edit") || !await canReplaceDraft()) return;
        modal.close();
        parent?.close();
        renderEditor(record);
      }
    };
    content.addEventListener("click", event => actions[event.target.closest("[data-action]")?.dataset.action]?.());
    modal.dialog.addEventListener("keydown", event => {
      const action = { F4: "edit", F7: "print", F8: "remove" }[event.key];
      if (!action) return;
      event.preventDefault();
      event.stopPropagation();
      actions[action]();
    });
  }

  function browse() {
    const table = listTable(ui, {
      title: "Search saved transactions", searchPlaceholder: "Search by number, reference or party",
      columns: ["Number", "Date", "Account", "Amount", "Status", ""],
      rows: docList, value: record => record.id,
      initialQuery: ui.params?.query || "",
      match: (query, record) => findMatch(query, [record.number, record.reference, record.date, record.party, record.partyUr, record.narration]),
      row: record => `
        <td><span class="entry-ref">${ui.escape(record.number)}</span></td>
        <td>${ui.escape(ui.date(record.date))}</td>
        <td>${bilingual(documentParty(ui, record), ui)}</td>
        <td class="entry-number">${ui.escape(ui.money(record.total ?? record.amount ?? 0))}</td>
        <td>${statusMarkup(record)}</td>
        <td><button type="button" class="btn btn-ghost btn-icon entry-open-record" data-view-record aria-label="View transaction">${icon(ui, "chevron-right")}</button></td>`,
      onOpen: record => detail(record, { close: modal.close, render: table.render }),
      empty: "No matching transactions. Saved entries will appear here.",
      pageSize: 8
    });
    table.root.classList.add("entry-list-modal");
    table.input.setAttribute("autofocus", "");
    const modal = ui.modal({ title: `Saved ${trade ? "inventory transactions" : "vouchers"}`, content: table.root, wide: true });
    modal.dialog.addEventListener("keydown", async event => {
      if (event.key === "F2" || (event.ctrlKey && event.key.toLowerCase() === "f")) {
        event.preventDefault();
        event.stopPropagation();
        table.input.focus();
        table.input.select();
      }
      const record = table.selected();
      if (!record || !["F4", "F7", "F8"].includes(event.key)) return;
      event.preventDefault();
      event.stopPropagation();
      if (event.key === "F7") print(record);
      if (event.key === "F8") cancel(record, table.render);
      if (event.key === "F4" && allowed("edit") && record.status === "posted" && await canReplaceDraft()) {
        modal.close();
        renderEditor(record);
      }
    });
  }

  function renderEditor(existing = null) {
    editing = existing;
    ui.setDirty?.(false);
    if (!allowed(existing ? "edit" : "add")) {
      entry.innerHTML = `<div class="card empty entry-readonly"><p>Browse saved transactions for this section.</p>${button(ui, "browse", "Browse records", "history")}</div>`;
      editor = {};
      return;
    }

    const record = existing || { openingKind: "Party" };
    const settings = ui.demo.settings();
    const precision = Math.max(0, Math.min(4, Number(existing?.precision ?? settings.decimalPrecision ?? 2)));
    const step = Math.pow(10, -precision);
    const form = document.createElement("form");
    form.className = "entry-posting";
    const number = record.number || ui.demo.nextNumber(kind);
    const date = record.date || ui.today();
    let submitting = false;

    const currentWarehouse = record.warehouseId || rowsFor(ui, "warehouses")[0]?.id;
    const currentCash = record.cashId || rowsFor(ui, "settlement")[0]?.id;
    const lookup = (label, name, collection, value, options = {}) => lookupControl(
      ui, label, name, value, rowsFor(ui, collection, value), options
    );
    const tradeHeader = trade ? [
      lookup(trade.partyLabel, "partyId", trade.party, record.partyId, { required: kind !== "counter", placeholder: kind === "counter" ? "Walk-in customer" : "Search party accounts", title: trade.party === "suppliers" ? "Supplier accounts" : "Party accounts" }),
      lookup("Warehouse", "warehouseId", "warehouses", currentWarehouse, { required: true }),
      kind === "sale" ? entryField(ui, "Payment terms (days)", "terms", record.terms ?? settings.paymentTerms ?? 30, { required: true, type: "number", step: 1, max: 3650 }) : "",
      trade.returnType ? entryField(ui, `${trade.returnType} reference`, "reference", record.reference, { hint: "Enter the original invoice number if available." })
        : entryField(ui, "Reference number", "reference", record.reference)
    ].join("") : "";
    const lineTable = trade ? `
      <section class="card entry-form-card entry-line-card">
        <header class="entry-form-card-header"><div><h2>Items</h2>${kind === "sale" ? '<p class="muted">Select an item, then press Enter through quantity and rate.</p>' : ""}</div>
          ${button(ui, "line-add", "Add line", "plus", "secondary")}</header>
        <div class="table-wrap"><table class="data-table entry-table entry-lines">
          <thead><tr><th scope="col">Item</th><th scope="col">Quantity</th><th scope="col">Rate</th>
            ${trade.damaged ? "" : '<th scope="col">Discount</th><th scope="col">Tax %</th>'}<th scope="col">Amount</th><th scope="col"><span class="entry-visually-hidden">Remove line</span></th></tr></thead>
          <tbody data-lines></tbody></table></div>
      </section>` : "";
    const totals = trade ? `
      <section class="card entry-form-card entry-totals-card">
        <div class="entry-form-grid form-grid entry-settlement">
          ${trade.payment ? lookup("Cash or bank account", "cashId", "settlement", currentCash) : ""}
          ${trade.payment ? entryField(ui, trade.payment, "paid", record.paid ?? 0, { type: "number", step }) : ""}
          ${noteField(ui, record.narration)}
          <p class="inline-error entry-field-wide" data-calculation-error role="alert" hidden></p>
        </div>
        <div class="entry-totals">
          <div><span>Subtotal</span><strong data-total="subtotal"></strong></div>
          ${trade.damaged ? "" : `<div><label class="entry-totals-input"><span>Invoice discount</span>${textInput("discount", record.discount ?? 0, ui, `type="number" min="0" step="${step}" data-enter inputmode="decimal"`)}</label></div>
          <div><span>Line discounts</span><strong data-total="lineDiscount"></strong></div>
          <div><span>Tax</span><strong data-total="tax"></strong></div>`}
          <div class="entry-grand-total"><span>Grand total</span><strong data-total="total"></strong></div>
          ${trade.payment || kind === "sale" ? '<div><span>Remaining balance</span><strong data-total="balance"></strong></div>' : ""}
        </div>
      </section>` : "";
    form.innerHTML = `
      <section class="card entry-form-card">
        <header class="entry-form-card-header">
          <div><h2>${existing ? "Edit transaction" : trade ? "Invoice details" : "Voucher details"}</h2>
            ${existing ? `<p class="muted">Editing ${ui.escape(existing.number)}</p>` : ""}</div>
        </header>
        <div class="entry-form-grid form-grid entry-document-fields">
          ${entryField(ui, trade ? "Invoice number" : "Voucher number", "number", number, { readOnly: true })}
          ${entryField(ui, "Date", "date", formatEntryDate(date), { type: "date", required: true })}
          ${trade ? tradeHeader : journalFields(ui, kind, record, step)}
        </div>
      </section>
      ${lineTable}${totals}
      ${kind === "journal" ? '<div class="entry-journal-totals"><span>Debit <strong data-total="debit"></strong></span><span>Credit <strong data-total="credit"></strong></span></div>' : ""}
      <p class="inline-error entry-posting-error" role="alert" hidden></p>
      <div class="entry-form-actions">
        ${existing ? button(ui, "add", "Cancel edit", "arrow-left", "ghost") : allowed("add") ? button(ui, "add", "New entry", "plus", "ghost", "F3") : ""}
        <div>
          ${allowed("print") ? button(ui, "save-print", "Save and print", "printer") : ""}
          ${button(ui, "save", existing ? "Save changes" : (trade ? "Save invoice" : "Save voucher"), "check", "primary", "F6")}
        </div>
      </div>`;
    entry.replaceChildren(form);

    const get = name => form.elements.namedItem(name);
    const normalizeDateInput = input => {
      const shorthandDate = /^\d{6}$/.test(input.value) ? parseEntryDate(input.value) : "";
      if (shorthandDate) input.value = formatEntryDate(shorthandDate);
      input.setCustomValidity(entryDateError(input.value, ui.today()));
    };
    normalizeDateInput(get("date"));
    const lines = form.querySelector("[data-lines]");
    const errorLine = form.querySelector(".entry-posting-error");
    const selectLines = () => [...(lines?.children || [])];
    const openerName = () => get("openingKind")?.value;
    const sources = {
      partyId: trade?.party || "parties", cashId: "settlement", methodId: "methods",
      debitId: "ledger", creditId: "ledger", fromId: "settlement", toId: "settlement",
      warehouseId: "warehouses", itemId: "items"
    };

    function setSelection(control, row) {
      const input = control.querySelector("input");
      input.value = row.id;
      control.querySelector("[data-selection]").innerHTML = bilingual(row, ui);
      control.querySelector("button").removeAttribute("aria-invalid");
    }

    function pick(control) {
      const name = control.dataset.picker;
      const wrapper = control.closest("[data-lookup]");
      const input = wrapper.querySelector("input");
      const source = name === "accountId" ? openingLists[openerName()] : sources[name];
      const options = rowsFor(ui, source, input.value);
      if (kind === "counter" && name === "partyId") {
        options.unshift({ id: "", code: "—", name: "Walk-in customer", urdu: "کاؤنٹر خریدار" });
      }
      if (kind === "transfer" && name === "methodId") {
        options.unshift({ id: "", code: "—", name: "None" });
      }
      setupPicker(ui, {
        title: control.dataset.title,
        origin: control, current: input.value, items: options,
        hint: row => row.barcode || row.phone,
        describe: row => source === "items" ? `${row.stock ?? 0} in stock` : accountDescription(ui, row),
        select: row => {
          setSelection(wrapper, row);
          if (name === "partyId" && kind === "sale" && row.paymentTerms != null) get("terms").value = row.paymentTerms;
          if (kind === "opening" && name === "itemId") get("rate").value = row.purchasePrice || 0;
          trackDraft();
        },
        next: () => {
          if (name === "partyId" && kind === "sale") return selectLines()[0]?.querySelector("[data-line-item]");
          const controls = tabFields(form);
          return controls[controls.indexOf(control) + 1] || control;
        }
      });
    }

    function lineRows() {
      return selectLines().map(row => {
        const value = name => row.querySelector(`[data-line-input="${name}"]`)?.value ?? "";
        return {
          itemId: row.dataset.itemId || "",
          quantity: Number(value("quantity")), rate: Number(value("rate")),
          discount: Number(value("discount")), tax: Number(value("tax"))
        };
      }).filter(row => row.itemId);
    }

    function setTotal(name, value) {
      const target = form.querySelector(`[data-total="${name}"]`);
      if (target) target.textContent = ui.money(value || 0);
    }

    function updateTotals() {
      if (!trade) {
        setTotal("debit", Number(get("amount")?.value || 0));
        setTotal("credit", Number(get("amount")?.value || 0));
        return;
      }
      let sums;
      const issue = form.querySelector("[data-calculation-error]");
      try {
        sums = calculateDocumentTotals({ lines: lineRows(), discount: Number(get("discount")?.value || 0) }, precision);
        errors(issue, "");
      } catch (error) {
        errors(issue, error.message);
        sums = { subtotal: 0, lineDiscount: 0, tax: 0, total: 0, lines: [] };
      }
      const paidControl = get("paid");
      if (kind === "counter" && !paidControl.dataset.manual) paidControl.value = sums.total.toFixed(precision);
      for (const name of ["subtotal", "lineDiscount", "tax", "total"]) setTotal(name, sums[name]);
      setTotal("balance", Math.max(0, sums.total - Number(paidControl?.value || 0)));
      let index = 0;
      for (const row of selectLines()) {
        const totalCell = row.querySelector("[data-line-total]");
        if (row.dataset.itemId) {
          totalCell.textContent = ui.money(sums.lines[index]?.total || 0);
          index += 1;
        } else totalCell.textContent = "—";
      }
    }

    function addLine(line = {}, focus = false) {
      const item = rowsFor(ui, "items", line.itemId).find(row => String(row.id) === String(line.itemId));
      const selected = Boolean(line.itemId);
      const row = document.createElement("tr");
      row.dataset.itemId = line.itemId || "";
      const label = item ? bilingual(item, ui) : '<span class="muted">Search items</span>';
      const valueInput = (key, placeholder, step, min = 0, max, required = false) => `
        <input type="number" inputmode="${["quantity", "rate"].includes(key) ? "numeric" : "decimal"}" data-enter data-line-input="${key}" aria-label="${placeholder}" min="${min}" step="${step}" ${max != null ? `max="${max}"` : ""} ${required ? "required" : ""} ${selected ? "" : "disabled"}
          value="${ui.escape(line[key] ?? "")}" placeholder="${placeholder === "Quantity" ? "0" : "0.00"}">`;
      row.innerHTML = `<td><button type="button" class="entry-select" data-enter data-line-item aria-label="Select item" aria-haspopup="dialog"><span data-selection>${label}</span>${icon(ui, "search", 16)}</button></td>
        <td>${valueInput("quantity", "Quantity", 1, 1, null, true)}</td>
        <td>${valueInput("rate", "Rate", 1, 0, null, true)}</td>
        ${trade.damaged ? "" : `<td>${valueInput("discount", "Line discount", step)}</td><td>${valueInput("tax", "Tax percent", 0.0001, 0, 100)}</td>`}
        <td class="entry-line-amount" data-line-total>—</td>
        <td><button type="button" class="btn btn-ghost btn-icon" data-action="line-remove" aria-label="Remove item line" ${selected ? "" : "disabled"}>${icon(ui, "x", 16)}</button></td>`;
      lines.append(row);
      if (focus) row.querySelector("[data-line-item]").focus();
      return row;
    }

    function nextLine(row) {
      let next = row.nextElementSibling;
      if (!next) next = addLine();
      next.querySelector("[data-line-item]").focus();
    }

    function pickItem(control) {
      const row = control.closest("tr");
      const candidates = rowsFor(ui, "items", row.dataset.itemId);
      const quantity = row.querySelector('[data-line-input="quantity"]');
      const rate = row.querySelector('[data-line-input="rate"]');
      setupPicker(ui, {
        title: "Item list", nameUrdu: "اشیاء کی فہرست", origin: control, current: row.dataset.itemId, items: candidates,
        hint: item => item.barcode,
        describe: item => {
          const warehouse = get("warehouseId").value;
          const damaged = Boolean(trade.damaged);
          const stock = warehouse && ui.demo.stockFor
            ? ui.demo.stockFor(item.id, warehouse, damaged)
            : item.stock;
          return `${stock ?? 0} ${damaged ? "damaged" : "in stock"}`;
        },
        meta: item => ui.money(item[`${trade.rate}Price`] ?? 0),
        select: item => {
          const changed = row.dataset.itemId !== String(item.id);
          row.dataset.itemId = item.id;
          control.querySelector("[data-selection]").innerHTML = bilingual(item, ui);
          for (const input of row.querySelectorAll("[data-line-input], [data-action='line-remove']")) input.disabled = false;
          if (changed) {
            quantity.value = "1";
            const manual = settings[`${trade.rate}PriceDefault`] === "manual";
            rate.value = manual ? "" : item[`${trade.rate}Price`] ?? 0;
            for (const input of row.querySelectorAll('[data-line-input="discount"], [data-line-input="tax"]')) input.value = "";
          }
          updateTotals();
          trackDraft();
          requestAnimationFrame(() => quantity.select());
        },
        next: quantity
      });
    }

    function save(withPrint = false) {
      if (submitting) return;
      errors(errorLine, "");
      const dateValue = parseEntryDate(get("date").value);
      normalizeDateInput(get("date"));
      if (!form.reportValidity()) return;
      const missing = [...form.querySelectorAll("[data-required]")].find(wrapper => !wrapper.querySelector("input").value);
      if (missing) {
        const control = missing.querySelector("button");
        control.setAttribute("aria-invalid", "true");
        control.focus();
        errors(errorLine, `Choose ${control.dataset.title.toLowerCase()} before saving.`);
        return;
      }
      const data = Object.fromEntries(new FormData(form));
      data.date = dateValue;
      if (existing) data.id = existing.id;
      delete data.number;
      for (const key of ["amount", "paid", "discount", "quantity", "rate", "terms"]) {
        if (key in data) data[key] = Number(data[key]);
      }
      if (trade) {
        data.lines = lineRows();
        if (!data.lines.length) {
          errors(errorLine, "Add an item before saving this transaction.");
          selectLines()[0].querySelector("[data-line-item]").focus();
          return;
        }
      }
      if (kind === "opening") {
        const resetFields = data.openingKind === "Inventory" ? ["accountId", "amount", "openingType"]
          : ["itemId", "warehouseId", "quantity", "rate"];
        for (const name of resetFields) data[name] = "";
      }
      submitting = true;
      try {
        lastSaved = ui.demo.saveDocument(kind, data);
        ui.toast(`${lastSaved.number} saved.`);
        renderEditor();
        if (withPrint) print(lastSaved);
      } catch (error) {
        errors(errorLine, error.message);
        submitting = false;
      }
    }

    if (trade) {
      for (const line of initialRows(record)) addLine(line);
      addLine();
      if (existing && get("paid")) get("paid").dataset.manual = "true";
    }
    const handlers = {
      add: () => newEntry(),
      "line-add": () => {
        const available = selectLines().find(row => !row.dataset.itemId);
        if (available) available.querySelector("[data-line-item]").focus();
        else addLine({}, true);
      },
      "line-remove": target => {
        const line = target.closest("tr");
        const next = line.nextElementSibling || line.previousElementSibling;
        line.remove();
        if (!next) addLine({}, true);
        else next.querySelector("[data-line-item]").focus();
        updateTotals();
        trackDraft();
      },
      save: () => save(),
      "save-print": () => save(true)
    };
    form.addEventListener("click", event => {
      const control = event.target.closest("[data-picker], [data-line-item], [data-action]");
      if (!control) return;
      if (control.hasAttribute("data-line-item")) pickItem(control);
      else if (control.hasAttribute("data-picker")) pick(control);
      else handlers[control.dataset.action]?.(control);
    });
    form.addEventListener("focusin", event => {
      if (event.target.matches('[data-line-input="rate"]') || (trade && event.target.name === "date")) event.target.select();
    });
    form.addEventListener("click", event => {
      if (event.target.matches('[data-line-input="rate"]') || (trade && event.target.name === "date")) event.target.select();
    });
    form.addEventListener("input", event => {
      if (event.target.name === "paid") event.target.dataset.manual = "true";
      if (event.target.name === "date") normalizeDateInput(event.target);
      updateTotals();
      trackDraft();
    });
    form.addEventListener("blur", event => {
      if (event.target.name !== "date") return;
      normalizeDateInput(event.target);
      trackDraft();
    }, true);
    form.addEventListener("change", event => {
      if (event.target.name === "openingKind") {
        form.querySelector("[data-opening-fields]").innerHTML = openingFields(ui, event.target.value, {}, step);
      }
      trackDraft();
    });
    form.addEventListener("keydown", event => {
      if (event.key !== "Enter" || event.isComposing || event.shiftKey) return;
      const input = event.target;
      if (trade && input.matches('[data-line-input="rate"], [data-line-input="tax"]')) {
        event.preventDefault();
        if (input.reportValidity()) nextLine(input.closest("tr"));
        return;
      }
      enterAdvance(event, form, save);
    });
    form.addEventListener("submit", event => { event.preventDefault(); save(); });
    updateTotals();
    function trackDraft() {
      if (editor.form === form) ui.setDirty?.(editor.original !== editor.data());
    }

    editor = {
      form, save, data: () => JSON.stringify([
        [...new FormData(form)].filter(([key]) => !["date", "number", "paid"].includes(key)),
        get("date")?.value && get("date")?.value !== formatEntryDate(date) ? get("date")?.value : "",
        get("paid")?.dataset.manual ? get("paid").value : "", lineRows()
      ]),
      search: () => {
        const control = document.activeElement;
        if (form.contains(control) && control.matches("[data-line-item], [data-picker]")) control.click();
        else browse();
      }
    };
    editor.original = editor.data();
    requestAnimationFrame(() => {
      if (!form.isConnected || document.querySelector("dialog[open]")) return;
      get("date")?.focus({ preventScroll: true });
    });
  }

  function canReplaceDraft() {
    if (editor.original === editor.data?.()) return true;
    return ui.confirm("Discard the changes to the current transaction?", {
      title: "Discard changes", confirmLabel: "Discard changes", cancelLabel: "Keep editing"
    });
  }

  async function newEntry() {
    if ((editing || allowed("add")) && await canReplaceDraft()) renderEditor();
  }

  const actions = {
    browse,
    search: () => editor.search ? editor.search() : browse(),
    add: newEntry,
    edit: async () => {
      if (lastSaved?.status === "posted" && allowed("edit")) {
        if (await canReplaceDraft()) renderEditor(lastSaved);
      } else browse();
    },
    refresh: () => { if (editor.original === editor.data?.()) renderEditor(editing); else updatePreviewNotice(); },
    save: () => editor.save?.(),
    print: () => lastSaved ? print(lastSaved) : ui.toast("Open a saved transaction to print it.", "info"),
    remove: () => lastSaved ? cancel(lastSaved) : browse()
  };
  function updatePreviewNotice() {
    ui.toast("Your entry is open. Save it or start a new entry to refresh its values.", "info");
  }
  page.addEventListener("click", event => {
    if (event.target.closest('[data-action="browse"]')) browse();
  });
  ui.setActions(actions);
  renderEditor();

  const id = ui.params?.id || ui.params?.documentId;
  if (id || ui.params?.number) {
    const record = docList().find(row => String(row.id) === String(id) || row.number === ui.params.number);
    if (record) requestAnimationFrame(() => { if (page.isConnected) detail(record); });
  } else if (ui.params?.query) {
    requestAnimationFrame(() => { if (page.isConnected) browse(); });
  }
  return page;
}

export function renderEntries(route, ui) {
  const [section, kind] = route.split(".");
  if (!kind) return catalog(section, ui);
  if (section === "coding" && masterDefinitions[kind]) return renderMaster(kind, ui);
  if (section === "financial" && journalTypes[kind] || section === "inventory" && tradeTypes[kind]) {
    return renderDocument(section, kind, ui);
  }
  return catalog(section, ui);
}
