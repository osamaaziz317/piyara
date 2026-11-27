export function secondaryPage(ui, title, description, actions = '') {
  const page = document.createElement('section');
  page.className = 'page secondary-page';
  page.innerHTML = `
    <header class="page-header">
      <div><h1>${ui.escape(title)}</h1><p>${ui.escape(description)}</p></div>
      ${actions ? `<div class="sp-page-actions">${actions}</div>` : ''}
    </header>`;
  return page;
}

export function options(ui, choices, selected) {
  return choices.map(([value, label]) => `
    <option value="${ui.escape(value)}" ${String(value) === String(selected) ? 'selected' : ''}>
      ${ui.escape(label)}
    </option>`).join('');
}

export function emptyRow(columns, message) {
  return `<tr><td colspan="${columns}" class="sp-empty-row muted">${message}</td></tr>`;
}

export function stats(ui, values) {
  return `<div class="sp-stat-grid">${values.map(([label, value, icon, detail]) => `
    <article class="card sp-stat">
      <span class="sp-stat-icon">${ui.icon(icon)}</span>
      <span class="sp-stat-label">${ui.escape(label)}</span>
      <strong>${ui.escape(value)}</strong>
      ${detail ? `<small>${ui.escape(detail)}</small>` : ''}
    </article>`).join('')}</div>`;
}

export function urdu(ui, value) {
  return value ? `<bdi class="urdu sp-urdu-name" lang="ur" dir="rtl">${ui.escape(value)}</bdi>` : '';
}
