# Phase 10C: Caller notification center

Only `LEAD_ASSIGNED` is persisted in this phase. Existing website lead push code,
import/assignment semantics, claims, calls, outcomes and PushDevice registration
are unchanged. No reminder, recording, deletion or website-notification feature
is added.

## Persistence and transaction boundary

`apps.notifications.models.Notification` has recipient, type, title, body, data,
is_read, read_at and created_at. Its indexes cover `(recipient, is_read)` and
`(recipient, -created_at, -id)`. Notification data is a snapshot of an event,
not a grant of current lead access. There is no Lead FK dependency and listing
does not fetch leads individually.

The existing `bulk_assign_leads()` and `LeadAdmin.save_model()` on-commit hooks
call `notify_caller_about_assigned_leads()`. That helper now uses
`apps.notifications.services.create_notification()` to save one row per lead,
in a separate transaction, **before** looking up devices or contacting Expo:

Assignment → assignment commit → persist notification(s) → best-effort push.

An outer rollback discards the callback. Same-caller assignments and skipped
leads never reach it. A→B→A generates a new event on each actual transition.
Unassignment creates no event. The import path's existing `notify=False` is
preserved. No historical assignments are backfilled by the migration.

Push failures cannot roll back the committed notifications or assignment.
Users without active devices still receive inbox entries. As with the existing
on-commit push mechanism, this is not a durable task queue: a process termination
between assignment commit and the callback, or a notification database failure,
can require operational reconciliation. No new background worker is required.

## Push payload and bulk compatibility

Single assignment (plus existing `channelId: default` and `sound: default`):

```json
{
  "to": "<ExpoPushToken>",
  "title": "New Lead Assigned",
  "body": "Rahul Sharma has been assigned to you.",
  "data": {"type": "LEAD_ASSIGNED", "lead_id": 123, "notification_id": 456}
}
```

All devices for the recipient share the same persistent notification ID.
**Bulk push remains the existing single summary per device**, with `count`, the
first lead ID, and that lead's persistent notification ID. Ten successful lead
assignments create ten inbox entries, not one. Opening the summary marks only
the linked entry read; the other entries remain unread. No password, API key or
auth token is added to the payload.

## Authenticated API

All URLs below are under `/api/v1/mobile/notifications/`. Use the existing
`Authorization: Token <token>` header. `VerifiedSessionAuthentication` retains
the current verified, unexpired session and registration checks.

| Method | Relative path | Response |
|---|---|---|
| GET | empty | `{count, next, previous, results}`; 25 entries per page, `?page=2` |
| GET | `unread-count/` | `{"unread_count": 5}` |
| POST | `<id>/read/` | Updated notification |
| POST | `read-all/` | `{"updated": 5}` |

Each entry exposes only `id`, `type`, `title`, `body`, `data`, `is_read`,
`read_at`, `created_at`. Ordering is newest first, with ID as tie-breaker.
The payload data contains `type: LEAD_ASSIGNED` and `lead_id`.

Every query filters `recipient=request.user`; request body/query parameters
cannot select another owner. Another user's ID returns 404. Count uses indexed
SQL COUNT, not a full-table fetch. Mark-read uses a conditional UPDATE and
preserves the original read_at on repeated requests. Mark-all updates only that
user's unread rows in one statement and returns 0 when there are none. There is
no public create/delete endpoint. Django admin shows a read-only, escaped JSON
view and disables adding/deleting notifications.

## Caller app

**More → Notifications** opens the existing-router `/notifications` screen.
The badge hides zero and displays `99+` for larger counts while keeping the real
backend count. The screen supports focus refresh, app-resume/foreground-push
refresh, pull-to-refresh, loading/empty/error states, retry and Load more.
Unread cards have an accent border/background and dot. Mark all as read is
available at the top.

Inbox and Android push taps mark the exact notification read before opening
`/lead-details`. Read failure does not prevent opening a valid lead. Old push
payloads without notification_id still work; invalid IDs never become API paths.
Taps wait for restored authentication and the router to be ready. Listener
cleanup cancels pending work; logout prevents pending navigation. No tap calls
claim, call-started or dial APIs.

Notification-originated lead details recheck the existing authenticated
`GET /api/v1/mobile/leads/<id>/`. A 403/404 displays “This lead is no longer
assigned to you.” Network failures offer retry. The normal lead list is refreshed
and existing call/outcome actions wait for that lead to be in the list. Normal
lead-detail navigation remains unchanged. The foreground notification handler
retains existing banner/sound behavior and never navigates automatically.

## Migration and verification

Migration: `notifications/0001_initial.py` (applied locally).

```powershell
.\venv\Scripts\python.exe manage.py migrate
.\venv\Scripts\python.exe manage.py makemigrations --check
.\venv\Scripts\python.exe manage.py check
.\venv\Scripts\python.exe manage.py test apps.notifications apps.accounts.test_push_devices apps.accounts.test_send_test_push apps.leads.test_claiming test_assignment test_assignment_api --noinput
.\venv\Scripts\python.exe manage.py test --noinput
```

Results on 2026-09-30: targeted **82 passed**; full Django suite **326 passed**,
zero failures/errors. Migration consistency and Django system checks pass.
Full suite includes batch/import, manual assignment, caller access, calls,
outcomes and claim lifecycle regressions. Transport is mocked in notification
tests; no real push was sent by this phase's tests.

From caller-app:

```powershell
npx tsc --noEmit
node --test --test-isolation=none scripts/test-notification-center.cjs scripts/test-notification-routing.cjs scripts/test-call-lifecycle.cjs scripts/test-recording-uploads.cjs
```

TypeScript passes; all **40 mobile regression tests pass**. Tests cover API requests, unread counts, IDs, read
operations, tap/auth gates, pending navigation cleanup and existing regressions.
Physical-device/UI verification is still required; automated checks do not
establish real Android presentation/delivery.

## Device smoke test

1. Update backend/app and apply the migration on each deployment.
2. Log in as Caller A. Assign an unassigned lead from the existing admin/manager
   UI. Dismiss the Android push; More → Notifications must still show the entry
   and unread badge.
3. Tap the entry: it becomes read and opens lead details without dialing.
4. Assign another lead and tap its Android push with the app closed. After
   session restoration the correct lead opens and the linked entry is read.
5. Test same-caller reassignment (no duplicate), A→B→A (new events), ten-lead bulk
   assignment (ten inbox entries, existing summary push), and mark-all-read.
6. Reassign a previously notified lead to B; A's old entry remains history but
   opening it shows the unavailable message. B's notifications must never appear
   in A's list/count. Denied push permission must not prevent inbox access.

## Phase 10C file inventory

Backend new: `apps/notifications/models.py`, `admin.py`, `api.py`,
`test_center.py`, `migrations/__init__.py`, `migrations/0001_initial.py`, this doc.
Backend modified: `apps/notifications/services.py`, `tests.py`,
`apps/accounts/push_notifications.py`, `apps/accounts/api/urls.py`,
`config/settings.py` (register notifications app only).

Mobile new: `src/services/notificationCenter.ts`,
`src/hooks/useNotificationUnreadCount.ts`, `src/components/NotificationIcon.tsx`,
`src/app/notifications.tsx`, `scripts/test-notification-center.cjs`.
Mobile modified: `src/services/notifications.ts`,
`src/components/PushNotificationRegistration.tsx`, `src/app/(tabs)/more.tsx`,
`src/app/_layout.tsx`, `src/app/lead-details.tsx`,
`src/context/LeadContext.tsx` (export existing lead type/mapper for reuse only).
No package, native Android, Firebase or Expo configuration changes.
