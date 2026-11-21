# Native master maintenance

`bifma/masters.py` provides authenticated maintenance for the existing native
company/master tables. It operates only on an explicitly initialized, current
native database. It does not create, migrate or promote an archive/V2 file and
does not copy historical records or credentials.

## Service API

```python
service = MasterService(path, security=None, clock=time.time)
record = service.create(actor, entity, values)
suggested_code = service.next_product_code(actor)
product = service.create_product(actor, item_values)
record = service.update(actor, entity, record_id, values, expected_version)
record = service.get(actor, entity, record_id)
records = service.list_records(
    actor, entity, search="", active_only=False, limit=100, offset=0
)
choices = service.lookup(actor, company_id, module, action="read")
```

`actor` is a real `SecurityService` session or its opaque token, never merely a
user UUID or a user display object. Every operation checks current authorization
inside the same immediate native transaction used for its queries/mutations.
Revocations, disabling a user and company changes therefore apply immediately.
Authenticated reads may refresh the session's idle timestamp; they do not edit
masters, initialize schemas, grant privileges or perform migrations.

Create, automatic item-code preview/create, update, get and list require administrator status. This is an explicit
initial policy: these masters are shared between companies, so a company-scoped
`products/create` grant must not silently permit global product mutation.
Company setup, membership setup and account hierarchy changes follow the same
administrator-only policy. Granular delegated master administration needs its
own reviewed policy and is not inferred from legacy menu flags.

Results are dictionaries containing the persisted fields and an opaque `version`
fingerprint. `list_records` returns a list, with page size 1–500 and a nonnegative
offset. Search treats `%`, `_`, backslash and SQL-looking text literally. All
values use SQL parameters, and entity/field/query expressions come from fixed
allowlists. There is no delete API.

Normal `record_id` values are native UUID strings. A company/store membership has
the composite ID `(company_id, store_id)`; its returned `id` is that tuple. Its
key fields cannot be changed in an update. Create another membership explicitly
instead of moving the existing relationship to another company.

## Routine item entry

The Item form shows a read-only **Item code**, description, group, units and
prices. It has no party or sales/purchase/inventory-account dropdowns. A new
form calls `next_product_code(actor)` for a preview; this does not reserve the
code. Saving calls `create_product(actor, values)`, which allocates its actual
code, validates references, inserts the item and records the audit event in the
same authenticated write transaction. The returned dictionary includes the
saved code and the usual version fingerprint. Concurrent forms may display the
same preview; their saved codes are allocated independently under the lock.

Automatic creation rejects caller-supplied `code`, `sale_account_id`,
`purchase_account_id` and `inventory_account_id`, including explicit `None`.
Other item fields use the ordinary master validation. The next code is the
maximum ASCII-digit-only value among all current product codes and sealed
original `PUR_ITEM` mapping keys, plus one. Inactive products count too. Other
text codes remain unchanged and do not participate; existing leading zeros are
preserved in their records, while the new code is unpadded decimal text. No
numeric codes yields `1`; exceeding the existing 80-character code limit fails.
The supplied original sequence is exactly `1` through `6207`, so its first next
code is `6208`, subject to newer saved items. This is exact text arithmetic, not
SQLite integer casting or floating-point conversion.

The posting defaults must be one distinct, fully configured sales/purchase
pair across current products, including inactive ones. An entirely unmapped
item supplies no candidate. A partial mapping does not establish a pair, and
any non-null partial reference conflicting with the chosen pair prevents
automatic creation. Both selected accounts must still exist, be active,
postable and of kind `general`; customer, supplier, both, cash, bank and group
kinds are not accepted as these automatic item defaults. New inventory mapping
remains `None`. No account names, code prefixes, groups or financial
classifications are used to guess defaults. Missing, ambiguous or invalid setup
requires administrator review rather than a party selection in the Item form.

All 6,207 supplied original items use the same pair, including every populated
subgroup; this is observed configuration, not proof of an original form rule.
Routine edits call `update` while omitting code and account fields, preserving
their saved values. Generic explicit `create`/`update` retain their administrator
configuration APIs, including deliberately supplied codes and mappings. A
completely empty, unconfigured evaluation file therefore needs explicit
administrator setup before its first routine item can be created. No schema
migration or original-data rewrite is involved.

## Editable fields

`code` and `name` are required for all entities except memberships. Names may
include Urdu; `name_urdu` is optional. Codes remain text, preserve leading zeros
and case, and may be deliberately edited through the explicit administrator API
with concurrency protection. The routine Item form keeps its code read-only. IDs,
creation/update timestamps and returned version tokens are not caller-editable.

| Entity | Additional editable fields |
| --- | --- |
| `companies` | `fiscal_start`, `fiscal_end`, `cash_account_id`, `is_active`, and the optional contact fields below |
| `cities` | No additional fields |
| `accounts` | `parent_id`, `city_id`, `kind`, `classification`, `is_postable`, `is_active`, and the optional contact fields below |
| `units` | `is_active` |
| `product_groups` | `parent_id` |
| `products` | `group_id`, required `small_unit_id`, `large_unit_id`, `unit_factor`, `sale_rate`, `purchase_rate`, `purchase_account_id`, `sale_account_id`, `inventory_account_id`, `is_active` |
| `stores` | `is_active` |
| `company_stores` | Required `company_id`, required `store_id`, `is_active`; only activity is editable after creation |

Cities and product groups have no active flag in the current schema; the service
does not invent one or delete them.

### Optional contact and tax identifier fields

Native migration 3 adds the following nullable text fields. Company and account
create/edit/get/list results and authorized operational lookups include them.

| Field | Company | Account | Maximum characters |
| --- | --- | --- | ---: |
| `address` | Yes | Yes | 500 |
| `address_urdu` | Yes | No | 500 |
| `phone` | Yes | Yes | 80 |
| `fax` | Yes | No | 80 |
| `mobile` | No | Yes | 80 |
| `email` | Yes | Yes | 254 |
| `website` | Yes | Yes | 2,048 |
| `sales_tax_number` | Yes | Yes | 80 |
| `national_tax_number` | Yes | Yes | 80 |

Values remain opaque strings: leading zeros, letter case, punctuation and Urdu
are preserved. The service strips surrounding whitespace and normalizes NFC,
as it does for names. Omitted fields and explicit `None` become SQL NULL; a
blank or whitespace-only optional string clears the value. Embedded control or
surrogate characters and overlong values fail validation. Integers, floats,
booleans, bytes and other non-string inputs are rejected without coercion.
Addresses are single-line text under the same control-character rule.

These fields store supplied contact details and identifiers. They do not verify
phone numbers, email addresses, websites, tax registration or identifier formats,
and they do not calculate tax. No region-specific formatting or validation is
guessed. Source `CONFIG` and `CHART` field mappings are documented in
[NATIVE_DESIGN.md](NATIVE_DESIGN.md#migration-3-contact-and-tax-identifier-storage).
SMS credentials, aliases, alternate company names/addresses, default account
discounts and automatic tax rules are outside this migration.

Existing version-2 records gain NULL values only after an explicit verified
upgrade. No contact data is imported from the recovered archive. Already posted
document inputs, labels and snapshots are not rewritten by the migration or
later master edits. Master mutation remains administrator-only, with the same
version checks and field-name-only audit metadata as other edits.

## Validation and lifecycle rules

- Codes contain 1–80 characters and names 1–150. Text is stripped and NFC
  normalized; control characters and unsupported surrogate characters fail with
  a validation error. Codes are never converted through numeric types.
- Booleans accept Python booleans or integer 0/1, not strings or floats.
- Rates and conversion factors accept exact decimal strings, `Decimal` or
  integers. Floats and booleans are rejected. Values have at most 18 integer and
  6 effective fractional places; redundant trailing zeros normalize without
  rounding. Rates are nonnegative and factors positive.
- If the large unit is absent or identical to the small unit, the factor must
  equal 1. With distinct units the factor must be positive. A stricter physical
  “large unit” ordering is not guessed from missing original workflow evidence.
- An item's base unit cannot change once any company has posted document lines
  for it, including fully reversed history. This prevents aggregation across
  different physical units. Use a separate item for a different base unit.
  Draft-only items may change units, with draft resave/review required before
  posting. Large-pack factors may change because persisted quantities stay in
  the same base unit; issued snapshots retain their original conversion factor.
- Active products require active unit references and active/postable accounts
  for every mapping that is supplied. Account mappings may remain unset during
  setup; a transaction service must require the mappings needed for its action.
- Accounts may use native kinds `group`, `general`, `customer`, `supplier`,
  `both`, `cash` or `bank`. Classification is optionally `asset`, `liability`,
  `equity`, `income` or `expense`; no classification is inferred from a code.
- Group accounts are nonpostable. Account parents must be groups, and active
  accounts require active ancestors. An account with children cannot be changed
  into a leaf kind. Both account and product-group trees reject self-parenting
  and longer cycles, without assuming a new depth limit from the original menus.
- Company fiscal dates must both be unset or both valid ISO dates in
  chronological order. An unset period permits initial setup; posting services
  can require an actual configured period. A configured active company's cash
  account must be active, postable and of kind `cash`.
- Company/store memberships explicitly reference existing companies/stores.
  Enabling a membership requires both parents active. The same store can belong
  to multiple companies; creating a company never grants ordinary users access.

Deactivation keeps the record and all historical references. An account cannot
be deactivated while active child accounts, products or a company's active cash
configuration depend on it. A unit cannot be deactivated while an active product
uses it. Remove/reconfigure those active dependencies or deactivate dependents
first. Inactive records can retain inactive configurations; reactivation checks
their references again.

Companies and stores can be disabled even while their membership rows remain
configured. Such memberships are dormant: operational availability requires an
active company, an active store and an active membership together. No memberships
or grants are silently deleted or reassigned. Historical documents/journals are
not rewritten when a master is renamed, recoded or disabled.

## Operational dropdown contract

`lookup` accepts these permission modules: `sales`, `purchases`, `sales_returns`,
`purchase_returns`, `stock`, `journals`, `receipts`, `payments`,
`breakage_receive`, `breakage_dispatch`, `delivery_challans`, `stock_transfers`.
This agrees with the native posting modules. Passing arbitrary security modules
such as `users` does not expose business choices.

The requested action is checked independently; a caller with `create` but no
`read` permission may request `action="create"` for the entry form. A default
read lookup requires read permission. The returned mapping always has:

| Key | Contents |
| --- | --- |
| `companies` | Only the selected authorized active company |
| `accounts` | Shared active postable accounts |
| `products` | Shared active products with active units and valid active/postable supplied account mappings; missing mappings remain visible for staged setup |
| `stores` | Active stores with an active membership in the selected company |
| `units` | Shared active units |
| `groups` | Shared product groups |

Each choice includes its native identifiers, applicable rate/unit/mapping fields,
optional contact fields where applicable, and a version fingerprint. No user credentials, session records or other-company
configuration rows are returned. Shared products/accounts are intentionally
global, as declared by the native schema. Transaction services must revalidate
selected choices and permissions when saving/posting; a dropdown snapshot alone
is never authorization or proof that a master is still active.

## Concurrency and audit

Updates require the `version` returned when the record was loaded. The service
checks it under the write lock before changing anything. A stale value raises
`MasterConflictError`; the editor must reload and reconcile its changes.

The version hashes both the persisted row and its latest immutable master audit
event ID. Thus A→B→A edits still change the version, even if all edits occur in the
same second. Writers that maintain these masters must use this service rather
than bypassing the audit/version contract with unrelated SQL. A no-op update
returns the existing record/version without creating a mutation audit event.

Successful mutations append `masters.created` or `masters.updated` with actor,
entity ID, applicable company ID, timestamp and changed-field names. Updates also
record the previous opaque version. Audit JSON does not contain actual names,
codes, prices, full rows, passwords or tokens. This records who changed which
fields; it is not an old-value history or a substitute for issued-document
snapshots. If audit insertion fails, the entire master change rolls back.

`MasterValidationError` reports invalid fields/configuration or an integrity
conflict, `MasterNotFoundError` reports a missing record, and
`MasterConflictError` reports stale edits. Authentication and authorization
failures retain the security service's distinct error classes.

## Verification and remaining scope

Run `python3 -m unittest tests.test_masters tests.test_master_contacts -v`. Tests use synthetic temporary
databases and cover administrative boundaries, revoked/forged sessions,
company/module/action lookups, shared store membership, exact decimals and float
rejection, fiscal/cash validation, hierarchy cycles, active dependencies,
concurrent/stale/ABA edits, parameterized literal searches, private audit
metadata, rollback when audit fails, and refusal to create/promote other files.
Contact tests additionally verify text boundaries and leading zeros, private
audit metadata, permissions, explicit version-2 upgrade/backup, atomic migration
rollback, unchanged migration-1/2 checksums and unchanged posted snapshots.

This service is a backend for the current native tables. Complete original
account hierarchy/coding interaction, import mappings, remaining setup fields,
transaction workflows, reports/printing and target
Windows acceptance remain part of the full production objective.
