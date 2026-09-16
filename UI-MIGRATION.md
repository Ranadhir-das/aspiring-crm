# Vaani web workspace

Open `http://localhost:8000/` and sign in with an existing CRM username and password.
Super Admin, Admin and Manager roles have calling-management access; callers see assigned leads. IT, Video Editor, Employee and Accountant roles can now sign in to the employee workspace. Salary and finance records are restricted to Super Admin, Admin and Accountant; other employees can see only their own salary records.
The web UI uses Django sessions and CSRF-protected forms. The mobile token APIs are unchanged.

## What changed from templates.zip

The archive contains Flask/Jinja templates (`url_for`, `current_user`, Flask-specific model fields). They cannot run directly in this Django project. The new `apps/web` app adapts the supported workflows to the current Django models, using the old archive as a reference rather than installing its scripts or routes.

| Old reference | New workspace |
| --- | --- |
| base.html, index.html, login.html | Responsive sidebar, account navigation and sign-in page |
| super_dashboard.html, admin_dashboard.html, employee_dashboard.html | Role-aware overview with real database totals, call-volume graph, lead-status donut, sources, recent calls and follow-ups |
| employee_leads.html, admin_leads.html, admin_assigned_leads.html | Searchable, paginated lead directory, status/caller filters and management-only bulk assignment |
| leads_from_csv.html, import_leads.html | CSV/XLSX preview and import, using existing duplicate detection and import history |
| website_leads.html | Lead source shown in directory and source analytics; no separate website-ingestion backend exists |
| lead_history_partial.html | Lead details with call history, profile editing and follow-up scheduling |
| activity_log.html | Call activity page (not a general system audit log) |
| Employee summaries | Caller team page with workload, interested leads and call counts |

## Employee and operations migration

The second implementation adds persisted Django models and workflows for the old employee/operations pages:

| Old feature | New route and behavior |
| --- | --- |
| Employee dashboards (all departments) | `/employee/`: attendance, own projects, leave summary, reports and due follow-up reminders |
| Registration and employee approval | `/register/`: creates an inactive Employee account; `/employees/` lets Admin/Super Admin activate accounts and assign roles. Managers may view the directory but cannot grant roles. Self-editing of access and promotion beyond the actor's authority are blocked. |
| Super/admin attendance overview | `/people/`: active employees by role and today's attendance; approved leave and holidays are shown. Unmarked days are not automatically classified as absences. |
| Leave application and approvals | `/leave/`, `/leave/apply/`: dates, reason, overlap validation, pending/approved/rejected/cancelled states, review notes and reviewer history. Management cannot approve its own leave. |
| Attendance | `/attendance/`: office/WFH/week-off marking, check-in/out and date/employee filters; elapsed session hours, not computer-activity surveillance |
| Holiday administration/calendar | `/holidays/`: month/year calendar plus add, edit and remove controls for management |
| Project assignment/status | `/projects/`: management assigns work to any active employee; employee updates their own project status |
| Work reports/manual feedback | `/reports/`: one report per employee/day, work link, notes, feedback counts, management reporting on behalf of employees, date/employee filters and CSV export; missing-report list for today |
| All caller feedback statistics | `/feedback/`: date range and employee filters; outcome distribution and employee breakdown. Recorded-call counts use the actual caller, even after a lead is reassigned. Manual report counts are a separate source and are never added to recorded calls. |
| Payroll and employee queries | `/payroll/`: record/edit salary payments, credited amount and deductions, employee-owned queries, finance resolution notes |
| Accountant dashboard | `/finance/`: today's received payments, expenses, salary records and open queries |
| Expenses | `/expenses/`: categories from the old template, amounts, dates, descriptions and recorded-by history; voiding preserves the original entry |
| Customers, billing, revenue and payments | `/customers/`, `/invoices/`: customer records, line items, quantities, room reference, explicit tax rate, decimal totals, payment mode/reference, outstanding balance and printable invoices |
| Activity log | `/activity/`: employee/management mutations with actor/category/time and date/actor filtering |
| Source/date bulk allocations | `/leads/distribute/`: per-caller counts, capacity validation, preview and optional reassignment; existing assignment history is preserved |
| Advanced lead feedback/notes/consultation | Lead profile → Notes, qualification & consultation: sub-status, temperature, append-only notes and management consultation queue at `/consultations/` |

WhatsApp and chat are **excluded at the user's request**; an internal chat system will be a later task. Nothing in this update sends messages through external services.

The ZIP contains templates only, not the old database or backend. Existing data in the new CRM is used; old employee/finance records cannot be migrated from HTML templates. Dynamic legacy feedback categories were not defined in the ZIP's backend; the manual report uses the current call outcomes plus the extra legacy statuses visible in the templates.

Finance screens record transactions that already occurred; they do not execute payments. Amount credited is net of deductions and is not deducted a second time. Invoice tax is user-entered, not an assumed statutory rate. Follow-up reminders are in-app; no background push/email service is configured.

## Design and data

- Graphite background with violet, mint and blue accents; responsive layouts and readable status badges.
- Charts use local SVG/CSS and real, role-filtered data. No chart CDN or fabricated production metrics.
- Period selector covers 7, 30 or 90 days of call activity. Other cards and lead distributions are all-time, except the explicitly labeled Calls today card.
- Chart values are also available in an accessible data table. Empty datasets have explicit empty states.
- Dates use the configured Django timezone (currently UTC), shown in the footer and scheduling form.
- Google Fonts enhance typography when reachable; system fonts remain available offline.
- All mutations use POST and CSRF checks. Assignment calls the existing service to preserve assignment history. Callers cannot import, assign, create leads or access the team directory. Department employees cannot access the lead/call workspace.
- Photos selected in the mobile app remain device-local and are not available to the web UI.

## Run and validate

```powershell
.\venv\Scripts\python.exe manage.py runserver 0.0.0.0:8000
.\venv\Scripts\python.exe manage.py migrate
.\venv\Scripts\python.exe manage.py check
.\venv\Scripts\python.exe manage.py test apps.web --settings=apps.web.test_settings
```

Tests use an isolated SQLite database, not the configured CRM database. Migrations `accounts.0003_alter_user_role` and `web.0001_initial` have been applied locally. They add employee role choices and operations tables without modifying existing lead/call records. Restart an existing server started with `--noreload` to discover the new routes.

For deployment, configure your usual Django static-file collection/serving. The development server serves the app assets when DEBUG is enabled.

Validation completed: 25 isolated tests; Django checks; no pending model migrations; JavaScript syntax; rendering employee/management pages against the configured CRM database; live login response. Tests cover access isolation, role escalation, CSRF, overlapping leave and decisions, attendance, reports and export privacy, call/manual statistics separation, invoice totals/overpayment, assignment preview/history, and inactive registration. Browser visual QA remains unverified because this session has no connected browser.
