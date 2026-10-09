# Employee location tracking

## Architecture and permissions

Tracking reuses the existing DRF `VerifiedSessionAuthentication` and `CallerSession`.
The initial sign-in location, attendance photo verification, midnight expiry, foreground
heartbeat and work-time calculation remain in their existing flows. No new employee or
authentication/session system is introduced.

`EmployeeLocationPoint` stores employee/session FKs, latitude, longitude, accuracy,
optional altitude/speed/heading, recorded time and server receipt time (`received_at` also
serves as row creation time). A unique session/timestamp constraint makes replay idempotent.
The `(employee, recorded_at DESC, id DESC)` index supports latest-point and chronological
route scans; PostgreSQL can scan it in either direction. A recorded-time index supports
retention cleanup. No phone hardware identifiers are collected.

CallerSession adds `location_state` and `location_state_at`. Login coordinates are never
overwritten with subsequent positions. Tracking activity is derived from the same session's
verification, expiry/logout and reported collection state. GPS/permission unavailability is
separate from observation freshness; the server cannot infer a device's GPS switch from silence.

Only active **ADMIN/SUPER_ADMIN** accounts can read live positions/history, load the CRM page
or connect to its refresh socket. Manager, Counselor, Caller and other employees cannot read
these datasets. The existing Employee Sessions report also hides login-location map links
from Managers; its remaining attendance/session information is unchanged.

## APIs

- `POST /api/v1/mobile/location/`: verified employee token, at most 100 points per batch.
- `GET /api/v1/mobile/location/status/`: own current session ID, start, expiry and collection state.
- `POST /api/v1/mobile/location/status/`: own current `session_id` and `state`
  (`ACTIVE`, `UNAVAILABLE`, `STOPPED`); does not change attendance or session duration.
- `GET /api/v1/admin/employee-locations/live/`: CRM cookie session or verified admin token.
- `GET /api/v1/admin/employee-locations/history/?employee=123&date=2026-10-08`:
  chronological points; date uses CRM timezone. Alternatively supply ISO `start` and `end`
  (exclusive), at most 31 days per request.

Mobile example (existing `Authorization: Token ...` header; never put it in a URL):

```json
{
  "session_id": "the-existing-verified-session-uuid",
  "points": [{
    "latitude": 22.5726,
    "longitude": 88.3639,
    "accuracy": 12.5,
    "altitude": 10.2,
    "speed": 4.2,
    "heading": 180,
    "recorded_at": "2026-10-08T10:30:00Z"
  }]
}
```

The entire batch is validated and persisted atomically with bulk insertion. Unknown fields,
including employee/user overrides, are rejected. Coordinates/measurements must be finite
and in range. Timestamps must fall within the referenced verified session, the retention
window, and at most 60 seconds ahead of server time. An authenticated employee may replay
their own earlier session's offline points, but cannot add observations after that session
ended or use another employee's session. HTTP 200 returns `accepted` timestamps, including
duplicates. Invalid timestamps return HTTP 400 with `rejected`; the app can discard those
invalid samples without discarding valid offline points. Mobile writes are throttled at
120 requests/minute per employee, independently of existing throttles.

Admin responses are `private, no-store`. Both lists are paginated (200 default, 1,000 maximum).
Latest positions use correlated indexed subqueries and a constant number of queries,
not one history query per employee. Freshness thresholds are in
`EMPLOYEE_LOCATION_STATUS_SECONDS`: LIVE <30s, RECENT <300s, STALE <900s, otherwise OFFLINE.
An offline batch uploaded now does not make an old GPS observation live.

## Android collection and offline queue

Expo SDK 57's `expo-location` and `expo-task-manager` handle background callbacks. Task
definitions load at bundle scope. The existing authenticated app mounts one registration
component; it starts/restores tracking automatically and leaves workspace routing unchanged.

The employee sees a location disclosure before the background-permission request and a
visible Android foreground-service notification while tracking. Permission/GPS unavailability
shows a specific permission/GPS/paused diagnostic and last successful sync in the app; it does not block sign-in. A denied or
revoked permission is not bypassed. Reopening the app after permission is restored restarts
tracking automatically. Native/API failures are isolated from login.

Two requests share Android's fused provider:

- Primary foreground-service stream: high accuracy / 10 seconds / 15 metres when moving; balanced / 60 seconds / no displacement minimum when stationary or power-saving. Native options change only while the app is visible.
- Stationary stream: balanced accuracy, 60 seconds, no displacement minimum.

Shared filtering uses speed, distance, accuracy and elapsed time: moving samples about every
10 seconds, significant movement no faster than 5 seconds when delivered by Android, and
stationary samples once per minute. Finite poor-accuracy samples remain raw history; the CRM excludes points worse than the configured accuracy threshold from displayed segments. These are
requested intervals and recorded-sample limits, not guarantees about GPS timing or hardware
power consumption. The last safely configured native request remains registered while
backgrounded; device battery/GPS behavior must be measured in the field. SDK 57 rejects
restarting a foreground location service from the background, so the app does not attempt
unsupported background option changes. There is no rapid JS GPS polling.

The app-private AsyncStorage queue is written before upload. Network failures retain points;
only acknowledged timestamps are removed. Uploads are capped at 100 points, normally batched
over 15 seconds while moving, with exponential retry capped at 60 seconds. Native callbacks
drive background retries; a 30-second foreground timer only retries existing data/checks
expiry and never requests GPS. Overlapping callbacks serialize writes and uploads.

Current-session and archived-session buffers each hold at most 5,000 points and retain at
most two days when processed. Expiry/401/403 stops native collection and keeps pending data
for replay by the same employee after reauthentication. A different employee never receives
or uploads that history. Bounded archived queues retain their original employee/session identity.
Explicit logout stops native tasks first, makes a bounded online flush, reports STOPPED
where reachable, and retains unacknowledged disabled samples for that employee to retry.
Credentials still clear through the unchanged authentication flow. A remote
session that cannot be contacted remains stale until normal expiry. Auth restoration failures
preserve the bounded queue while keeping the existing sign-out behavior.

Android force-stop, terminated apps, vendor battery restrictions and approximate-only
permission can interrupt collection. Tracking does not silently restart after force-stop;
the employee must reopen the app. No locations are accepted after the session's expiry.

## CRM map and route

`/employee-locations/` appears for Admin/Super Admin under the workspace session area.
It shows paginated employees, freshness, reported location availability, last observed time,
accuracy and speed. Select a person/date to show recorded route points. The route API also
supports explicit time ranges. The page loads 500 points at a time on demand and caps a
rendered route at 10,000 points. Separate work sessions or gaps over five minutes break the
polyline. No road snapping, route invention or hidden interpolation is performed. A link
opens the existing employee work/activity profile for correlation.

The existing Channels/Redis infrastructure delivers post-commit invalidation events through
`/ws/employee-locations/`; the socket contains no coordinates. It requires a valid admin CRM
cookie session and allowed Origin, and rechecks session/account permissions before events.
The browser refreshes no faster than five seconds, with a ten-second polling fallback and
age refresh. Hidden browser tabs do not poll.

Leaflet **1.9.4** is vendored with its license. Default tiles are OpenStreetMap, as approved,
with visible attribution. Configure `EMPLOYEE_LOCATION_TILE_URL` and
`EMPLOYEE_LOCATION_TILE_ATTRIBUTION` for another approved provider. No paid API or key is used.
Map tile requests disclose the viewed map area to the tile provider; employee names, tokens
and API responses are not sent to it. Observe the provider's usage/privacy policy and use
an appropriate tile service if traffic grows; no tile prefetch or bulk download is implemented.

## Migration and retention scheduling

New migration: `accounts.0012_callersession_location_state_and_more`.
It adds the history table, constraints/indexes and two session-state fields without
recalculating existing attendance, points or CRM records. Review `migrate --plan` and
`sqlmigrate accounts 0012` before rollout. Production deployment is a separate task.

The cleanup command removes only location points older than 90 days in bounded transactions:

```sh
python manage.py cleanup_location_history --dry-run
python manage.py cleanup_location_history --batch-size 5000
```

Cleanup never runs as part of an API request. The included `deploy/vaani-location-cleanup.service`
and `.timer` provide daily automatic cleanup (03:30 server time, randomized by up to five
minutes). During a separately approved production rollout, review paths/user and install/enable
the timer using the normal systemd workflow. These files are not installed/enabled automatically
by this implementation. An equivalent daily scheduler invocation also works.

At 10-second moving samples, an eight-hour workday is approximately 2,880 points/employee;
stationary-only collection is approximately 480. One hundred mobile employees at the moving
rate produce about 288,000 points/day and 25.9 million over 90 days. Monitor table/index growth,
autovacuum, request latency and retention runtime; consider time partitioning as scale grows.

## Native build and device acceptance

`expo-task-manager` supplies the existing native tasks. The audit adds `expo-battery` for supported low-power signals (unknown/older binaries fall back safely). The expo-location plugin enables Android
background and foreground-service permissions; the existing native manifest is synchronized
without prebuild or rewriting Gradle/custom native modules. Android requires fine/coarse,
background location, FOREGROUND_SERVICE and FOREGROUND_SERVICE_LOCATION permissions. The
Expo library supplies its location service and task receiver. Existing notifications remain
configured; Android may separately ask for notification permission.

**A new Android development/release build is required.** Metro alone cannot add the native
module/permissions. No APK is built or production deployment performed by this task. Android
native folders are already ignored by this project's workflow; app.json/plugin settings are
the durable configuration for generated builds. iOS background tracking is not enabled in V1.

After installing the updated native build against the local backend:

1. Sign in normally; accept the disclosure, precise foreground and background location.
2. Verify the Android tracking notification and Admin live marker.
3. Walk outdoors with the app backgrounded for several minutes, then stand still; inspect
   timestamps, accuracy and the route. Measure battery impact on the target device.
4. Disable networking while moving; restore it and verify acknowledged batches recover the route.
5. Disable GPS/revoke location permission; verify the exact app message and no new collection.
6. Log out while online and offline; verify native tasks/notification stop and another account
   cannot receive the old queue. Test midnight/session expiry and same-employee replay.
7. Try Manager/Caller/Counselor API/page access; expect 403. Select a past date and paginate.

References: [Expo SDK 57 Location](https://docs.expo.dev/versions/v57.0.0/sdk/location/),
[TaskManager](https://docs.expo.dev/versions/v57.0.0/sdk/task-manager/),
[Leaflet](https://leafletjs.com/download.html),
[OpenStreetMap tile policy](https://operations.osmfoundation.org/policies/tiles/).


## 2026-10-09 reliability audit and upgrade

See [location-tracking-audit.md](location-tracking-audit.md) for confirmed causes,
new fields and behavior, test evidence, remaining device/browser checks, and the
backup-first production rollout/rollback procedure. The new additive migration is
`accounts.0013_location_diagnostics` plus the concurrent receipt-time index in
`accounts.0014_location_received_index`; the original `0012` is unchanged.
