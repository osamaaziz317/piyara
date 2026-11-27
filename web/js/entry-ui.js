const PAGE_SIZE = 15;

export function urdu(value, ui) {
  return value ? `<span class="urdu entry-urdu" lang="ur" dir="rtl">${ui.escape(value)}</span>` : "";
}

export function bilingual(value, ui) {
  if (!value) return '<span class="muted">—</span>';
  return `<span class="entry-names"><strong>${ui.escape(value.name)}</strong>${urdu(value.urdu, ui)}</span>`;
}

export function field(label, control, options = {}) {
  const classes = ["field", options.wide ? "entry-field-wide" : "", options.className || ""].join(" ");
  return `<label class="${classes}"><span>${label}${options.required ? ' <span class="entry-required" aria-hidden="true">*</span>' : ""}</span>${control}</label>`;
}

export function optionList(rows, selected, ui, placeholder = "Select an option") {
  const options = rows.map(({ id, name, urdu: nameUrdu }) => {
    const label = nameUrdu ? `${name} · ${nameUrdu}` : name;
    return `<option value="${ui.escape(id)}"${String(id) === String(selected) ? " selected" : ""}>${ui.escape(label)}</option>`;
  });
  return `<option value="">${ui.escape(placeholder)}</option>${options.join("")}`;
}

export function textInput(name, value, ui, extra = "") {
  return `<input name="${name}" value="${ui.escape(value ?? "")}" ${extra}>`;
}

export function icon(ui, name, size = 17) {
  return ui.icon?.(name, size) ?? "";
}

export function makePage(ui, { title, description, actions = "", section, nameUrdu = "" }) {
  const page = document.createElement("section");
  page.className = "page entry-page";
  page.innerHTML = `
    <header class="page-header entry-heading">
      <div>
        <p class="eyebrow entry-eyebrow">${ui.escape(section)}</p>
        <h1>${ui.escape(title)}${urdu(nameUrdu, ui)}</h1>
        <p>${ui.escape(description)}</p>
      </div>
      ${actions && `<div class="entry-heading-actions">${actions}</div>`}
    </header>`;
  return page;
}

export function button(ui, action, title, name, style = "secondary", shortcut = "") {
  return `<button type="button" class="btn btn-${style}" data-action="${action}">${name ? icon(ui, name) : ""}<span>${ui.escape(title)}</span>${shortcut ? `<kbd>${shortcut}</kbd>` : ""}</button>`;
}

export function findMatch(query, values) {
  const keywords = query.trim().normalize("NFC").toLocaleLowerCase().split(/\s+/).filter(Boolean);
  if (!keywords.length) return true;
  const searchable = values.join(" ").normalize("NFC").toLocaleLowerCase();
  return keywords.every(keyword => searchable.includes(keyword));
}

export function errors(target, message) {
  target.hidden = !message;
  target.textContent = message || "";
}

export function tabFields(container) {
  return [...container.querySelectorAll("[data-enter]")].filter(control => (
    !control.disabled && !control.readOnly && control.getClientRects().length
  ));
}

export function enterAdvance(event, container, finalAction) {
  if (event.key !== "Enter" || event.defaultPrevented || event.isComposing || event.shiftKey) return;
  if (!event.target.matches("input[data-enter], select[data-enter], textarea[data-enter]")) return;
  const controls = tabFields(container);
  const current = controls.indexOf(event.target);
  if (current < 0) return;
  event.preventDefault();
  if (!event.target.reportValidity()) return;
  if (controls[current + 1]) {
    controls[current + 1].focus();
  } else {
    finalAction?.();
  }
}

export function listTable(ui, {
  title = "Search records", searchPlaceholder = "Search by name or code", initialQuery = "",
  columns, rows, value, match, row, onSelect, onOpen, extraFilter, extraToolbar = "",
  empty = "No records match your search.", pageSize = PAGE_SIZE
}) {
  const root = document.createElement("section");
  root.className = "card entry-list";
  root.innerHTML = `
    <div class="toolbar entry-toolbar">
      <label class="entry-search">
        ${icon(ui, "search")}
        <span class="entry-visually-hidden">${ui.escape(title)}</span>
        <input type="search" aria-label="${ui.escape(title)}" placeholder="${ui.escape(searchPlaceholder)}" autocomplete="off">
      </label>
      ${extraToolbar}
    </div>
    <div class="table-wrap">
      <table class="data-table entry-table">
        <thead><tr>${columns.map(text => `<th scope="col">${ui.escape(text)}</th>`).join("")}</tr></thead>
        <tbody></tbody>
      </table>
    </div>
    <div class="entry-list-footer">
      <span data-counter class="muted" aria-live="polite"></span>
      <div class="entry-pagination">
        <button type="button" class="btn btn-ghost" data-page="previous" aria-label="Previous page">${icon(ui, "chevron-left")}</button>
        <button type="button" class="btn btn-ghost" data-page="next" aria-label="Next page">${icon(ui, "chevron-right")}</button>
      </div>
    </div>`;
  const input = root.querySelector("input");
  const body = root.querySelector("tbody");
  const previous = root.querySelector('[data-page="previous"]');
  const next = root.querySelector('[data-page="next"]');
  const count = root.querySelector("[data-counter]");
  input.value = initialQuery;
  let page = 0;
  let selectedId = null;
  let visible = [];

  function select(index) {
    const selected = visible[index];
    if (!selected) return;
    selectedId = value(selected);
    for (const element of body.querySelectorAll("[data-row]")) {
      const active = Number(element.dataset.row) === index;
      element.classList.toggle("is-selected", active);
    }
    onSelect?.(selected);
  }

  function render(reset = false) {
    if (reset) page = 0;
    const data = rows().filter(record => match(input.value, record) && (!extraFilter || extraFilter(root, record)));
    const pages = Math.max(1, Math.ceil(data.length / pageSize));
    page = Math.min(page, pages - 1);
    const offset = page * pageSize;
    visible = data.slice(offset, offset + pageSize);
    const selected = visible.find(item => value(item) === selectedId);
    selectedId = selected ? value(selected) : null;
    onSelect?.(selected || null);
    body.innerHTML = visible.length ? visible.map((record, index) => `
      <tr tabindex="0" data-row="${index}"${value(record) === selectedId ? ' class="is-selected"' : ""}>${row(record)}</tr>
    `).join("") : `<tr><td class="entry-empty-cell" colspan="${columns.length}">${ui.escape(empty)}</td></tr>`;
    count.textContent = data.length
      ? `Showing ${offset + 1}–${offset + visible.length} of ${data.length}`
      : "0 records";
    previous.disabled = page === 0;
    next.disabled = page >= pages - 1;
  }

  input.addEventListener("input", () => render(true));
  root.addEventListener("change", event => {
    if (event.target.matches("[data-table-filter]")) render(true);
  });
  previous.addEventListener("click", () => { page -= 1; render(); });
  next.addEventListener("click", () => { page += 1; render(); });
  body.addEventListener("click", event => {
    const result = event.target.closest("[data-row]");
    if (!result) return;
    const index = Number(result.dataset.row);
    select(index);
    if (event.target.closest("[data-view-record]")) onOpen?.(visible[index]);
  });
  body.addEventListener("dblclick", event => {
    const result = event.target.closest("[data-row]");
    if (result && !event.target.closest("[data-view-record]")) onOpen?.(visible[Number(result.dataset.row)]);
  });
  body.addEventListener("focusin", event => {
    const result = event.target.closest("[data-row]");
    if (result) select(Number(result.dataset.row));
  });
  body.addEventListener("keydown", event => {
    const result = event.target.closest("[data-row]");
    if (!result || event.target.closest("button")) return;
    const index = Number(result.dataset.row);
    if (event.key === "Enter") {
      event.preventDefault();
      select(index);
      onOpen?.(visible[index]);
    } else if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      const target = index + (event.key === "ArrowDown" ? 1 : -1);
      body.querySelector(`[data-row="${target}"]`)?.focus();
    }
  });
  input.addEventListener("keydown", event => {
    if (event.key === "ArrowDown" || event.key === "Enter") {
      event.preventDefault();
      if (event.key === "Enter" && visible.length === 1) onOpen?.(visible[0]);
      else body.querySelector("[data-row]")?.focus();
    }
  });

  render();
  return { root, input, render, selected: () => visible.find(item => value(item) === selectedId) };
}

export function setupPicker(ui, {
  title, items, select, origin, next, current, hint, describe, meta, nameUrdu, search
}) {
  const content = document.createElement("div");
  content.className = "entry-picker";
  content.innerHTML = `
    <label class="entry-search">
      ${icon(ui, "search")}
      <input type="search" placeholder="${ui.escape(search || "Search by code, English or Urdu name")}" aria-label="${ui.escape(title)}" autocomplete="off" aria-controls="entry-picker-results" role="combobox" aria-autocomplete="list" aria-expanded="true" autofocus>
    </label>
    <div class="entry-picker-results" id="entry-picker-results" role="listbox" aria-label="${ui.escape(nameUrdu || title)}"></div>
    <div class="entry-picker-footer"><span class="muted" data-count aria-live="polite"></span><span><kbd>↑</kbd> <kbd>↓</kbd> Move <kbd>Enter</kbd> Select</span></div>`;
  const searchInput = content.querySelector("input");
  const results = content.querySelector("[role=listbox]");
  const tally = content.querySelector("[data-count]");
  let filtered = [];
  let active = 0;
  const modal = ui.modal({ title, content, wide: true });

  function mark(scroll = false) {
    const buttons = results.querySelectorAll("[data-index]");
    for (const [index, element] of buttons.entries()) {
      element.setAttribute("aria-selected", String(index === active));
    }
    const selected = buttons[active];
    if (selected) {
      searchInput.setAttribute("aria-activedescendant", selected.id);
      if (scroll) selected.scrollIntoView({ block: "nearest" });
    } else searchInput.removeAttribute("aria-activedescendant");
  }

  function render(initial = false) {
    const matches = items.filter(record => findMatch(searchInput.value, [
      record.code, record.name, record.urdu, hint?.(record)
    ]));
    filtered = matches.slice(0, 75);
    const currentIndex = initial ? filtered.findIndex(record => record.id === current) : -1;
    active = Math.max(0, currentIndex);
    results.innerHTML = filtered.length ? filtered.map((record, index) => `
      <div role="option" id="entry-picker-option-${index}" data-index="${index}" aria-selected="false">
        <div>
          <span class="entry-ref">${ui.escape(record.code)}</span>
          ${bilingual(record, ui)}
        </div>
        <div class="entry-picker-meta">
          ${describe ? `<span>${ui.escape(describe(record))}</span>` : ""}
          ${meta ? `<span class="muted">${ui.escape(meta(record))}</span>` : ""}
        </div>
      </div>`).join("") : '<p class="entry-picker-empty muted">No matching records.</p>';
    tally.textContent = matches.length > filtered.length ? `Showing 75 of ${matches.length} matches` : `${matches.length} result${matches.length === 1 ? "" : "s"}`;
    mark(initial);
  }

  function commit(index) {
    if (!filtered[index]) return;
    modal.dialog.addEventListener("close", () => {
      (typeof next === "function" ? next() : next || origin)?.focus();
    }, { once: true });
    modal.close();
    select(filtered[index]);
  }

  searchInput.addEventListener("input", () => render());
  content.addEventListener("keydown", event => {
    if (event.isComposing) return;
    if ((event.ctrlKey && event.key.toLowerCase() === "f") || event.key === "F2") {
      event.preventDefault();
      event.stopPropagation();
      searchInput.focus();
      searchInput.select();
    }
    if (!["ArrowUp", "ArrowDown", "Enter"].includes(event.key)) return;
    event.preventDefault();
    event.stopPropagation();
    if (event.key === "Enter") {
      commit(active);
    } else if (filtered.length) {
      active = Math.min(filtered.length - 1, Math.max(0, active + (event.key === "ArrowDown" ? 1 : -1)));
      mark(true);
    }
  });
  results.addEventListener("click", event => {
    const option = event.target.closest("[data-index]");
    if (option) commit(Number(option.dataset.index));
  });

  render(true);
}

export function printRecord(markup) {
  const existing = document.getElementById("entry-print");
  existing?.remove();
  const print = document.createElement("article");
  print.id = "entry-print";
  print.innerHTML = markup;
  document.body.append(print);
  window.addEventListener("afterprint", () => print.remove(), { once: true });
  window.print();
}
