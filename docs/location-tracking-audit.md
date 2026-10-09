# Employee location reliability audit ? 2026-10-09

## Confirmed causes and scope

The audit traced Expo native callbacks ? serialized app-private queue ? existing verified
session API ? raw PostgreSQL points ? post-commit Channels invalidation ? authenticated
CRM live/history queries ? Leaflet. No authentication, attendance, leads, calls, points,
Apostille or assignment workflows were redesigned.

Confirmed by code and regression tests:

1. Live responses combined the newest point across *all* sessions with the newest session's
   active flag. An older session's observation could therefore appear LIVE after a new login.
2. Live markers/details and history shared map state. There was no explicit history mode.
   Editing a date without submitting also left old pagination/current requests usable.
3. Routes broke only on session changes/five-minute gaps. Poor-accuracy points, mock samples
   and implausible jumps could be drawn and there was no auditable distance/playback.
4. A mobile latest-point watermark discarded delayed native samples. Explicit offline logout
   discarded unacknowledged history. Initial metadata failures could leave tracking stopped
   until an app-state change. The native high-accuracy request remained active when stationary.
5. Django's configured default `Referrer-Policy: same-origin` suppressed the cross-origin
   browser referrer required by OSM. The page had no tile-error state. This is a confirmed
   policy mismatch, **not proof that it alone caused the reported production 403**.

Already correct and preserved: token-derived employee ownership, coordinate validation,
unique session/capture-time idempotency, capture-vs-receipt timestamps, transactional batches,
post-commit socket messages, role checks, pagination, and batched 90-day retention cleanup.
Business-calendar date boundaries existed; explicit timezone rendering, time filters and DST
regressions were missing. Neither old raw coordinates nor capture timestamps are rewritten.

## Resulting behavior

- Live selects the newest non-future, non-mock observation within the configured accuracy
  limit. Previous-session positions remain visibly *last known*, never LIVE/RECENT.
- Current session, foreground heartbeat online state, reported collection state/reason,
  state report time, observation age/freshness and most recent server receipt are distinct.
  No network silence or WebSocket disconnection is interpreted as GPS-off.
- Live API adds `search`, `tracking` and `status` filters, applied before pagination.
- History uses the configured business timezone, defaults to Asia/Kolkata, converts calendar
  boundaries to UTC and uses [start,end). `date` accepts optional `time_from`/`time_to`.
  Existing explicit ISO `start`/`end` remains supported (maximum 31 days). Nonexistent or
  ambiguous DST wall times are rejected with instructions to use offset-aware ISO ranges.
- Raw results remain chronological (`recorded_at`, primary key), including poor-quality
  observations. Every point adds `segment_reason`, `display_accepted` and
  `distance_from_previous_m`. Pagination includes the previous raw observation when classifying
  the first result, so page boundaries do not invent gaps or distance. Pagination URLs retain
  a server receipt-time snapshot to exclude newly received delayed batches from an ongoing
  history view; reload the range to see newly synchronized history.
- `START`, `SESSION_CHANGE`, `TIME_GAP`, `QUALITY_GAP`, `POOR_ACCURACY`, `MOCK_LOCATION` and
  `IMPLAUSIBLE_JUMP` break segments. Accepted continuous pairs contribute Haversine distance;
  speed checks allow for both points' accuracy uncertainty. Jumps are segment breaks, not
  proof that either endpoint is false. Raw points remain stored and visible in the timeline.
- Explicit Live/History buttons; historical requests and playback reset immediately on
  employee/date/time changes. Abort + generation + selection-key guards reject stale results.
  Socket refreshes cannot alter historical markers, details or playback.
- Playback visits actual stored samples only, supports speed/scrub/point jumps and visibly
  pauses at gaps. Start/end markers, per-point accuracy, raw/display counts and partial-route
  distance are labeled. No fabricated coordinates, smoothing or road matching.
- Mobile preserves out-of-order capture times, retains bounded disabled queues on failed
  logout/expiry and only uploads a queue under the same authenticated employee. Other employees'
  archived points cannot be uploaded by the new account. Queue expiry/size limits still apply.
- Foreground maintenance retries startup after transient metadata/network/GPS problems.
  UI reports permission/GPS/pause/native availability and last successful sync separately.
- `expo-battery` supplies supported low-power/battery signals. Below 20% or power saver,
  use balanced 60-second native requests; otherwise adapt moving vs stationary settings.
  Native foreground-service options change **only while visible**, as required by Expo 57.
  Background processing retains the last native request; actual delivery is OS-controlled.
  Battery values are not attached to historical samples: Expo locations do not contain a
  capture-time battery reading, and attaching today's reading to a delayed batch would be false.
- Optional diagnostic/source fields are sent only when the status API advertises
  `supports_location_diagnostics`. Old clients remain accepted and a backend rollback does
  not force new clients to submit unknown fields to the previous strict serializer.

### Configuration

All existing endpoint URLs and auth mechanisms are retained.

```dotenv
EMPLOYEE_LOCATION_LIVE_SECONDS=30
EMPLOYEE_LOCATION_RECENT_SECONDS=300
EMPLOYEE_LOCATION_STALE_SECONDS=900
EMPLOYEE_LOCATION_MAX_GAP_SECONDS=300
EMPLOYEE_LOCATION_MAX_ACCURACY_METERS=150
EMPLOYEE_LOCATION_MAX_SPEED_MPS=55
EMPLOYEE_LOCATION_TILE_URL=https://tile.openstreetmap.org/{z}/{x}/{y}.png
EMPLOYEE_LOCATION_TILE_ATTRIBUTION=&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors
```

Thresholds must be positive with live < recent < stale. Retention remains 90 days.
Unknown prior metadata remains unknown. The page/API are private, no-store; this applies to
sensitive CRM responses, **not** third-party tiles. Tile images use normal browser caching.
The page and Leaflet tile elements use `strict-origin-when-cross-origin`, sending only the
real page origin rather than employee/date URLs. No proxy, forged identity, cache bypass,
prefetch, or automatic provider switching is introduced. Attribution remains visible.

### Tile verification and remaining evidence

Reviewed the [official OSM tile policy](https://operations.osmfoundation.org/policies/tiles/).
On 2026-10-09, one request to `https://tile.openstreetmap.org/0/0/0.png` with the truthful
`VaaniLocationAudit/1.0 (+https://vaaniapp.co.in)` User-Agent returned **HTTP 200**, image/png,
6,930 bytes. Cache-Control: `max-age=520808, stale-while-revalidate=604800, stale-if-error=604800`.
This verifies provider reachability, not the original failing browser request. Browser tools
reported no available browser, so the failed production request/response headers, console,
and rendered map could not be inspected. The user was asked for that evidence.

In the affected browser, use ordinary caching (do not enable DevTools Disable cache), open
Network ? Img and inspect one tile. Confirm HTTPS, HTTP 200/304, the real CRM-origin Referer,
normal browser User-Agent/cache behavior and visible attribution. Check any reverse proxy or
browser policy that overrides Referrer-Policy. If 403 persists, retain the error state and
obtain provider clarification or approve a licensed provider with verified terms, key, quota
and attribution. No replacement key/provider was invented. Do not claim production fixed
until that request and both live/history overlays have been verified there.

## Migration and data safety

`accounts/0013_location_diagnostics.py` only adds `CallerSession.location_reason` and optional
point `source`, `platform`, `mocked`. No data conversion/backfill/deletion and no changes to
`0012`, cleanup behavior, production models outside tracking, or existing rows' coordinates.
A migration-executor test inserts an old-schema session/point, migrates and verifies identity,
coordinates, capture time, receipt time and session state are unchanged.
The additional `0014_location_received_index` creates an employee/receipt-time index with
PostgreSQL CREATE INDEX CONCURRENTLY, avoiding a full history aggregate on each live refresh.
Migration was exercised on the isolated test database only; no live schema was migrated.

## Changed files

CRM:
- `apps/accounts/api/location.py`
- `apps/accounts/location_models.py`
- `apps/accounts/location_service.py`
- `apps/accounts/models.py`
- `apps/accounts/migrations/0013_location_diagnostics.py`
- `apps/accounts/migrations/0014_location_received_index.py`
- `apps/accounts/test_locations.py`
- `apps/web/location_views.py`
- `apps/web/templates/web/employee_locations.html`
- `apps/web/static/web/employee-locations.js`
- `apps/web/static/web/employee-locations.css`
- `config/settings.py`
- `scripts/test-employee-locations.cjs`
- `docs/employee-location-tracking.md`
- `docs/location-tracking-audit.md`

Caller app:
- `src/services/locationQueue.ts`
- `src/services/employeeLocation.ts`
- `src/components/EmployeeLocationRegistration.tsx`
- `scripts/test-employee-location.cjs`
- `package.json` and `package-lock.json` (`expo-battery`, SDK-compatible version)

No native custom modules/Gradle files, existing auth/session flow, production data, or cleanup
schedules were modified. Other pre-existing untracked APK-updater files and concurrently edited
`apps/accounts/api/views.py` / `apps/web/views.py` were left untouched by this task.

## Local acceptance

```powershell
cd E:\react\aspiring-crm
.\venv\Scripts\python.exe manage.py migrate --plan
# After reviewing that this is your LOCAL database:
.\venv\Scripts\python.exe manage.py migrate accounts 0014
.\venv\Scripts\python.exe manage.py runserver 0.0.0.0:8000
```

1. Sign in to the CRM as Admin/Super Admin and open `/employee-locations/`. Confirm Manager
   and employee accounts cannot access the page, APIs or socket.
2. In the mobile project run `npm ci`; use the existing development build workflow
   (`npx expo run:android --device`) to include the battery module. Do not run prebuild --clean
   or overwrite customized Android modules. No APK was built by this audit.
3. Sign in normally on a physical Android device, grant precise/background location after
   the disclosure, and verify the foreground notification and state/sync indicator.
4. Walk outdoors, stand still, background/foreground the app, toggle power saver, deny/regrant
   permission and disable/re-enable GPS. Verify reports match the observed device state.
5. Disable network, move, restore it; capture times must remain unchanged and duplicates
   must not appear. Repeat with offline logout and same-account login. Another employee
   must never upload those retained points. Capture must stop at logout/session expiry.
6. Select a previous date and optional time range, compare raw times against UTC API results,
   rapidly switch employees/dates, load multiple pages, play/scrub and inspect gap warnings.
   Trigger a live update while viewing history: the historical route must remain unchanged.
7. Verify real tile responses and overlays as described above. Check empty/error states.

Physical movement, manufacturer battery behavior, actual background delivery and a rendered
browser map were not verified in this tool session. Native-module mocks test logic, not GPS.
ADB reported zero connected devices during this audit. Force-stop/OS termination can stop
collection until the employee reopens the app. Expiry shutdown is checked on native callbacks
and foreground maintenance; Android may delay callbacks, but expired samples are not uploaded
or accepted by the server. Sampling
and accuracy are requests, never guarantees. Offline storage is limited to 2 days and 5,000
current + 5,000 archived points, with expiry applied when processed. The map is limited to
10,000 loaded points; narrow the time range for larger histories. Distance is a noisy GPS
segment measurement and never a certified road/travel distance.

## Safe production procedure ? not executed

Use the existing `/opt/vaani` and `vaani.service` deployment layout. Deployment is separate.
First arrange a restricted PostgreSQL service entry (`~/.pg_service.conf`, mode 0600), named
`vaani_backup`, using the correct production DB and a backup/verification operator. Do not
put credentials in command arguments, source control or this document. It needs permission
to create a separate private restore-verification database; stop if it cannot do so.

Run these commands as the authorized operator, with PostgreSQL client tools matching the server:

```bash
set -euo pipefail
umask 077
export PGSERVICE=vaani_backup
VAANI_DATABASE="$(psql -X -A -t -c 'SELECT current_database()')"
VAANI_STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
VAANI_BACKUP_DIR="/var/backups/vaani/location-$VAANI_STAMP"
mkdir -p "$VAANI_BACKUP_DIR"
VAANI_BACKUP_FILE="$VAANI_BACKUP_DIR/database.dump"
pg_dump --format=custom --file="$VAANI_BACKUP_FILE"
test -s "$VAANI_BACKUP_FILE"
pg_restore --list "$VAANI_BACKUP_FILE" > "$VAANI_BACKUP_DIR/contents.txt"
sha256sum "$VAANI_BACKUP_FILE" > "$VAANI_BACKUP_FILE.sha256"
sha256sum --check "$VAANI_BACKUP_FILE.sha256"
VAANI_VERIFY_DATABASE="vaani_location_verify_$VAANI_STAMP"
test "$VAANI_VERIFY_DATABASE" != "$VAANI_DATABASE"
createdb --maintenance-db="$VAANI_DATABASE" --template=template0 "$VAANI_VERIFY_DATABASE"
# Revoke PUBLIC connection before restoring sensitive data into this separate database.
psql -X --dbname="$VAANI_DATABASE" -v ON_ERROR_STOP=1 -v verify_db="$VAANI_VERIFY_DATABASE" <<'SQL'
REVOKE CONNECT ON DATABASE :"verify_db" FROM PUBLIC;
SQL
pg_restore --exit-on-error --single-transaction --no-owner --no-privileges \
  --dbname="$VAANI_VERIFY_DATABASE" "$VAANI_BACKUP_FILE"
psql -X --dbname="$VAANI_VERIFY_DATABASE" -v ON_ERROR_STOP=1 \
  -c 'SELECT count(*) AS preserved_location_points FROM accounts_employeelocationpoint;' \
  -c 'SELECT count(*) AS preserved_sessions FROM accounts_callersession;'
```

Only continue after the full restore succeeds. The verification DB is separate; do not run
restore against production. Keep it access-restricted and handle its later disposal using
normal approved backup policy. Save the current code revision and static artifact before rollout.
Stage the reviewed release using your normal release process, then:

```bash
cd /opt/vaani
sudo -u www-data ./venv/bin/python manage.py check
sudo -u www-data ./venv/bin/python manage.py migrate --plan
sudo -u www-data ./venv/bin/python manage.py sqlmigrate accounts 0014
# Stop if the plan contains unexpected migrations; never use --fake.
sudo -u www-data ./venv/bin/python manage.py migrate accounts 0014 --noinput
sudo -u www-data ./venv/bin/python manage.py collectstatic --noinput
sudo systemctl restart vaani
sudo systemctl is-active vaani
sudo journalctl -u vaani --since '5 minutes ago' --no-pager
sudo -u www-data ./venv/bin/python manage.py check
sudo -u www-data ./venv/bin/python manage.py showmigrations accounts
sudo systemctl status vaani-location-cleanup.timer --no-pager
sudo -u www-data ./venv/bin/python manage.py cleanup_location_history --dry-run
```

If concurrent index creation fails, stop and have the database operator inspect
`pg_index.indisvalid` for `employee_location_received`; do not fake the migration or blindly
retry over an invalid index. Have the operator repair only that new index before retrying.

Do not run actual cleanup to repair routes. Keep the existing approved 90-day timer unchanged.
Verify Redis configuration for multi-worker invalidations, then the authenticated smoke tests,
permissions, historical queries and real browser tile request. Only then roll out the tested
mobile native build through the existing release process. Do not print tokens/coordinates in logs.

Rollback: restore the previously saved application/static release using the normal release
mechanism, run collectstatic for that release and restart `vaani`. **Leave migrations 0013/0014 and
all recorded data in place**: the added nullable/default fields are compatible with old code.
Do not reverse migrations, restore a backup over a live DB, delete location rows or reset
sessions. The new mobile client capability-negotiates diagnostic payloads with old servers.
A serious data incident requires a separately reviewed recovery, not automatic overwrite.

## Validation results

Confirmed in this review:

* `.\venv\Scripts\python.exe manage.py test apps.accounts.test_locations --keepdb` — 32 tests passed.
* `node scripts/test-employee-locations.cjs` — 8 tests passed.
* `.\venv\Scripts\python.exe manage.py check` — no issues identified.
* `.\venv\Scripts\python.exe manage.py makemigrations --check --dry-run` — no changes detected.
* `git diff --check` — no whitespace errors.

Physical-device location delivery, background behavior, and the rendered browser map remain unverified. Mobile-project validation commands are not included in the confirmed results above.
