# Phase 5: Website lead integration layer, authentication, duplicate tracking, and admin visibility

Phase 5 builds on Phases 1–4 by introducing a secure website integration layer. It provides
per-source API key authentication, allowed-services restrictions, duplicate submission audit
logging, automatic service-based caller queue routing, and comprehensive admin visibility.

---

## 1. Public API Endpoint

`POST /api/v1/public/leads/`

Accepts JSON payloads up to 16 KiB. Rate-limited by IP address (defaults: 5/min burst, 50/day).

### Authentication

Website forms and backend integrations authenticate using the source's API key via any of:
1. `X-Api-Key: <api_key>` header
2. `Authorization: Api-Key <api_key>` or `Authorization: Bearer <api_key>` header
3. `"api_key": "<api_key>"` field in the JSON request body

If `WEBSITE_LEAD_REQUIRE_API_KEY = True` is configured in `settings.py`, all submissions require a valid API key. When false, legacy anonymous submissions are supported for backward compatibility, while requests targeting registered sources or providing invalid keys are strictly validated.

### Supported Fields

| Field | Type | Description |
| :--- | :--- | :--- |
| `name` | String (Required, max 200) | Visitor or applicant name |
| `phone` | String (Required, 7–15 digits) | Contact phone; validated and normalized |
| `email` | Email (Optional, max 254) | Visitor email address |
| `service` | String (Optional, max 100) | Service code (e.g. `MBBS`, `BDS`) or name |
| `source` | String (Optional, max 100) | Source code; defaults to the authenticated source |
| `campaign` | String (Optional, max 200) | Marketing campaign identifier |
| `location` | String (Optional, max 150) | City or region |
| `college` | String (Optional, max 200) | Current or preferred college |
| `neet_status` | String (Optional, max 100) | Qualification / exam status |
| `pcb_percentage` | Decimal (Optional, 0–100) | Academic percentage (up to 2 decimal places) |
| `preferred_intake`| String (Optional, max 100) | Target batch or semester |
| `notes` | String (Optional, max 2000) | Visitor inquiry or comments |
| `website` | String (Optional, max 200) | Honeypot field; must remain empty |
| `api_key` | String (Optional, max 128) | API key if supplied in body |

---

## 2. Duplicate Detection & Handling

When a submission matches an existing Lead (using normalized phone comparison):
1. **Zero Lead Duplication**: No duplicate `Lead` record is created.
2. **Audit Tracking**: A `WebsiteLeadSubmission` record is saved with `is_duplicate=True`, capturing the new submission timestamp, campaign, source, notes, and IP address.
3. **Response**: An authenticated response returns:
   ```json
   {
     "detail": "Enquiry received for existing lead.",
     "is_duplicate": true,
     "lead_id": 123
   }
   ```
4. **Original Lead Protection**: The existing lead's private notes and caller assignments remain unchanged.

When a submission is new:
1. A new `Lead` is created with `service_type` resolved.
2. A `LeadAvailability` record is created, placing it in the eligible caller queue.
3. A `WebsiteLeadSubmission` record is saved with `is_duplicate=False`.
4. Response:
   ```json
   {
     "detail": "Thank you. Your enquiry has been received.",
     "is_duplicate": false,
     "lead_id": 124
   }
   ```

---

## 3. Website / Lead Source Configuration

Admins configure sources in Django Admin via the `WebsiteSource` model:
- **Name**: Human-readable title (e.g. "Main Aspiring Website", "Study MBBS Landing Page").
- **Code**: Unique slug / source identifier (e.g. `official_web`, `mbbs_portal`).
- **API Key**: Auto-generated secure token (`ws_<token>`) or custom secret.
- **Allowed Services**: Multi-select of `Service` records. If set, submissions requesting other services are rejected with HTTP 400. If empty, all active services are allowed.
- **Active Status**: Active/inactive switch. Submissions to inactive sources return HTTP 403.

---

## 4. Automatic Service-Based Routing

New website leads automatically link to their requested `Service` (`lead.service_type`).
- Callers mapped to that service immediately see the lead in `GET /api/v1/mobile/leads/available/`.
- Unmapped callers cannot see or claim the lead.
- The lead can be claimed via `POST /api/v1/mobile/leads/<id>/claim/` and handled via the Phase 4 outcome workflow.

---

## 5. Admin Visibility

1. **Django Admin (`apps/leads/admin.py`)**:
   - `WebsiteSourceAdmin`: Overview of configured sources, masked API keys, allowed services, and submission counts.
   - `WebsiteLeadSubmissionAdmin`: Detailed searchable log of all inbound submissions with phone, name, source, campaign, duplicate status, submission time, lead links, and live routing status.
   - `LeadAdmin`: Includes `WebsiteLeadSubmissionInline` tabular inline, showing all historical form submissions per lead, alongside `routing_status_display` ("Available in queue", "Claimed by ...", "Assigned to ...", "Retry Cooldown").
2. **CRM Web Workspace (`apps/web/`)**:
   - The Lead Detail page (`web/lead_detail.html`) displays a dedicated **Website Enquiries** panel listing all inbound inquiries, duplicate badges, campaigns, and sources.

---

## 6. Verification & Test Suite

Run the Phase 5 test suite:
```bash
python manage.py test apps.leads.test_website_integration
```
Run the full test suite (197 tests):
```bash
python manage.py test
```
All batch upload, manual assignment, calling, and mobile workflows continue to pass without regression.
