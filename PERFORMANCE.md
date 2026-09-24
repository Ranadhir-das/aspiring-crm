# Caller points and performance

## Behavior and scoring

Caller Profile supports start/end dates, inclusive start/end hours, and daily/hourly buckets in Asia/Kolkata. Profiles default to today; team Performance defaults to the last 30 days. The same selected window drives call statistics, points, the timeline, ledger, comparison graph and signed caller bar chart. Hourly ranges are capped at 31 days and daily ranges at 366 days. Invalid web filters show their errors and clearly disclose the default window; the API rejects them with HTTP 400.

Calendar cards show today, this Monday-to-now week, and this month-to-now. They are explicitly independent of the report window. Current assigned leads and batch lists remain a live all-date inventory.

The original status pie bug was caused by ordering fields entering SQL GROUP BY, splitting each status by lead. Status aggregation now clears ordering and counts distinct lead IDs. The profile retains this corrected current-inventory pie and adds a separate call-outcomes pie for the filtered period. Each pie centre and legend use the same denominator. A call with no outcome has its own segment.

| Event | Points | Deduplication scope |
|---|---:|---|
| Dialed | +1 | Call |
| Connected | +2 | Call |
| Duration >=30s / >=120s / >=300s | +1 / +2 / +3, highest tier only | Call; later edits append only the score difference |
| Follow-up completed | +3 | Follow-up |
| Interested lead | +5 | Lead, including across reassignment/status toggles |
| Counselling/demo scheduled | +8 | Lead milestone |
| Application started | +10 | Lead milestone |
| Verified admission | +20 | Lead milestone, admin verified |
| Invalid / wrong number | 0 call points | Call; corrections reverse previous call points |
| Missed follow-up | -3 | Follow-up |
| False/incorrect status | -5 | Admin-reviewed lead milestone |

Connected means positive recorded talk duration or an Interested, Not Interested or Call Back outcome, excluding Busy, No Answer and Wrong Number. No outcome plus positive duration is connected. This uses existing CRM data; there is no independent telephony verification. Talk time and average duration use connected calls only.

A follow-up becomes missed when its scheduled time passes without completion. A later completion can earn +3 but retains the -3 missed penalty. Cancellation before a penalty is posted earns neither award nor penalty. First completion time is persisted; later note edits cannot turn an on-time completion into a late completion. Existing completed records use their previous `updated_at` during migration, the best timestamp previously available.

Admissions, applications and counselling are recorded as explicit milestones from an admin's caller-profile form or Django admin/API. Existing `LeadQualification.ADMISSION_DONE` is not proof of a verified admission and does not automatically award points. Include a verification/evidence reference in the mandatory reason. A second incident or correction can be recorded through a reasoned manual adjustment.

Session login time is clipped to the selected window and to the last heartbeat for open sessions. Historical sessions only store aggregate active seconds, so partially overlapping active time is explicitly labelled estimated and prorated. It is not an exact per-hour foreground log.

## Models and integrity

- `performance.PointsEntry`: append-only ledger with caller, nullable lead/call/follow-up links, event, signed points, reason, event timestamp, write timestamp, actor, and a unique event key. Caller/time and event/time indexes support reports. Deleted source objects do not erase ledger transactions. Caller deletion is protected.
- `performance.LeadMilestone`: immutable, unique lead/event record with evidence, caller, verifier and timestamp.
- `performance.PointsAdjustment`: immutable request UUID, signed adjustment, caller, optional lead, required reason and administrator. Ledger and existing `web.AuditEvent` are written in the same transaction.
- `calls.Call.client_event_id`: optional caller-app UUID with a conditional unique caller/event constraint. Also adds caller/start-time index.
- `followups.FollowUp.completed_at`: immutable first completion time under ordinary saves.

All normal Call, Lead and FollowUp saves trigger scoring, including existing web/API/admin flows. Raw fixture saves are ignored. Call and follow-up scoring uses transaction row locks; ledger event keys enforce uniqueness. Duration edits append differences rather than stacking tiers. Bulk ORM writes bypass Django signals; use the reconciliation command when intentionally importing historical source data.

The ledger cannot be edited or deleted through the CRM, API or Django admin. Use a reasoned adjustment to correct history. Admin and Super Admin can manage points; Manager can read team performance; Caller can read only their own profile and ledger. Other roles are denied. Django admin also requires the existing staff login. The `performance.manage_points` permission is declared for discoverability; the existing application role gate remains authoritative.

## API

All endpoints reuse the existing VerifiedSessionAuthentication and its active verified work-session requirement.

- `GET /api/v1/points/me/` ? caller's summary, trend and paginated ledger.
- `GET /api/v1/points/callers/<id>/` ? own caller account or management read access.
- `POST /api/v1/points/adjustments/` ? Admin/Super Admin only; `request_id` (UUID), `caller`, nonzero `points`, `reason`, optional `lead`. Replaying the identical UUID/payload returns the existing adjustment; conflicting reuse is rejected.
- `POST /api/v1/points/milestones/` ? Admin/Super Admin only; `caller`, `lead`, `event` (`COUNSELLING`, `APPLICATION`, `ADMISSION`, `FALSE_STATUS`), `reason`. Lead must be assigned to the selected caller.

Read filters: `start_date`, `end_date` (YYYY-MM-DD), `start_hour`, `end_hour` (0?23), `interval=day|hour`, `page`. Ledger pages contain 50 entries and next/previous links. Dates are inclusive; end hour is inclusive, implemented with an exclusive upper boundary. JSON summary includes signed totals, calendar points, calls, connected calls, talk time, average connected duration, follow-ups, interest awards, counselling, applications and admissions.

Existing `POST /api/v1/calls/` accepts optional `client_event_id`. The Android client should generate one UUID per device call and reuse it for retries. First submission returns 201; an identical retry returns 200 without another call, follow-up or award. Conflicting call data returns 409. Older clients remain compatible, but without a UUID distinct call rows are treated as distinct calls. The caller app displays the existing ledger through `/api/v1/points/me/` (see Mobile points below).

## Migrations and rollout

New migrations:

1. `calls/0002_call_client_event_id_and_more.py`
2. `followups/0003_followup_completed_at.py` (preserves available historical completion times)
3. `performance/0001_initial.py`

Apply against the deployment database and backfill once:

```sh
python manage.py migrate
python manage.py reconcile_points --backfill
```

Backfill uses existing call start times, first interested call where available, existing interested leads' last update time, and completed follow-up timestamps. It does not invent historical applications/admissions. Re-running is safe. Events from ordinary saves are immediate. Missed follow-ups caused solely by time passing need the provided scheduled command:

```sh
python manage.py reconcile_points
```

`deploy/vaani-points.service` and `deploy/vaani-points.timer` run it every minute, using the same `/opt/vaani` paths and service user as the existing deployment. Copy both into systemd, reload and enable `vaani-points.timer`; adjust paths if the deployment differs. The timer has not been installed on a live server by this change.

## Files changed

New `apps/performance/`: app registration, `models.py`, `services.py`, `signals.py`, `reporting.py`, `forms.py`, `admin.py`, `api.py`, `urls.py`, `tests.py`, `migrations/0001_initial.py`, and `management/commands/reconcile_points.py`, with package initializers.

Existing integration files: `apps/calls/models.py`, `apps/calls/api/serializers.py`, `apps/calls/api/views.py`, `apps/followups/models.py`, `apps/web/caller_profile.py`, `apps/web/performance_views.py`, `apps/web/views.py`, `config/settings.py`, `config/urls.py`, and the calls/follow-ups migrations listed above.

UI: `apps/web/templates/web/profile_summary.html`, `performance.html`, new `performance_filter.html`, `base.html`, plus `apps/web/static/web/crm.css` and `crm.js`. Existing activity markup and batch lead links are retained. Styles use the existing theme tokens and responsive layout; static version is bumped to 10.

Validation: `apps/web/test_performance_profile.py`, `apps/activity/tests.py`, `apps/web/test_settings.py`, new `config/test_runner.py`. The test-settings runner discovers all automated app tests; root-level `test_*.py` files are legacy manual scripts that query a pre-existing database at import time, not isolated test cases. Explicit test labels remain supported.

## Validation commands

Use the existing isolated in-memory test settings to avoid the configured operational database:

```powershell
$env:DJANGO_SETTINGS_MODULE = 'apps.web.test_settings'
python manage.py check
python manage.py test
python manage.py makemigrations --check
```

New tests cover tier boundaries, call corrections, invalid numbers, duplicate statuses and API retries, follow-up deadlines, adjustments, verified milestones, permissions, ledger immutability, calendar/hourly filters, session clipping and pie aggregation. The full suite has four pre-existing failures independently reproduced from the original HEAD: photo-challenge identity review, pending-enrollment onboarding expectation, password-login session creation expectation, and the removed dashboard account-request count. These unrelated authentication/notification behaviors were not changed.

A browser was unavailable in this session, so templates were rendered with synthetic fixtures but visual screenshot QA could not be completed. Live database migrations/backfill have not been applied.

Final results: `manage.py check` passed; `manage.py makemigrations --check` reported no changes; `node --check apps/web/static/web/crm.js` passed; the focused performance/profile/activity suite passed 34/34; the full app suite passed 73/77 with the four baseline failures above.


## Mobile points (2026-09-23)

The caller app now shows a My points card on Employee Home and Caller Dashboard.
Only CALLER accounts see it. The card uses the existing authenticated points/me
endpoint; it never calculates or awards points on the device. It shows all-time,
today, this week, and this month totals, and the five latest ledger entries from
the default last-30-days reporting window. Calendar totals use Asia/Kolkata.
Negative adjustments and penalties remain signed.

The summary now includes additive `lifetime_points`, independent of report filters.
Existing `total_points` retains its selected-period meaning. Older backend responses
without lifetime_points display an explicitly labelled last-30-days total.
The card refreshes on screen focus, app resume, every minute while focused/active,
and with its Refresh button. Errors show a retry control and identify stale values;
changing accounts clears the previous account's points. No authentication or scoring
rules were changed, and no migration is needed.

Validation: TypeScript passed; all 23 performance tests passed using isolated test
settings, including lifetime totals and caller isolation. The local PostgreSQL
backfill completed for all four callers; ledger entries increased from 26 to 35.
New ordinary saves continue to award points through existing signals. Time-only
missed-follow-up penalties still use the reconciliation schedule described above.
Deploy/restart the CRM backend for the new lifetime field and reload/rebuild the
mobile app to distribute the card. Native device visual verification was not run.
