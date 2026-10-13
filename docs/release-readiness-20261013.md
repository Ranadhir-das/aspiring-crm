# CRM release preparation — 2026-10-13

This is a preparation checklist, not a record of production deployment.

## Reviewed change set

Branch: `main`. Starting commit: `072648a`.
Remote: `https://github.com/Ranadhir-das/aspiring-crm.git` (`origin/main`).

| File | Purpose |
| --- | --- |
| `apps/calls/api/views.py` | Convert unknown Interested contacts into caller-owned external leads; complete due reminders transactionally |
| `apps/calls/phone.py` | Share normalized-phone variants and PostgreSQL creation locks |
| `apps/calls/test_direct_workflow.py` | Conversion, points, forwarding, concurrency, permissions, rollback and follow-up regressions |
| `apps/calls/test_external_calls.py` | Preserve call-only tests for non-converting outcomes |
| `apps/followups/services.py` | Resolve due reminders only after a successfully persisted call |
| `apps/followups/api/mobile_views.py` | Due-only option and caller/lead ownership filtering |
| `apps/followups/api/views.py` | Atomic reminder updates and consistent lock order |
| `apps/leads/test_website_outcomes.py` | Verify due completion without changing website ownership or future appointments |
| `docs/release-readiness-20261013.md` | Deployment preparation and rollback guide |

Approved business rule: an unknown Interested Direct Dialer contact becomes a lead
with source `EXTERNAL`, assigned to the authenticated caller. The selected course
becomes its original preference, so the existing own-course points rule applies.
No historical contacts are converted; other unknown-number outcomes remain call-only.
The number is the initial name because Direct Dialer does not collect a name.
Existing lead preferences, scoring/ledger rules and counselor service remain unchanged.

The existing mobile follow-up endpoint accepts `?due=true`; the default feed still
includes future pending appointments. Call-create responses additionally return
`completed_followup_ids`. Existing clients may ignore this extra field.

## Validation

- Full Django suite: **686 passed**, no failures/errors, including authentication,
  imports, manual assignment, calls, counselor workflows and PostgreSQL concurrency.
- Targeted call/course/counselor/follow-up suite: **94 passed**.
- `manage.py check`: passed.
- `manage.py makemigrations --check --dry-run`: no changes detected.
- `manage.py migrate --plan`: no pending operations in the local database; no
  migrations were applied by this task.
- No configured Ruff, flake8 or coverage command was found. No tool was installed
  merely to invent a passing lint/coverage result.
- Local `manage.py check --deploy`: failed with the development console-email
  backend and six development-security warnings (HSTS, HTTPS redirect, secret-key
  quality, secure session/CSRF cookies, DEBUG). These are **local** results; production
  settings were not read and production readiness is not established by unit tests.

No new migrations or environment variables are introduced by this pending change.
The previously committed Apostille release includes `leads/0026` and `calls/0009`;
the operator must compare production's applied migrations with the selected release.
Do not assume the local migration plan describes production.

## Deployment checklist (operator only; not executed)

1. Confirm the reviewed Git commit, database backup, media/private-file backup and
   previous working release are available. Record current updater metadata/APK hash.
2. Stage the new checkout independently; preserve `/opt/vaani/.env`, uploaded media,
   private attachments/recordings, face models and the live APK. Do not run `git clean`.
3. Use the existing virtual environment and pinned `requirements.txt`. No dependency
   changes are needed for this CRM patch. PostgreSQL and Redis are existing requirements.
4. Inspect `./venv/bin/python manage.py migrate --plan` on the server. Review any
   previously unapplied Apostille/location migrations before applying them. There is
   no schema migration for the Direct Dialer/follow-up fix itself.
5. Run `./venv/bin/python manage.py check` and `check --deploy` with the real production
   configuration. Resolve DEBUG, strong-secret, HTTPS/proxy/cookie and email settings
   there; do not copy this workstation's development environment.
6. Use the normal `collectstatic --noinput` procedure if deploying previous CRM UI
   changes as well. This particular pending patch adds no static assets.
7. The checked-in `deploy/vaani.service` describes **Daphne/ASGI**, service `vaani`,
   working directory `/opt/vaani`, environment `/opt/vaani/.env`, socket
   `/run/vaani/daphne.sock`, and user `www-data`. Verify the installed unit before use.
   It serves HTTP and team-chat WebSockets. Do not replace it with Gunicorn blindly.
8. During the separately authorized deployment, restart the verified `vaani` service;
   inspect `systemctl is-active vaani` and `journalctl -u vaani --since '5 minutes ago'`.
9. Read-only smoke checks: `curl --fail --head https://vaaniapp.co.in/app/` and the
   public APK download. The protected mobile API should return 401 without a token.
   With a test employee, verify login, one normal call, external Interested points,
   counselor assignment and due reminder completion. Never place tokens in URLs or logs.
10. Publish a new APK/metadata only after the mobile release is separately verified.
    Use `/app/download/`, not the older missing `/dist/vaani.apk` path. Set the actual
    verified release version/code and preserve the existing mandatory/minimum policy.

No SSH, production restart, production migration, APK replacement, settings edit,
or production-data mutation was performed in this audit.

## Rollback

Restore the previous application release and its dependencies/static artifacts,
then restart the verified service and repeat smoke checks. This patch adds no schema
changes. Leave already applied backward-compatible migrations in place unless a
separately reviewed migration rollback is required. Do not delete external leads,
call history, completed follow-ups or awarded ledger entries created after rollout.
Restore prior updater metadata and APK only as an operator-controlled publishing
rollback; Android cannot normally downgrade an installed version code. A mobile
hotfix should retain the same certificate/package and use a higher version code.
