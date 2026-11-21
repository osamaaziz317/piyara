# Native audit browser

`bifma.audit.AuditService` provides administrator-only browsing of the native
`audit_events` table. It does not insert, update or delete events, business records,
grants, or users. Normal live authentication may refresh the existing session's
heartbeat. Native startup recovery remains subject to the shared service lifecycle
contract; construct the service outside database sessions.

```python
audit = AuditService(native_path, security=security_service)
choices = audit.selectors(actor)
page = audit.list_events(
    actor, company_id=None, action="", search="",
    date_from="2026-10-01", date_to="2026-10-31", offset=0, limit=100,
)
```

`security` is optional. Every call requires current administrator status and an
active, unexpired session. Ordinary company/module grants do not allow audit
access: the log includes global security, shared master and backup events.
Authorization, count, selected rows, and current label resolution share one native
transaction. There is no separate cached authorization decision.

## Filters, selectors and pages

`list_events` accepts only the keyword arguments shown above. It returns
`{"rows": [...], "total": int, "offset": int, "limit": int}`.

- `company_id=None` includes all companies and global events. A supplied canonical
  company UUID must exist; inactive companies remain valid for historical audit
  review. That filter excludes global events with a null company. Shared master
  changes and security events are often global, even when they affect work across
  multiple companies. This initial API has no separate global-only mode.
- Empty `action` accepts every action; otherwise the action must match exactly,
  including case. Action text is limited to 200 characters.
- `search` is a literal substring over event action, entity type, entity ID, and
  current actor username/display name. It is limited to 200 characters. `%`, `_`,
  backslashes and quotes have no wildcard meaning. SQLite's `lower` implements
  the case comparison; this does not claim Unicode-wide case folding. Search does
  not inspect details, resolved subject labels, or company names.
- Dates are optional, valid ISO `YYYY-MM-DD` values. They refer to **UTC calendar
  days**, independent of the desktop's local timezone. Start is inclusive at
  00:00:00 UTC; end is inclusive at 23:59:59 UTC. Start cannot follow end. Event
  times are whole Unix seconds, not invoice business dates. Date arithmetic uses
  integer day/second bounds rather than local-time conversions.
- `offset` must be a nonnegative integer within SQLite's signed integer range;
  `limit` must be an integer from 1 through 500. Booleans and floats are rejected.
  Order is `occurred_at DESC, rowid DESC, id DESC`, so equal timestamps follow
  reverse insertion order, regardless of UUID spelling. Count is over all rows
  matching the same filters. Empty pages retain that full count. Separate page
  requests may shift if new events arrive between requests.

`selectors(actor)` returns `companies` with `id`, `code`, `name`, `name_urdu`,
`is_active` (including inactive companies), and an ordered distinct list of
recorded exact action strings under `actions`. This call is also administrator-only.

## Returned event data

Each row preserves the event's `id`, `actor_id`, `company_id`, `action`,
`entity_type`, `entity_id`, integer `occurred_at`, and original `details_json` text.
Additional fields are:

| Fields | Meaning |
| --- | --- |
| `occurred_at_utc` | Explicit `YYYY-MM-DD HH:MM:SS UTC`; an explicit epoch-second fallback for an out-of-calendar timestamp |
| `actor_username`, `actor_name`, `actor_label` | Current username/display name; unknown/null actors fall back to ID or `System / unknown` |
| `company_code`, `company_name`, `company_name_urdu`, `company_is_active`, `company_label` | Current company identity; null company displays `Global` |
| `actor_label_source`, `company_label_source` | `current`, `identifier`, or `context` |
| `subject_label`, `subject_label_source` | Current resolved subject or an explicit identifier/context fallback |
| `details` | Parsed JSON object containing data only; no execution or interpretation of text as code |

Subject lookup is bounded to the requested page and uses fixed allowlisted tables
and safe label columns. It resolves documents to number/kind; users to
username/display name; companies, accounts, cities, units, products, product groups
and stores to code/name; and company/store memberships to company/store labels.
Unknown types or missing subjects remain visible by their identifiers. A document
or membership with a conflicting company is not mislabeled from another company.
Database events use the explicit `Native database` context label.

Current labels are **not historical identity snapshots**: a renamed account can
appear under its current name on an earlier audit event. The stored event itself
is unchanged. The service does not fetch or return password hashes, session tokens,
or credential records as display data. Existing event producers record action
metadata, changed field names, revisions and reasons, not credential values.
Stored free text is returned as data, including angle brackets or code-like text;
renderers must display it as text, not HTML or executable input.

`details_json` remains the original representation. The parsed `details` object
uses standard JSON-compatible scalar/list/object types. Unsupported nonfinite
numeric values or excessively nested/unparseable objects fail with `AuditError`
identifying the event, without including its contents in the error. No repair or
normalization is written back to the log. This browser is not a forensic guarantee
against direct modification of database files outside the application's managed
write and immutability rules.

Synthetic tests cover company/global filtering, UTC boundaries, stable paging,
literal search, inactive selectors, actual master/document/user audit producers,
current-label resolution and unknown/null fallbacks, data-only JSON handling,
ordinary-user denial, live administrator/session revocation, malformed inputs,
single-transaction consistency during a concurrent write, and unchanged stored
records apart from the normal session heartbeat. No audit export is implemented
in this scope.
