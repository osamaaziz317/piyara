import {
  bilingual, button, enterAdvance, errors, field, findMatch, icon, listTable,
  makePage, optionList, printRecord, textInput, urdu
} from "./entry-ui.js";

export const masterGroups = [
  {
    name: "Accounts and parties",
    keys: ["parties", "customers", "suppliers", "groups", "cash", "banks", "methods", "salesmen"]
  },
  {
    name: "Items and locations",
    keys: ["items", "categories", "units", "warehouses", "cities"]
  }
];

export const masterDefinitions = {
  parties: { title: "Party accounts", singular: "party account", urdu: "پارٹی اکاؤنٹس", icon: "users", description: "Manage customers, suppliers and ledger accounts.", type: "party" },
  items: { title: "Items", singular: "item", urdu: "اشیاء", icon: "package", description: "Maintain products, rates, barcodes and minimum stock levels.", type: "item" },
  cities: { title: "Cities", singular: "city", urdu: "شہر", icon: "map-pin", description: "Link parties to bilingual city records by city code.", type: "city" },
  categories: { title: "Item categories", singular: "item category", urdu: "اشیاء کی اقسام", icon: "layers", description: "Group related items for inventory entry and review." },
  units: { title: "Units of measurement", singular: "unit", urdu: "پیمائش کی اکائیاں", icon: "box", description: "Set the units your staff use for buying, counting and selling." },
  groups: { title: "Account groups", singular: "account group", urdu: "اکاؤنٹ گروپس", icon: "layers", description: "Group accounts for ledger review and trial balances." },
  warehouses: { title: "Warehouses", singular: "warehouse", urdu: "گودام", icon: "building", description: "Manage locations where your business holds stock." },
  cash: { title: "Cash accounts", singular: "cash account", urdu: "نقد اکاؤنٹس", icon: "wallet", description: "Manage the cash accounts available for vouchers." },
  banks: { title: "Banks", singular: "bank", urdu: "بینک", icon: "building", description: "Manage bank accounts for receipts, payments and transfers." },
  methods: { title: "Payment methods", singular: "payment method", urdu: "ادائیگی کے طریقے", icon: "credit-card", description: "Maintain the payment methods available on vouchers." },
  salesmen: { title: "Salesmen", singular: "salesman", urdu: "سیلز مین", icon: "users", description: "Maintain records for your sales staff." },
  suppliers: { title: "Suppliers", singular: "supplier", urdu: "سپلائرز", icon: "truck", description: "Maintain supplier accounts, contact details and terms.", type: "party", accountType: "Supplier" },
  customers: { title: "Customers", singular: "customer", urdu: "گاہک", icon: "users", description: "Maintain customer accounts, credit limits and terms.", type: "party", accountType: "Customer" }
};

const accountTypes = ["Customer", "Supplier", "Expense", "Income", "Asset", "Liability", "Equity", "Other"];
const balanceTypes = ["Debit", "Credit"];

function choices(values) {
  return values.map(value => ({ id: value, name: value }));
}

function lookup(ui, kind, current) {
  return ui.demo.list(kind).filter(row => row.active || String(row.id) === String(current));
}

function controlsFor(ui, kind, record = {}) {
  const def = masterDefinitions[kind];
  const settings = ui.demo.settings();
  const step = Math.pow(10, -Number(settings.decimalPrecision ?? 2));
  const input = (label, name, options = {}) => ({
    name, label, ...options,
    html: textInput(name, record[name] ?? options.value, ui,
      `data-enter ${options.required ? "required" : ""} ${options.extra || ""}`)
  });
  const select = (label, name, options, extra = {}) => ({
    name, label, ...extra, options,
    html: `<select name="${name}" data-enter${extra.required ? " required" : ""}>${optionList(options, record[name] ?? extra.value, ui, extra.placeholder)}</select>`
  });
  const notes = (label, name) => ({
    name, label, wide: true,
    html: `<textarea rows="2" name="${name}" data-enter maxlength="500">${ui.escape(record[name] ?? "")}</textarea>`
  });
  const money = (label, name, value = 0) => input(label, name, {
    value, number: true, extra: `type="number" min="0" step="${step}" inputmode="decimal"`
  });
  const quantity = (label, name) => input(label, name, {
    value: 0, number: true, extra: 'type="number" min="0" step="0.0001" inputmode="decimal"'
  });

  const name = input("English name", "name", { extra: 'maxlength="100" dir="auto"', required: true });
  const nameUrdu = input("Urdu name", "urdu", { extra: 'class="urdu" lang="ur" dir="rtl" maxlength="100"' });
  const status = {
    name: "active", label: "Status",
    html: `<select name="active" data-enter><option value="true"${record.active !== false ? " selected" : ""}>Active</option><option value="false"${record.active === false ? " selected" : ""}>Inactive</option></select>`
  };

  if (def.type === "party") {
    return [
      name, nameUrdu,
      input("Country", "country", { value: ui.demo.profile().country || "Pakistan", extra: 'maxlength="60"' }),
      input("Phone number", "phone", { extra: 'type="tel" maxlength="40" autocomplete="off"' }),
      {
        name: "cityCode", label: "City code",
        html: `${textInput("cityCode", record.cityCode, ui, 'data-enter list="entry-city-codes" maxlength="30" aria-describedby="entry-city-caption"')}
          <datalist id="entry-city-codes">${ui.demo.list("cities").filter(city => city.active || city.code === record.cityCode).map(city => `<option value="${ui.escape(city.code)}">${ui.escape(city.name)}${city.urdu ? ` · ${ui.escape(city.urdu)}` : ""}</option>`).join("")}</datalist>
          <small class="entry-field-hint" id="entry-city-caption"></small>`
      },
      input("Complete address", "address", { extra: 'maxlength="200" dir="auto"' }),
      def.accountType ? {
        name: "accountType", label: "Account type", value: def.accountType,
        html: textInput("accountType", def.accountType, ui, "readonly")
      } : select("Account type", "accountType", choices(accountTypes), { required: true, value: "Customer" }),
      select("Account group", "groupId", lookup(ui, "groups", record.groupId)),
      money("Opening balance", "openingBalance"),
      select("Opening balance type", "openingType", choices(balanceTypes), { value: def.accountType === "Supplier" ? "Credit" : "Debit", required: true }),
      money("Credit limit", "creditLimit", settings.creditLimit ?? 0),
      input("Payment terms (days)", "paymentTerms", {
        value: settings.paymentTerms ?? 30, number: true, extra: 'type="number" min="0" max="3650" step="1" inputmode="numeric"'
      }),
      notes("Remarks", "remarks"), status
    ];
  }
  if (def.type === "item") {
    return [
      name, nameUrdu,
      select("Category", "category", lookup(ui, "categories", record.category), { required: true }),
      select("Unit", "unit", lookup(ui, "units", record.unit), { required: true }),
      input("Purchase price", "purchasePrice", { value: 0, number: true, extra: 'type="number" min="0" step="0.0001" inputmode="decimal"' }),
      input("Sale price", "salePrice", { value: 0, number: true, extra: 'type="number" min="0" step="0.0001" inputmode="decimal"' }),
      quantity("Opening stock", "openingStock"), quantity("Minimum stock level", "minimumStock"),
      input("Barcode (optional)", "barcode", { extra: 'maxlength="60"' }), status,
      notes("Description", "description")
    ];
  }
  if (def.type === "city") {
    return [name, nameUrdu, input("Country", "country", {
      value: ui.demo.profile().country || "Pakistan", extra: 'maxlength="60"', required: true
    }), status];
  }
  return [name, nameUrdu, status];
}

function masterColumns(kind, ui) {
  const def = masterDefinitions[kind];
  const status = item => `<span class="chip entry-status ${item.active ? "entry-status-green" : "entry-status-gray"}">${item.active ? "Active" : "Inactive"}</span>`;
  const related = (section, id) => ui.demo.list(section).find(record => String(record.id) === String(id))?.name;
  const balance = value => value ? `${ui.money(Math.abs(value))} ${value < 0 ? "Cr" : "Dr"}` : ui.money(0);
  const columns = [
    { label: "Code", cell: item => `<span class="entry-ref">${ui.escape(item.code)}</span>` },
    { label: "Name", cell: item => bilingual(item, ui) }
  ];
  if (def.type === "party") {
    columns.push(
      { label: "Type", cell: item => ui.escape(item.accountType) },
      { label: "Phone", cell: item => ui.escape(item.phone || "—") }
    );
    if (ui.demo.can("coding", "financial")) {
      columns.push({ label: "Balance", cell: item => `<span class="entry-number">${ui.escape(balance(item.balance))}</span>` });
    }
  } else if (def.type === "item") {
    columns.push(
      { label: "Category", cell: item => ui.escape(related("categories", item.category) || "—") },
      { label: "Stock", cell: item => `<span class="entry-number${item.stock < item.minimumStock ? " entry-low" : ""}">${ui.escape(item.stock ?? item.openingStock ?? 0)} ${ui.escape(related("units", item.unit) || "")}</span>` },
      { label: "Sale price", cell: item => `<span class="entry-number">${ui.escape(ui.money(item.salePrice))}</span>` }
    );
  } else if (def.type === "city") {
    columns.push({ label: "Country", cell: item => ui.escape(item.country || "—") });
  }
  columns.push(
    { label: "Status", cell: status },
    { label: "", cell: () => `<button type="button" class="btn btn-ghost btn-icon entry-open-record" data-view-record aria-label="View record">${icon(ui, "chevron-right")}</button>` }
  );
  return columns;
}

function connectCityField(form, ui) {
  const city = form.elements.namedItem("cityCode");
  if (!city) return;
  const caption = form.querySelector("#entry-city-caption");
  const cities = ui.demo.list("cities");
  function update() {
    const code = city.value.trim().toLocaleUpperCase();
    const found = cities.find(row => code === row.code.toLocaleUpperCase() && (row.active || row.code === city.defaultValue));
    caption.innerHTML = found ? `${ui.escape(found.name)} ${urdu(found.urdu, ui)}` : code ? "Choose an existing city code." : "";
    city.setCustomValidity(code && !found ? "Choose a city code from the list." : "");
    return found;
  }
  city.addEventListener("input", update);
  city.addEventListener("change", () => {
    const found = update();
    if (found) city.value = found.code;
  });
  update();
}

export function renderMaster(kind, ui) {
  const def = masterDefinitions[kind];
  const allowed = action => ui.demo.can("coding", action);
  const page = makePage(ui, {
    title: def.title, nameUrdu: def.urdu, section: "Coding", description: def.description,
    actions: allowed("add") ? button(ui, "add", `Add ${def.singular}`, "plus", "primary", "F3") : ""
  });
  let selected = null;
  let opened = null;
  const cols = masterColumns(kind, ui);
  const showLowStock = kind === "items";
  const editButton = allowed("edit") ? button(ui, "edit", "Edit", "edit", "ghost", "F4") : "";
  const table = listTable(ui, {
    columns: cols.map(col => col.label), rows: () => ui.demo.list(kind), value: row => row.id,
    initialQuery: ui.params?.query || ui.params?.code || "",
    searchPlaceholder: "Search by code, English or Urdu name",
    match: (query, row) => findMatch(query, [row.code, row.name, row.urdu, row.phone, row.country, row.barcode, row.accountType]),
    row: row => cols.map(col => `<td>${col.cell(row)}</td>`).join(""),
    extraToolbar: `<label class="entry-filter"><span class="entry-visually-hidden">Status</span><select name="status" data-table-filter>
        <option value="">All statuses</option><option value="active">Active</option><option value="inactive">Inactive</option>
      </select></label>
      ${showLowStock ? `<label class="entry-filter"><span class="entry-visually-hidden">Stock level</span><select name="stock" data-table-filter>
        <option value="">All stock levels</option><option value="low"${ui.params?.lowStock ? " selected" : ""}>Low stock</option>
      </select></label>` : ""}
      <div class="entry-table-actions">${button(ui, "view", "View", "eye", "ghost")}${editButton}</div>`,
    extraFilter: (root, row) => {
      const status = root.querySelector('[name="status"]').value;
      const stock = root.querySelector('[name="stock"]')?.value;
      return (!status || row.active === (status === "active")) && (!stock || row.stock < row.minimumStock);
    },
    onSelect: record => {
      selected = record;
      for (const control of page.querySelectorAll('[data-action="view"], [data-action="edit"]')) control.disabled = !record;
    },
    onOpen: record => open(record, "view")
  });
  page.append(table.root);
  for (const control of table.root.querySelectorAll("[data-action]")) control.disabled = true;

  function print(record) {
    if (!record || !allowed("print")) return;
    const attributes = visibleAttributes(record);
    printRecord(`<header><h1>${ui.escape(ui.demo.profile().name || "FM Solutions")}</h1><p>${ui.escape(def.title)}</p></header>
      <h2>${ui.escape(record.code)} · ${bilingual(record, ui)}</h2><dl>
      ${attributes.map(attribute => `<div><dt>${attribute.label}</dt><dd>${valueMarkup(attribute, record)}</dd></div>`).join("")}</dl>`);
  }

  function valueMarkup(attribute, record) {
    const value = record[attribute.name];
    if (attribute.name === "active") return record.active ? "Active" : "Inactive";
    if (attribute.name === "urdu") return urdu(value, ui) || "—";
    if (["openingBalance", "creditLimit", "purchasePrice", "salePrice"].includes(attribute.name)) {
      return ui.escape(ui.money(value || 0));
    }
    return ui.escape(attribute.options?.find(option => option.id === value)?.name || value || (value === 0 ? "0" : "—"));
  }

  function visibleAttributes(record) {
    const attributes = controlsFor(ui, kind, record);
    if (allowed("financial")) return attributes;
    const confidential = new Set(["openingBalance", "openingType", "creditLimit", "purchasePrice"]);
    return attributes.filter(attribute => !confidential.has(attribute.name));
  }

  async function remove(record) {
    if (!record || !allowed("delete")) return;
    const confirmed = await ui.confirm(`Delete ${record.name} (${record.code})?`, { title: "Delete master record", danger: true, confirmLabel: "Delete record" });
    if (!confirmed) return;
    try {
      ui.demo.removeMaster(kind, record.id);
      opened?.close();
      table.render();
      ui.toast(`${record.name} deleted.`);
    } catch (error) {
      ui.toast(error.message, "error");
    }
  }

  function open(record, mode) {
    if (mode === "create" && !allowed("add")) return;
    if (mode === "edit" && (!record || !allowed("edit"))) return;
    if (mode === "view" && !record) return;
    const view = mode === "view";
    const title = view ? record.name : mode === "edit" ? `Edit ${def.singular}` : `Add ${def.singular}`;
    const content = document.createElement(view ? "div" : "form");
    content.className = view ? "entry-detail" : "entry-editor";
    const attributes = view ? visibleAttributes(record) : controlsFor(ui, kind, record ?? {});
    if (view) {
      content.innerHTML = `<div class="entry-detail-top"><span class="entry-ref">${ui.escape(record.code)}</span>${urdu(record.urdu, ui)}</div>
        <dl class="entry-details-grid">
          ${attributes.filter(attribute => !["name", "urdu"].includes(attribute.name)).map(attribute => `
            <div${attribute.wide ? ' class="entry-field-wide"' : ""}><dt>${attribute.label}</dt><dd dir="auto">${valueMarkup(attribute, record)}</dd></div>`).join("")}
        </dl>
        <div class="entry-modal-actions">
          ${allowed("delete") ? button(ui, "remove", "Delete", "trash", "ghost", "F8") : ""}
          <div>
            ${allowed("print") ? button(ui, "print", "Print", "printer", "secondary", "F7") : ""}
            ${allowed("edit") ? button(ui, "edit", "Edit record", "edit", "primary", "F4") : ""}
          </div>
        </div>`;
    } else {
      const recordCode = record?.code || ui.demo.nextCode(kind);
      const code = field("Code", textInput("code", recordCode, ui, record ? "readonly" : 'required maxlength="30" aria-describedby="entry-code-caption"'), { required: true });
      content.innerHTML = `<div class="entry-editor-code">${code}${record ? "" : '<small id="entry-code-caption" class="muted">Use the suggested code or enter your own.</small>'}</div>
        <div class="form-grid entry-form-grid">${attributes.map(attribute => field(attribute.label, attribute.html, attribute)).join("")}</div>
        <p class="inline-error" role="alert" hidden></p>
        <div class="entry-modal-actions">
          ${button(ui, "close", "Cancel", "", "secondary")}
          ${button(ui, "save", "Save record", "check", "primary", "F6")}
        </div>`;
      connectCityField(content, ui);
      content.elements.namedItem("name").setAttribute("autofocus", "");
    }

    const modal = ui.modal({ title, content, wide: true });
    opened = modal;
    modal.dialog.addEventListener("close", () => { if (opened === modal) opened = null; }, { once: true });

    function save() {
      const name = content.elements.namedItem("name");
      name.value = name.value.trim();
      if (!content.reportValidity()) return;
      const values = Object.fromEntries(new FormData(content));
      values.code = values.code.trim();
      for (const attr of attributes) {
        values[attr.name] = attr.number ? Number(values[attr.name]) : values[attr.name]?.trim();
      }
      values.active = values.active === "true";
      if (record) values.id = record.id;
      try {
        ui.demo.saveMaster(kind, values);
        ui.setDirty?.(false);
        modal.close();
        table.render();
        ui.toast(`${values.name} saved.`);
      } catch (error) {
        errors(content.querySelector(".inline-error"), error.message);
      }
    }

    const values = () => JSON.stringify([...new FormData(content)]);
    const original = view ? null : values();
    async function closeEditor() {
      if (!view && original !== values()) {
        const confirmed = await ui.confirm("Discard the changes to this master record?", {
          title: "Discard changes", confirmLabel: "Discard changes", cancelLabel: "Keep editing"
        });
        if (!confirmed) return;
      }
      if (!view) ui.setDirty?.(false);
      modal.close();
    }

    const actions = {
      close: closeEditor,
      edit: () => { modal.close(); open(record, "edit"); },
      remove: () => remove(record),
      print: () => print(record),
      save
    };
    content.addEventListener("click", event => actions[event.target.closest("[data-action]")?.dataset.action]?.());
    if (!view) {
      content.addEventListener("submit", event => { event.preventDefault(); save(); });
      content.addEventListener("keydown", event => enterAdvance(event, content, save));
      content.addEventListener("input", () => ui.setDirty?.(original !== values()));
      modal.dialog.addEventListener("cancel", event => {
        event.preventDefault();
        closeEditor();
      });
      modal.dialog.querySelector(".dialog-header button")?.addEventListener("click", event => {
        event.stopImmediatePropagation();
        closeEditor();
      }, true);
    }
    modal.dialog.addEventListener("keydown", event => {
      const saveKey = event.key === "F6" || (event.ctrlKey && event.key.toLowerCase() === "s");
      const handler = view
        ? { F4: actions.edit, F7: actions.print, F8: actions.remove }[event.key]
        : saveKey ? save : null;
      if (!handler) return;
      event.preventDefault();
      event.stopPropagation();
      handler();
    });
  }

  const actions = {
    add: () => open(null, "create"),
    view: () => open(selected, "view"),
    edit: () => open(selected, "edit"),
    search: () => { table.input.focus(); table.input.select(); },
    refresh: () => table.render(),
    print: () => print(selected),
    remove: () => remove(selected)
  };
  page.addEventListener("click", event => actions[event.target.closest("[data-action]")?.dataset.action]?.());
  page.addEventListener("fm:show", () => table.render());
  ui.setActions(actions);
  if (ui.params?.id) {
    const target = ui.demo.list(kind).find(record => String(record.id) === String(ui.params.id));
    if (target) requestAnimationFrame(() => { if (page.isConnected) open(target, "view"); });
  }
  if (ui.params?.mode === "add" || ui.params?.fresh) {
    requestAnimationFrame(() => { if (page.isConnected) open(null, "create"); });
  }
  return page;
}
