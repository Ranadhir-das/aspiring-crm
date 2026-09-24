# Django test suite

Run with the project virtual environment:

```powershell
.\venv\Scripts\python.exe manage.py check
.\venv\Scripts\python.exe manage.py test
```

`config.test_runner.AppTestRunner` discovers all automated tests under `apps/`
plus the converted root modules `test_assignment`, `test_assignment_api`,
`test_commit`, `test_import`, and `test_mobile_login`.

The other root `test_*.py` files are legacy manual smoke scripts: they query or
mutate an existing database at import time. They are retained, but excluded from
default discovery. Do not use `manage.py test .` to include those scripts. Put
new automated tests under the relevant app, with fixtures created by Django
TestCase and generated uploads rather than existing database rows/root files.

Authentication tests use `config.test_helpers.isolate_auth_throttles()` to give
each test a private in-memory throttle cache. The actual throttle classes and
rates still run. No production cache is cleared and no production throttle is
disabled. Tests making requests as independent clients use distinct test IPs.

## Repairs

| Failure | Cause and correction |
| --- | --- |
| `test_assignment.py`, `test_assignment_api.py` | Import-time scripts required existing callers/admins and unassigned leads. Converted to TestCase with generated users/leads and assignment assertions. |
| `test_commit.py`, `test_import.py` | Import-time scripts opened `test_leads.csv` relative to the working directory. Converted to TestCase using in-memory CSV uploads, including preview/no-write and duplicate import checks. |
| `test_mobile_login.py` | Expected a token immediately after credentials. Now verifies enrollment challenge, expiry, required photo, and absence of token/session; pending enrollment remains blocked. |
| Employee challenge/replay test | Called retired photo endpoints, which now return 410. Tests retain that assertion and exercise login/verify: enrollment review, one-use challenges, expiry rejection, and verified session creation. |
| Employee onboarding test | Treated pending enrollment as completed onboarding. Now requires approved enrollment; pending and rejected photos still require onboarding. |
| Workforce registration test | Expected a removed dashboard widget/context key. Account reviews intentionally live in header notifications. Tests verify the notification and inactive-directory link, role isolation, activation, and CSRF. |
| Session login test / HTTP 429 | Shared throttle state leaked between tests; test also expected a session before photo verification. Uses a private test cache and the current two-step login flow. Added mismatch/replay rejection and an explicit real-rate-limit test. |

Files changed for these repairs:

- `config/settings.py`: select the test runner only; no authentication/API changes.
- `config/test_runner.py`: discover isolated app tests and converted root tests.
- `config/test_helpers.py`: per-test throttle cache isolation.
- `test_assignment.py`, `test_assignment_api.py`, `test_commit.py`, `test_import.py`, `test_mobile_login.py`.
- `apps/web/test_employee_mobile.py`, `apps/web/test_sessions.py`, `apps/web/test_workforce.py`.
- `TESTING.md`: this guide.

Existing mobile endpoints, authorization, enrollment approval requirements,
face verification, and production throttling are unchanged.

## Verified result (2026-09-24)

- `python manage.py check`: no issues.
- `python manage.py test`: **123 tests, 0 failures, 0 errors**, completed in 53.660 seconds.
- Targeted `git diff --check`: passed.
