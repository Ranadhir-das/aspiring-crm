# Phase 2: service eligibility

[Phase 3 website queues and claiming](website-lead-claiming.md) now use this
configuration for newly created website leads. Existing manual assignment stays
independent of service eligibility.

Service eligibility is configuration only. Neither website intake nor the
existing batch import/manual assignment flow automatically assigns or claims
leads. Manual assignment does not require a service mapping.

## Models and migration

- `leads.Service`: name, unique uppercase code, description, is_active,
  created_at and updated_at. Initial codes: MBBS, MBA, APOSTILLE, OTHER.
- `accounts.User.designation`: optional job title (up to 100 characters), separate
  from the existing role/permissions. It does not grant access.
- `accounts.EmployeeService`: employee/service foreign keys, timestamps and a
  database uniqueness constraint on the pair. `user.services` is the related
  many-to-many interface. Employees may have multiple services or none.
- `Lead.service_type`: protected nullable Service foreign key. Phase 1's
  `Lead.service` string remains unchanged for backwards compatibility and
  preserves the website's submitted text.

Run `python manage.py migrate`, then restart Django. Migrations are
`leads.0008_service_eligibility` and `accounts.0007_service_eligibility`.
The data migration links existing nonblank service text to its known code or
OTHER, and links blank-service leads whose source is exactly `website` and which
have no batch to OTHER. Other legacy uncategorized leads remain null; old
custom-source enquiries with no service cannot reliably be distinguished from
manual leads. No batch IDs, phone values, statuses, assignment histories or
timestamps are rewritten. No employees are assigned services automatically.

## Admin

Use Django Admin (`/admin/`) with existing staff/model permissions:

1. Under Leads → Services, add/edit services or deactivate them.
2. Under Accounts → Users, set Designation in CRM Information.
3. Add/remove rows in the Employee services inline on that user.

Inactive services retain their mappings/history but are not eligible or returned
by the APIs below. Referenced lead services cannot be deleted; deactivate instead.
Mapping an employee does not turn that employee into a CALLER or grant CRM access.
Service codes must be unique uppercase identifiers, for example `MBBS` or
`STUDY_ABROAD`; use descriptions only for publicly visible information.

## Website intake compatibility

`POST /api/v1/public/leads/` keeps its existing request/response contract and
spam controls. Every newly created website lead receives `service_type`.

- `service: "MBBS"` resolves its code, case-insensitively; a unique service name
  is also accepted. Using catalog codes is recommended.
- Missing, blank or unknown legacy text maps to active OTHER, preserving the
  original text. This keeps Phase 1 integrations working.
- An explicitly selected inactive service is rejected with HTTP 400. If OTHER
  is missing/inactive, blank/unknown submissions return 400 rather than creating
  uncategorized leads. Keep OTHER active for old forms.
- Ambiguous service names are rejected; send a unique code instead.
- Duplicate leads keep their existing service and owner. No reassignment occurs.
- Clients cannot set `service_type`, employees, eligibility or assignment fields.

## Read APIs

`GET /api/v1/public/services/` is anonymous, read-only and returns active catalog
entries only. It contains no employees or lead data. Website forms can populate
their service selector from this array using the existing CORS configuration.

```json
[{"id": 1, "name": "MBBS", "code": "MBBS", "description": ""}]
```

`GET /api/v1/mobile/me/services/` returns the same array shape, restricted to the
authenticated caller's active mapped services. An empty mapping returns `[]`.
Use the existing `Authorization: Token ...` header and verified, unexpired work
session. Anonymous/expired sessions return 401; non-caller users return 403.
There is no user-ID selector and no write API. Catalog or designation changes
do not bypass the existing enrollment, attendance or authentication checks.

## Validation

Run `python manage.py check`, `python manage.py makemigrations --check --dry-run`
and `python manage.py test`. Service tests cover seeded data, migration
preservation, multi-service mapping, uniqueness/removal, admin forms, catalog
visibility, caller isolation, real token/session expiry, inactive service
handling, website resolution, duplicate preservation and unchanged manual
assignment. Phase 1 and existing CSV/XLSX import tests remain in the full suite.
