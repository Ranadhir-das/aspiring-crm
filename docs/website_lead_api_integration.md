# Phase 9: direct multi-website public intake

Endpoint: `POST /api/v1/public/leads/` (JSON, maximum 16 KiB).
Google Apps Script and Google Sheets are not part of this integration.

## Authentication and source

Every request needs an active WebsiteSource credential. Prefer `X-Api-Key`.
Existing `Authorization: Api-Key <key>`, `Authorization: Bearer <key>` and body
`api_key` formats remain supported. Missing/invalid credentials return 401;
inactive sources return 403. WEBSITE_LEAD_REQUIRE_API_KEY is True; the endpoint
cannot be made anonymous by overriding that setting.

Required JSON fields: name and phone. Optional fields: email, service, source,
campaign, location, college, neet_status, pcb_percentage, preferred_intake, notes,
website (honeypot, leave empty). Unknown fields return 400.

Omit source to use the authenticated source code. A nonempty source must match
that code exactly (case-sensitive); conflicts return 400 without storing a lead
or audit row. Source codes retain their configured case. New leads and submission
records use the authenticated source; existing duplicate Leads retain their
original attribution and ownership.

Omit/leave service blank to use the website default. A supplied service must be an
active service code (case-insensitive) explicitly in allowed_services. Unknown,
inactive or disallowed services return 400. Empty allowed_services permits none.
A default must be active and in allowed_services; admin forms and intake validate
this. Existing sources without defaults can still send explicit allowed services;
configure a default before accepting submissions with omitted service.

Successful accepted leads return 202 with detail, lead_id and is_duplicate.
Honeypot submissions are dropped and return generic 202 without an ID. Duplicates
reuse the existing normalized-phone Lead and append submission history without
changing its assignment/service/ownership. A new website enquiry creates the
existing LeadAvailability and uses existing service eligibility/atomic claiming.
CSV/XLSX import and manual assignment paths are unchanged.

## AuthenticAttest provisioning

Migrate first: `python manage.py migrate`.
Provide AUTHENTIC_ATTEST_API_KEY securely in the process environment (32?128
characters, generated with a cryptographically secure generator), then run:

```text
python manage.py provision_website_source
```

The command creates/updates AUTHENTIC_ATTEST, activates it, sets default APOSTILLE
and replaces its allowed services with APOSTILLE only. APOSTILLE must already be
active. It never prints credentials. Re-running without the environment variable
preserves an existing credential. Providing a new key deliberately rotates it.
Do not pass secrets as command-line arguments, commit them, or add them to browser
code. Local AuthenticAttest was provisioned during implementation with a generated
credential stored only in the database. Production must be provisioned separately.

New websites need a WebsiteSource/admin configuration, their own credential,
allowed services, default service and CORS origin configuration, not new CRM code.

## Browser CORS configuration

Set WEBSITE_CORS_ALLOWED_ORIGINS to an explicit comma-separated list of confirmed
origins (scheme + hostname + optional port, no paths). Production configuration:

```text
WEBSITE_CORS_ALLOWED_ORIGINS=https://authenticattest.com,https://www.authenticattest.com
```

The default allowlist contains only these two approved origins. Set
WEBSITE_CORS_ALLOWED_ORIGINS to the complete desired list to add future websites
without changing API code. The old development-origin defaults are removed.
X-Api-Key is added to allowed headers. Restart the backend after
changing environment configuration. Unapproved origins receive no CORS grant.
An Origin header is not a credential, and CORS does not authenticate requests.

IMPORTANT: Direct browser code cannot keep an API key secret. Provisioning and
CORS do not solve this limitation. The requested combination of direct browser
calls and a credential unavailable to that browser cannot be fulfilled with a
static API key. Secure server-side forwarding or a separately designed public
browser credential/abuse-control model is still a production architecture decision.
No browser code or alternative authentication system is implemented here.

The CRM needs a reachable HTTPS domain and appropriate Django/proxy host settings.
Configure existing website sources' allowlists/defaults before rollout: previously
empty allowlists allowed all services, and now intentionally reject all services.
Throttling, JSON validation and honeypot protections remain enabled.

Health and dry-run endpoints described by older documentation are NOT implemented.
Do not send dry_run: it is an unsupported field. The earlier Google bridge files
are not wired into this integration and were not changed by this hardening work.

## Verification

Run targeted public/website tests, `python manage.py test apps.leads apps.calls`,
`python manage.py test`, `python manage.py check` and
`python manage.py makemigrations --check --dry-run`.
