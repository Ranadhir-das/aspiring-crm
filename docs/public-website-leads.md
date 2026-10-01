# Phase 1: website lead intake

Phase 2 adds a linked Service to new website leads while preserving this API's
text input and response format. See [service eligibility](service-eligibility.md)
for the catalog endpoints, inactive-service validation and OTHER fallback.
Phase 3 additionally creates a shared availability record for each new website
lead; see [website queues and claiming](website-lead-claiming.md). Duplicates and
legacy leads are not automatically added to those queues.

`POST /api/v1/public/leads/` accepts anonymous website enquiries as JSON. It does
not grant CRM access or accept a CRM token. Existing import, quick-entry, mobile
lead APIs and manual assignment continue to use their existing code and rules.

## Deployment

Run `python manage.py migrate` to add `Lead.service` (migration
`leads.0007_lead_service`), then restart the backend. Existing leads receive an
empty service value; no phone data, batches or assignments are rewritten.
Use HTTPS in production. No API key or CRM credentials belong in browser code.

For a browser form on a different origin, add that exact website origin to the
existing `CORS_ALLOWED_ORIGINS` environment setting and restart Django. Do not
enable wildcard origins. CORS is browser policy, not authentication or spam
protection. A website backend can also submit this JSON server-to-server.

## Example

```javascript
const response = await fetch('https://your-crm.example/api/v1/public/leads/', {
  method: 'POST',
  credentials: 'omit',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({
    name: 'Example Visitor',
    phone: '+91 (98765) 43210',
    email: 'visitor@example.com',
    service: 'MBBS',
    source: 'website',
    campaign: 'September admissions',
    location: 'Kolkata',
    notes: 'Please contact me about admissions.',
    website: '' // Honeypot: leave empty; hidden from real form users.
  })
});
const result = await response.json();
if (!response.ok) throw new Error(JSON.stringify(result));
// Show the thank-you message; no CRM record data is returned.
```

Required: `name` (nonblank, max 200) and `phone` (max 30 formatted characters).
Optional: `email` (valid email, max 254), `service` (100), `source` (100, defaults
to `website` if omitted), `campaign` (200), `location` (150), `college` (200),
`neet_status` (100), `preferred_intake` (100), `notes` (2000), `pcb_percentage`
(0–100, at most two decimal places; null allowed), and the `website` honeypot
(200). Numbers in parentheses are maximum string lengths. Unknown fields,
including status, owner, batch and record IDs, are rejected. JSON only; use
`JSON.stringify`, not multipart FormData. Payloads are limited to 16 KiB.

## Phone and duplicate behavior

The endpoint validates 7–15 ASCII digits after stripping common formatting. It
rejects letters, misplaced plus signs and repeated-single-digit placeholders.
It stores the result of the existing `normalize_phone` helper, and calls
`find_duplicate_lead` across all leads, including batch imports.

This deliberately preserves existing semantics: punctuation/spaces are ignored,
but country codes and leading zeros are **not** removed or guessed. For example,
`+91 98765 43210` matches `91-98765-43210`, but not `9876543210`. Use a consistent
country-code convention on the website. Validation is syntactic, not proof of
number ownership or reachability.

New leads are PENDING, unassigned, and have no import batch. Duplicates create
nothing and do not modify existing notes, attribution, status or assignment.
PostgreSQL transaction advisory locks prevent two simultaneous website requests
for the same normalized number from creating two leads. Legacy writers do not
participate in that lock: simultaneous legacy import/manual creation retains its
existing concurrency behavior. No global uniqueness constraint was introduced.
Duplicate lookup retains the existing phone scan; a large-scale indexed canonical
phone migration is outside Phase 1.

## Responses and security

- `202`: `{"detail":"Thank you. Your enquiry has been received."}` for a valid
  new submission, a duplicate, or a filled honeypot. No ID, duplicate flag or
  existing lead details are exposed; a honeypot submission is not stored.
- `400`: invalid JSON/fields; field validation errors are returned.
- `405`: read/update/delete methods are not supported.
- `413`: body exceeds 16 KiB.
- `415`: request is not JSON.
- `429`: submission rate exceeded, with a `Retry-After` header.

Separate per-IP limits default to `PUBLIC_LEAD_BURST_RATE=5/min` and
`PUBLIC_LEAD_DAILY_RATE=50/day`. Existing authenticated endpoint throttles and
permissions are unchanged. Intake keys on `REMOTE_ADDR`, ignoring arbitrary
`X-Forwarded-For` values. Behind a reverse proxy, configure the trusted ingress
to supply the actual client address securely or enforce per-client limits at
the proxy; otherwise clients share the proxy's quota. Server-to-server forms
likewise share their backend IP quota.

For multiple application workers/instances use a shared Django default cache
(for example Redis) and enforce request-size/rate limits at the reverse proxy.
Django's default local-memory cache is per process; DRF throttles are basic,
non-atomic spam controls, not a distributed denial-of-service defense. Honeypots
do not stop targeted bots. Add verified CAPTCHA at the website/ingress if abuse
requires it. No global cache, CORS or authentication policy was relaxed here.
Service/source/campaign are untrusted submission metadata, not authorization or
assignment instructions. No caller auto-assignment or claiming is implemented.

## Validation

```powershell
python manage.py check
python manage.py makemigrations --check --dry-run
python manage.py test
```

Focused regressions: `apps.leads.test_public_intake`, `apps.web.test_lead_entry`,
`test_assignment`, `test_assignment_api`, `test_commit`, and `test_import`.
Tests generate their own leads and CSV/XLSX files and isolate throttle caches.
