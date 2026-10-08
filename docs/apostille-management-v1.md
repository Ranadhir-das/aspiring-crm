# Apostille Management V1

Admin and Super Admin can use **Admissions > Apostille** at `/apostilles/`.
Other roles receive HTTP 403, including direct access to the search and points-preview endpoints.
There is no new mobile/public API or website integration.

## Data and points

`leads.Apostille` holds the external name/phone or one existing Lead, document count,
country, decimal amount received, one CALLER, creator and timestamps. It has no status.
External records do not create Leads. Existing-lead records use the Lead's name and phone;
they do not change the Lead's status or assignment. Linked Leads/callers are protected from
deletion while referenced by an Apostille.

`calculate_apostille_points()` in `apps/performance/apostille.py` owns all tier rules:

| Minimum INR received | Points |
| ---: | ---: |
| 0 | 0 |
| 1,500 | 10 |
| 3,000 | 20 |
| 5,000 | 30 |
| 10,000 | 50 |
| 20,000 | 100 |
| 30,000 | 200 |
| 40,000 | 300 |
| 50,000 | 500 |
| 100,000 | 1,000 |

Each tier extends to the next threshold, including fractional rupees. The browser requests
the preview from the server and cannot supply the awarded points.

The existing `PointsEntry` ledger receives signed `APOSTILLE` entries linked to the record.
Saving locks the Apostille row and reconciles each caller's existing net contribution in
the same transaction. Amount edits produce only the difference; caller changes reverse the
old attribution and award the new one. Unchanged saves do not duplicate entries. Below the
first threshold no positive award is created.

Deletion uses a confirmation page and CSRF-protected POST. It reverses the current net
contribution in the deletion transaction, including ORM queryset deletions. Ledger history
remains; its nullable Apostille FK is cleared on deletion, while event keys and reasons retain
the record's stable UUID. A failure rolls back both the business operation and its points.
Bulk creates/updates of Apostilles are blocked because they bypass model save/reconciliation.
There is no historical backfill or recalculation of existing points.

## Schema rollout

New migrations:

- `leads.0024_apostille`
- `performance.0004_pointsentry_apostille_alter_pointsentry_event`

These are additive schema migrations without data migrations. They must be applied through
the normal approved deployment process before opening the new page. Implementation validation
uses `migrate --plan`; it does not apply them to the existing CRM database.

## Manual acceptance checks after local migration

1. As Admin, add an external candidate for Caller A with INR 2,000: verify 10 points.
2. Edit to INR 5,000: verify a +20 adjustment and a net contribution of 30.
3. Change to Caller B: verify A has net 0 and B has 30 for this record.
4. Save unchanged: verify no additional ledger entry.
5. Select Existing Lead, search and choose a lead: verify its details and link on the detail page.
6. Delete through confirmation: verify net contribution is reversed, with history retained.
7. As Manager/Caller, verify no Apostille menu and direct URLs return 403.
8. Check form layout at phone and desktop widths, amount preview and lead-search errors.

Automated coverage lives in `apps/leads/test_apostille.py` and `apps/web/test_apostille.py`,
including database rollback, concurrent edits, all tier boundaries, role permissions,
CSRF, source validation, filtering, pagination and CRUD.
