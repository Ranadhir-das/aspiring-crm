# AuthenticAttest Google Sheet → CRM bridge

## Status and integration boundary

Code: `scripts/authentic_attest_crm_bridge.gs`. This is an additive Apps Script
worker, not a replacement `doPost`. The existing deployed Apps Script and Sheet
headers were not available in this repository. Review those before installation:
we cannot yet verify that its writer tolerates extra columns. No website code,
existing Sheet, deployed script, CRM endpoints, imports or assignments were changed.

The existing website keeps posting to its existing Apps Script URL. Its existing
handler saves the same six fields and returns its existing response. A five-minute
trigger forwards saved rows independently; CRM downtime cannot fail that response.
This deliberately uses eventual forwarding rather than a network request in doPost.

Prerequisites: header row is row 1; submissions are appended as complete rows;
existing columns retain their names/order. Do not sort/delete/move rows while the
worker runs. Use filter views instead. If the existing writer derives row values
from every header, verify it leaves the new tracking columns blank. If it requires
an exact column count or overwrites tracking values, do not install until adapted.
Keep phone/WhatsApp columns as plain text so leading zeroes and + prefixes survive.

## Exact Script Properties

Open the **existing** Apps Script project → Project Settings → Script Properties →
Add script property. Set:

| Property | Value |
|---|---|
| `CRM_LEADS_URL` | Full reachable HTTPS endpoint, e.g. `https://crm.example.com/api/v1/public/leads/` (replace domain, retain trailing slash) |
| `CRM_API_KEY` | Actual API key from an active CRM WebsiteSource permitted to use APOSTILLE |
| `CRM_SPREADSHEET_ID` | Existing spreadsheet ID from its URL between `/d/` and `/edit` |
| `CRM_SHEET_NAME` | Exact existing worksheet/tab name |
| `CRM_COLUMN_MAP` | JSON mapping below, with values replaced by exact existing header labels |
| `CRM_FIRST_ROW` | Normally omit: installer sets it to the next unused row, excluding old submissions. Set `2` only to deliberately backfill all existing data rows. |

Example mapping (do not rename Sheet headers to match this example):

```json
{"name":"name","phone":"phone","whatsapp":"whatsapp","city":"city","country":"country","email":"email"}
```

The key is read only from Script Properties and sent in `X-Api-Key`. Never place it
in HTML, browser JavaScript, a Sheet cell, or source code. Restrict Apps Script
editor access: editors can access Script Properties. A CRM environment variable
alone does not provision a WebsiteSource; configure the source through Django Admin.
Ensure APOSTILLE is active and permitted for that source. The outgoing source text
is exactly `AUTHENTIC_ATTEST` and campaign is exactly `WEBSITE`.

Apps Script runs on Google's servers. It cannot reach this computer's localhost
or private Wi-Fi address. Use a deployed CRM HTTPS URL, or a deliberately configured
public HTTPS development tunnel to the local CRM, with that hostname accepted by
Django. No deployment or tunnel is created by this change. Redirects are rejected
to avoid forwarding credentials to a different destination.

## Install

1. Obtain/review the existing doPost and headers against the prerequisites above.
2. Add a new file `AuthenticAttestCrmBridge.gs` to that same project and paste the
   provided `.gs` file. Keep doPost, Sheet-saving logic and its returned response.
3. Enter Script Properties. Run `installAuthenticAttestCrmBridge` manually and
   authorize Spreadsheet, external-request and trigger permissions using a durable
   account with access to the Sheet. If scopes are explicitly listed in the project
   manifest, include spreadsheets, script.external_request and script.scriptapp.
4. Installation appends only six tracking columns, sets the initial row boundary,
   and creates one five-minute trigger for this account. Use one trigger-owning
   account. No website HTML changes or web-app URL changes are needed.
5. Submit one form normally. Confirm the original six fields and original browser
   response remain correct. Run `syncAuthenticAttestCrm` or wait for its trigger.

Outbound JSON contains name, phone, email, location (`city, country`, omitting empty
parts), service, source and campaign. WhatsApp stays in its original Sheet column;
the current CRM intake does not accept a WhatsApp field.

## Tracking and retries

Appended columns: `CRM Sync Status`, `CRM HTTP Status`, `CRM Attempts`,
`CRM Last Attempt`, `CRM Next Retry`, `CRM Lead ID`.

- `SYNCED` / `SYNCED_DUPLICATE`: verified HTTP 202 with lead ID and duplicate flag;
  the worker skips this row thereafter.
- `RETRY`: network errors, HTTP 408/429 or 5xx. Backoff begins at 15 minutes,
  doubles up to 24 hours; after eight attempts it becomes `REVIEW_MAX_RETRIES`.
- `SENDING`: persisted before sending. An interrupted run can retry after ten minutes.
- `REVIEW_HTTP`: validation/authentication/redirect errors; correct configuration or
  the row before retrying. HTTP code is retained without logging response contents.
- `REVIEW_RESPONSE`: unexpected acceptance response, requiring investigation.

For manual retry after fixing the cause, set the row's `CRM Sync Status` to `PENDING`,
clear `CRM Next Retry`, and reset `CRM Attempts` to `0`. Do not clear successful rows
unless deliberately replaying them. Blank status on eligible rows means not yet
attempted. Configuration errors leave those rows intact and emit a generic diagnostic
in Apps Script Executions. No names, phones, payloads, keys or response bodies are logged.

The script lock prevents overlapping worker runs. It sends at most three rows per
run and stops early on shared failures. Current CRM defaults are 5 requests/minute
and 50/day per IP; a busy Sheet can therefore accumulate a backlog. Apps Script
requests may share outbound IPs. Monitor 429 and backlog before a live rollout.

CRM already deduplicates Lead records by normalized phone. An ambiguous network
failure may retry an accepted submission: it should reference the existing Lead,
but can add another submission-audit record. This is at-least-once delivery, not
exactly-once. Normalization strips formatting; `+91...` and a number without its
country code are not necessarily equivalent. Keep website phone formatting consistent.

## Verification

Local mock tests: `node --test scripts/authentic_attest_crm_bridge.test.cjs`.
These check mapping, secret header usage, redirects, duplicates and failure handling;
they do not replace a real Apps Script/Sheet test.

On a test Sheet/project, verify: successful sync; same phone twice gives one CRM Lead;
invalid key records 401 without losing Sheet rows; unreachable CRM schedules retry;
correcting configuration plus PENDING syncs the retained row; rerunning skips synced
rows. Confirm existing Sheet headers/data/response before and after installation.

Google references: [Script Properties](https://developers.google.com/apps-script/guides/properties),
[UrlFetchApp](https://developers.google.com/apps-script/reference/url-fetch/url-fetch-app),
[LockService](https://developers.google.com/apps-script/reference/lock/lock-service).
