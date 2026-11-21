# Native company-access editing

`SecurityService.set_company_access(actor, user_id, company_ids)` replaces one
user's company assignments. It requires a current administrator session and
validates the entire replacement before changing stored grants. The authorization,
existing-assignment read, replacement and audit event share one native transaction.

An active existing company can be newly assigned. An **inactive company may only
be retained if that same user already has its assignment** at the time of the
transaction. This permits ordinary edits to other company assignments after a
company has been deactivated, without silently deleting its previous assignment.
A new inactive-company assignment is rejected, including when another user has
that company's assignment. Nonexistent companies are rejected.

Removing an existing inactive-company assignment is allowed. Once removed, it
cannot be added back while the company remains inactive. A stale editor that
submits the removed assignment therefore fails rather than restoring it.
Reactivation makes explicit reassignment available again.

Retaining an inactive assignment does **not** grant current operational access.
`authorize`/`require` still require an active company, and `accessible_companies`
still excludes inactive companies. `get_access` returns the actual stored
assignments so an administrative editor can identify retained inactive records.
An editor should label them as inactive and distinguish retention/removal from
adding a new inactive assignment.

Ordinary module grants cannot invoke this administrator operation. Failed
validation leaves grants and audit events unchanged. Successful replacement uses
the existing `security.company_access_changed` audit event and assignment count.
This API does not mutate companies or activate them implicitly.
