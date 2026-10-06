# CRM workspace UI audit

## Scope and implementation

Reviewed all 63 web templates and their shared CSS/JavaScript patterns. The automated route inventory renders 43 authenticated routes with isolated Django test fixtures: dashboard, leads/detail/import/create/routing, calls, follow-ups, counselling, admissions/create, team/employees/employee workspace/profiles, performance, sessions, services/caller eligibility, website integrations/create, chat, notices, projects/create, reports/create, feedback, attendance/photos, leaves/apply, holidays/create, payroll, expenses, customers, invoices, audit activity, consultations and distribution. Counselling details and populated admission flows also have existing regression coverage.

No standalone Students, Partners or Partner Approvals routes exist in this Django URL configuration. Django's built-in /admin/ and the separate partner project are not restyled by this change.

`workspace.css` is the final shared authenticated-workspace layer. It reuses Vaani theme tokens for widths, card insets/radii, form controls, buttons, table containment, labels, tabs, pagination, typography and wrapping. Chart grid children can shrink without widening the workspace. Mobile layouts stack fields and hide the closed sidebar's shadow. Existing screen-specific layouts remain intact.

## Employee navigation

Canonical route: `web:caller-detail` (`/team/<database-id>/`). The existing activity/performance view now accepts all employee roles. Management can inspect employees; everyone else can inspect only themselves. Non-caller profiles cannot submit caller milestone/adjustment actions. Existing caller adjustment rules remain unchanged.

For future templates use `{% load web_tags %}` then `{% employee_link record.employee %}`. This escapes names, uses the stable primary key and only renders a link when the viewer can access it. Keep names plain in form options, title/attribute text and existing action links to avoid invalid nested anchors. Generic workforce table rows retain user objects so the same helper can render names. Chat's live messages and analytics' recent activity use the same canonical route with permission-aware presentation.

Updated identity locations include leads/detail, calls/external calls/recordings, follow-ups, counselling/detail, admissions, team/eligibility/sessions, workforce reports/attendance/expenses/audit, leave/payroll/projects, chat, collaboration, performance/points history, dashboard activity, profile manager and sidebar account.

The existing profile retains filtered points ledger, trends, outcome charts and activity history. Added today's calls/points, calendar-month points, lifetime calls/interested leads/completed and overdue follow-ups, walk-in/Google Meet and admission counts, plus up to ten recent records in each activity category. Account role/status and contact information remain visible under the same profile permissions.

## Verification

- Full `apps.web` suite: 125 passed.
- Follow-up analytics/profile/workspace tests after final navigation edits: 21 passed.
- Final template/profile render checks: 11 passed.
- Django system check, CSS parsing, modified JavaScript syntax and git diff whitespace checks passed.
- Browser verification uses isolated fixture HTML, local static assets, both themes, and exact CSS viewport widths 1920, 1440, 1366, 1024, 768 and 390. External requests are blocked during the stable layout check. This checks layout, not live notification delivery, production data or realtime sockets.
- Stable browser run: 516 checks passed with no page-level horizontal overflow; internal table scrolling remains intentional.
- Local evidence is under E:/react/ui-audit-preview (screenshots and viewport JSON); tests never flush or edit the production database.

No models, migrations, mobile APIs, lead ownership rules or admission/counselling business rules were changed by this UI work. Existing unrelated working-tree edits were preserved. Nothing was committed.
