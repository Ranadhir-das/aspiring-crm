import hashlib
from datetime import timedelta
from decimal import Decimal
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.web.models import Project, ProjectWorkReport, WorkReport
from apps.web.project_services import (
    format_minutes_to_duration,
    merge_project_reports_json,
    parse_duration_to_minutes,
    sync_project_work_report,
)


class ProjectWorkReportPhase1Tests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user('admin-user', role='ADMIN', is_staff=True)
        self.manager = User.objects.create_user('manager-user', role='MANAGER')
        self.emp1 = User.objects.create_user('emp-it', role='IT')
        self.emp2 = User.objects.create_user('emp-video', role='VIDEO_EDITOR')
        self.other = User.objects.create_user('emp-other', role='CALLER')

        self.api_emp1 = APIClient()
        self.api_emp1.force_authenticate(self.emp1)

        self.api_admin = APIClient()
        self.api_admin.force_authenticate(self.admin)

        self.today = timezone.localdate()

    # =========================================================================
    # 1. Duration Parsing and Validation Tests
    # =========================================================================

    def test_duration_parsing_valid_formats(self):
        self.assertEqual(parse_duration_to_minutes("4 hours"), 240)
        self.assertEqual(parse_duration_to_minutes("2.5 hours"), 150)
        self.assertEqual(parse_duration_to_minutes("2h 30m"), 150)
        self.assertEqual(parse_duration_to_minutes("45m"), 45)
        self.assertEqual(parse_duration_to_minutes("45 mins"), 45)
        self.assertEqual(parse_duration_to_minutes(90), 90)
        self.assertEqual(parse_duration_to_minutes("90"), 90)
        self.assertEqual(parse_duration_to_minutes("24 hours"), 1440)

        self.assertEqual(format_minutes_to_duration(150), "2h 30m")
        self.assertEqual(format_minutes_to_duration(45), "45m")
        self.assertEqual(format_minutes_to_duration(120), "2h")

    def test_duration_parsing_rejects_invalid_values(self):
        # 0 or negative values must be rejected
        with self.assertRaises(ValidationError):
            parse_duration_to_minutes(0)
        with self.assertRaises(ValidationError):
            parse_duration_to_minutes("0 hours")
        with self.assertRaises(ValidationError):
            parse_duration_to_minutes("0 mins")
        with self.assertRaises(ValidationError):
            parse_duration_to_minutes(-5)
        with self.assertRaises(ValidationError):
            parse_duration_to_minutes("-2 hours")

        # Ambiguous bare numeric float without units must be rejected
        with self.assertRaises(ValidationError):
            parse_duration_to_minutes(2.5)
        with self.assertRaises(ValidationError):
            parse_duration_to_minutes("2.5")

        # Malformed strings must be rejected
        with self.assertRaises(ValidationError):
            parse_duration_to_minutes("invalid")
        with self.assertRaises(ValidationError):
            parse_duration_to_minutes("abc")
        with self.assertRaises(ValidationError):
            parse_duration_to_minutes("")
        with self.assertRaises(ValidationError):
            parse_duration_to_minutes(None)

        # Excessively large values (> 1440 minutes) must be rejected
        with self.assertRaises(ValidationError):
            parse_duration_to_minutes(1500)
        with self.assertRaises(ValidationError):
            parse_duration_to_minutes("25 hours")

    def test_api_duration_contract_validation(self):
        p1 = Project.objects.create(title="API Duration Project", employee=self.emp1, status=Project.Status.STARTED)

        # 1. Canonical duration_minutes (e.g. 90) succeeds
        res_ok_mins = self.api_emp1.post('/api/v1/mobile/employee/reports/', {
            'date': self.today.isoformat(),
            'project_reports': [{
                'project_id': p1.id,
                'project_name': p1.title,
                'duration_minutes': 90,
                'status': 'In Progress',
                'expected_completion_date': '2026-11-20',
                'notes': 'Worked 90 minutes',
            }],
        }, format='json')
        self.assertEqual(res_ok_mins.status_code, 200, res_ok_mins.data)

        # 2. Explicit duration_hours (e.g. 2.5) succeeds and converts to 150 mins
        res_ok_hours = self.api_emp1.post('/api/v1/mobile/employee/reports/', {
            'date': self.today.isoformat(),
            'project_reports': [{
                'project_id': p1.id,
                'project_name': p1.title,
                'duration_hours': '2.50',
                'status': 'In Progress',
                'expected_completion_date': '2026-11-20',
                'notes': 'Worked 2.5 hours',
            }],
        }, format='json')
        self.assertEqual(res_ok_hours.status_code, 200, res_ok_hours.data)

        # 3. Explicit unit string "2.5 hours" succeeds
        res_ok_str = self.api_emp1.post('/api/v1/mobile/employee/reports/', {
            'date': self.today.isoformat(),
            'project_reports': [{
                'project_id': p1.id,
                'project_name': p1.title,
                'duration': '2.5 hours',
                'status': 'In Progress',
                'expected_completion_date': '2026-11-20',
                'notes': 'Worked 2.5 hours string',
            }],
        }, format='json')
        self.assertEqual(res_ok_str.status_code, 200, res_ok_str.data)

        # 4. Zero duration is rejected with HTTP 400
        res_bad_zero = self.api_emp1.post('/api/v1/mobile/employee/reports/', {
            'date': self.today.isoformat(),
            'project_reports': [{
                'project_id': p1.id,
                'project_name': p1.title,
                'duration': '0 hours',
                'status': 'In Progress',
                'expected_completion_date': '2026-11-20',
                'notes': 'Zero hours',
            }],
        }, format='json')
        self.assertEqual(res_bad_zero.status_code, 400)

        # 5. Negative duration is rejected with HTTP 400
        res_bad_neg = self.api_emp1.post('/api/v1/mobile/employee/reports/', {
            'date': self.today.isoformat(),
            'project_reports': [{
                'project_id': p1.id,
                'project_name': p1.title,
                'duration': '-1 hour',
                'status': 'In Progress',
                'expected_completion_date': '2026-11-20',
                'notes': 'Negative hours',
            }],
        }, format='json')
        self.assertEqual(res_bad_neg.status_code, 400)

        # 6. Malformed duration is rejected with HTTP 400
        res_bad_str = self.api_emp1.post('/api/v1/mobile/employee/reports/', {
            'date': self.today.isoformat(),
            'project_reports': [{
                'project_id': p1.id,
                'project_name': p1.title,
                'duration': 'invalid-duration',
                'status': 'In Progress',
                'expected_completion_date': '2026-11-20',
                'notes': 'Invalid format',
            }],
        }, format='json')
        self.assertEqual(res_bad_str.status_code, 400)

    # =========================================================================
    # 2. Project Lifecycle and Terminal Completed Project Guards
    # =========================================================================

    def test_employee_cannot_reopen_completed_project_via_api(self):
        project = Project.objects.create(
            title="Completed Task Alpha",
            employee=self.emp1,
            status=Project.Status.COMPLETED,
            completion_reviewed_at=timezone.now(),
            reviewed_by=self.admin,
        )
        # Attempting to PATCH to STARTED or IN_PROGRESS must fail for ordinary employee
        res_reopen = self.api_emp1.patch(f'/api/v1/mobile/employee/projects/{project.id}/', {
            'status': 'STARTED',
        }, format='json')
        self.assertEqual(res_reopen.status_code, 400)
        self.assertIn("Cannot modify a project in Completed status", str(res_reopen.data))

        project.refresh_from_db()
        self.assertEqual(project.status, Project.Status.COMPLETED, "Project must remain COMPLETED!")

    def test_employee_cannot_reopen_completed_project_via_crm(self):
        project = Project.objects.create(
            title="Completed Task CRM",
            employee=self.emp1,
            status=Project.Status.COMPLETED,
            completion_reviewed_at=timezone.now(),
            reviewed_by=self.admin,
        )
        self.client.force_login(self.emp1)
        res = self.client.post(f'/projects/{project.pk}/status/', {'status': 'STARTED'})
        self.assertEqual(res.status_code, 302)

        project.refresh_from_db()
        self.assertEqual(project.status, Project.Status.COMPLETED, "CRM update by employee cannot reopen completed project!")

    def test_management_can_reopen_completed_project(self):
        project = Project.objects.create(
            title="Reopenable Project",
            employee=self.emp1,
            status=Project.Status.COMPLETED,
            completion_reviewed_at=timezone.now(),
            reviewed_by=self.admin,
        )
        # Management reopens via API
        res_reopen = self.api_admin.patch(f'/api/v1/mobile/employee/projects/{project.id}/', {
            'status': 'IN_PROGRESS',
            'reopen_reason': 'Additional client requirements added',
        }, format='json')
        self.assertEqual(res_reopen.status_code, 200)

        project.refresh_from_db()
        self.assertEqual(project.status, Project.Status.IN_PROGRESS)
        self.assertIsNone(project.completion_reviewed_at)
        self.assertIn("Additional client requirements added", project.review_comments)

    def test_employee_cannot_directly_set_completed_status(self):
        project = Project.objects.create(
            title="Direct Complete Attempt",
            employee=self.emp1,
            status=Project.Status.STARTED,
        )
        # Attempt to set COMPLETED directly redirects to completion request (PENDING_REVIEW)
        res = self.api_emp1.patch(f'/api/v1/mobile/employee/projects/{project.id}/', {
            'status': 'COMPLETED',
        }, format='json')
        self.assertEqual(res.status_code, 200)

        project.refresh_from_db()
        self.assertEqual(project.status, Project.Status.PENDING_REVIEW)
        self.assertIsNotNone(project.completion_requested_at)

    def test_employee_cannot_modify_project_while_pending_review(self):
        project = Project.objects.create(
            title="Locked in Review Project",
            employee=self.emp1,
            status=Project.Status.PENDING_REVIEW,
            completion_requested_at=timezone.now(),
        )
        # Attempt to cancel review and reset to STARTED without manager action
        res = self.api_emp1.patch(f'/api/v1/mobile/employee/projects/{project.id}/', {
            'status': 'STARTED',
        }, format='json')
        self.assertEqual(res.status_code, 400)
        self.assertIn("pending review", str(res.data))

        project.refresh_from_db()
        self.assertEqual(project.status, Project.Status.PENDING_REVIEW)

    def test_employee_cannot_assign_management_only_statuses(self):
        project = Project.objects.create(
            title="Management Status Project",
            employee=self.emp1,
            status=Project.Status.STARTED,
        )
        for bad_status in ['ON_HOLD', 'CANCELLED', 'NEEDS_CHANGES']:
            res = self.api_emp1.patch(f'/api/v1/mobile/employee/projects/{project.id}/', {
                'status': bad_status,
            }, format='json')
            self.assertEqual(res.status_code, 403, f"Status {bad_status} should be forbidden to employee!")

    # =========================================================================
    # 3. Work Entries: Multiple Entries per Project & Idempotency
    # =========================================================================

    def test_multiple_work_entries_for_same_project_on_same_date(self):
        p_alpha = Project.objects.create(title="Project Alpha Core", employee=self.emp1, status=Project.Status.STARTED)
        p_beta = Project.objects.create(title="Project Beta Docs", employee=self.emp1, status=Project.Status.STARTED)

        # 1. 10:00 — Project A — 60 minutes — Initial implementation (UUID-1)
        res1 = self.api_emp1.post('/api/v1/mobile/employee/reports/', {
            'date': self.today.isoformat(),
            'project_reports': [{
                'entry_id': 'client-uuid-1',
                'project_id': p_alpha.id,
                'project_name': p_alpha.title,
                'duration_minutes': 60,
                'status': 'In Progress',
                'expected_completion_date': '2026-11-20',
                'notes': '10:00 Initial implementation',
            }],
        }, format='json')
        self.assertEqual(res1.status_code, 200, res1.data)

        # 2. 14:00 — Project A — 45 minutes — Testing and fixes (UUID-2)
        res2 = self.api_emp1.post('/api/v1/mobile/employee/reports/', {
            'date': self.today.isoformat(),
            'project_reports': [{
                'entry_id': 'client-uuid-2',
                'project_id': p_alpha.id,
                'project_name': p_alpha.title,
                'duration_minutes': 45,
                'status': 'In Progress',
                'expected_completion_date': '2026-11-20',
                'notes': '14:00 Testing and fixes',
            }],
        }, format='json')
        self.assertEqual(res2.status_code, 200, res2.data)

        # 3. 16:00 — Project B — 30 minutes — Documentation (UUID-3)
        res3 = self.api_emp1.post('/api/v1/mobile/employee/reports/', {
            'date': self.today.isoformat(),
            'project_reports': [{
                'entry_id': 'client-uuid-3',
                'project_id': p_beta.id,
                'project_name': p_beta.title,
                'duration_minutes': 30,
                'status': 'Completed',
                'notes': '16:00 Documentation',
            }],
        }, format='json')
        self.assertEqual(res3.status_code, 200, res3.data)

        # VERIFY DATABASE STATE:
        # All three entries must exist separately in ProjectWorkReport!
        entries = ProjectWorkReport.objects.filter(employee=self.emp1, work_date=self.today).order_by('created_at')
        self.assertEqual(entries.count(), 3, "All three work sessions must exist as distinct rows!")

        e1, e2, e3 = entries[0], entries[1], entries[2]
        self.assertEqual(e1.project, p_alpha)
        self.assertEqual(e1.duration_minutes, 60)
        self.assertEqual(e1.description, '10:00 Initial implementation')
        self.assertEqual(e1.client_entry_id, 'client-uuid-1')

        self.assertEqual(e2.project, p_alpha)
        self.assertEqual(e2.duration_minutes, 45)
        self.assertEqual(e2.description, '14:00 Testing and fixes')
        self.assertEqual(e2.client_entry_id, 'client-uuid-2')

        self.assertEqual(e3.project, p_beta)
        self.assertEqual(e3.duration_minutes, 30)
        self.assertEqual(e3.description, '16:00 Documentation')
        self.assertEqual(e3.client_entry_id, 'client-uuid-3')

        # Total duration for Project Alpha
        alpha_total = sum(e.duration_minutes for e in ProjectWorkReport.objects.filter(project=p_alpha, work_date=self.today))
        self.assertEqual(alpha_total, 105)

        # Verify daily WorkReport summary contains all 3 entries in JSON
        daily = WorkReport.objects.get(employee=self.emp1, date=self.today)
        self.assertEqual(len(daily.project_reports), 3)

    def test_uuid_idempotent_retry_with_identical_payload(self):
        p = Project.objects.create(title="Retry Test Project", employee=self.emp1, status=Project.Status.STARTED)

        payload = {
            'date': self.today.isoformat(),
            'project_reports': [{
                'entry_id': 'idemp-uuid-999',
                'project_id': p.id,
                'project_name': p.title,
                'duration_minutes': 60,
                'status': 'In Progress',
                'expected_completion_date': '2026-11-20',
                'notes': 'First attempt work',
            }],
        }
        res1 = self.api_emp1.post('/api/v1/mobile/employee/reports/', payload, format='json')
        self.assertEqual(res1.status_code, 200)
        self.assertEqual(ProjectWorkReport.objects.filter(employee=self.emp1, client_entry_id='idemp-uuid-999').count(), 1)

        # Retry exact identical payload: returns 200, must NOT create a duplicate
        res2 = self.api_emp1.post('/api/v1/mobile/employee/reports/', payload, format='json')
        self.assertEqual(res2.status_code, 200)
        self.assertEqual(ProjectWorkReport.objects.filter(employee=self.emp1, client_entry_id='idemp-uuid-999').count(), 1)

    def test_conflicting_uuid_reuse_is_rejected(self):
        p = Project.objects.create(title="Conflict Test Project", employee=self.emp1, status=Project.Status.STARTED)

        payload1 = {
            'date': self.today.isoformat(),
            'project_reports': [{
                'entry_id': 'idemp-uuid-conflict',
                'project_id': p.id,
                'project_name': p.title,
                'duration_minutes': 60,
                'status': 'In Progress',
                'expected_completion_date': '2026-11-20',
                'notes': 'Original work description',
            }],
        }
        res1 = self.api_emp1.post('/api/v1/mobile/employee/reports/', payload1, format='json')
        self.assertEqual(res1.status_code, 200)
        self.assertEqual(ProjectWorkReport.objects.filter(employee=self.emp1, client_entry_id='idemp-uuid-conflict').count(), 1)

        # Reusing the same entry_id with a DIFFERENT payload must be rejected with 409 Conflict
        payload_conflicting = {
            'date': self.today.isoformat(),
            'project_reports': [{
                'entry_id': 'idemp-uuid-conflict',
                'project_id': p.id,
                'project_name': p.title,
                'duration_minutes': 120,  # Changed duration!
                'status': 'In Progress',
                'expected_completion_date': '2026-11-20',
                'notes': 'Conflicting new description',
            }],
        }
        res2 = self.api_emp1.post('/api/v1/mobile/employee/reports/', payload_conflicting, format='json')
        self.assertEqual(res2.status_code, 409)
        self.assertIn("Idempotency conflict", str(res2.data))

        # Original database record must NOT be silently overwritten!
        row = ProjectWorkReport.objects.get(employee=self.emp1, client_entry_id='idemp-uuid-conflict')
        self.assertEqual(row.duration_minutes, 60, "Duration must remain unchanged!")
        self.assertEqual(row.description, 'Original work description', "Description must remain unchanged!")

    def test_identical_legitimate_legacy_sessions_are_preserved_separately(self):
        p = Project.objects.create(title="Legacy Duplicate Session Project", employee=self.emp1, status=Project.Status.STARTED)

        # 1. First legitimate legacy session (e.g. 1 hour of routine coding in the morning without entry_id)
        res1 = self.api_emp1.post('/api/v1/mobile/employee/reports/', {
            'date': self.today.isoformat(),
            'project_reports': [{
                'project_id': p.id,
                'project_name': p.title,
                'duration': '1 hour',
                'status': 'In Progress',
                'expected_completion_date': '2026-11-20',
                'notes': 'Routine code maintenance',
            }],
        }, format='json')
        self.assertEqual(res1.status_code, 200)
        self.assertEqual(ProjectWorkReport.objects.filter(employee=self.emp1, work_date=self.today).count(), 1)

        # 2. Second legitimate legacy session with the EXACT same project, duration, and notes in the afternoon
        res2 = self.api_emp1.post('/api/v1/mobile/employee/reports/', {
            'date': self.today.isoformat(),
            'project_reports': [{
                'project_id': p.id,
                'project_name': p.title,
                'duration': '1 hour',
                'status': 'In Progress',
                'expected_completion_date': '2026-11-20',
                'notes': 'Routine code maintenance',
            }],
        }, format='json')
        self.assertEqual(res2.status_code, 200)

        # Both distinct legacy sessions are preserved without guessing or collapsing!
        entries = ProjectWorkReport.objects.filter(employee=self.emp1, work_date=self.today)
        self.assertEqual(entries.count(), 2, "Both legitimate legacy sessions must be preserved!")
        self.assertIsNone(entries[0].client_entry_id)
        self.assertIsNone(entries[1].client_entry_id)

        daily = WorkReport.objects.get(employee=self.emp1, date=self.today)
        self.assertEqual(len(daily.project_reports), 2)

    def test_different_employees_using_same_uuid_do_not_conflict(self):
        p1 = Project.objects.create(title="Emp1 Project", employee=self.emp1, status=Project.Status.STARTED)
        p2 = Project.objects.create(title="Emp2 Project", employee=self.emp2, status=Project.Status.STARTED)

        shared_uuid = 'shared-cross-employee-uuid-123'

        # Employee 1 submits with shared_uuid
        res1 = self.api_emp1.post('/api/v1/mobile/employee/reports/', {
            'date': self.today.isoformat(),
            'project_reports': [{
                'entry_id': shared_uuid,
                'project_id': p1.id,
                'project_name': p1.title,
                'duration_minutes': 60,
                'status': 'In Progress',
                'expected_completion_date': '2026-11-20',
                'notes': 'Emp1 work',
            }],
        }, format='json')
        self.assertEqual(res1.status_code, 200)

        # Employee 2 submits with the exact same shared_uuid
        api_emp2 = APIClient()
        api_emp2.force_authenticate(self.emp2)
        res2 = api_emp2.post('/api/v1/mobile/employee/reports/', {
            'date': self.today.isoformat(),
            'project_reports': [{
                'entry_id': shared_uuid,
                'project_id': p2.id,
                'project_name': p2.title,
                'duration_minutes': 90,
                'status': 'In Progress',
                'expected_completion_date': '2026-11-20',
                'notes': 'Emp2 work',
            }],
        }, format='json')
        self.assertEqual(res2.status_code, 200)

        # Both rows exist because the DB constraint is scoped to (employee, client_entry_id)
        self.assertTrue(ProjectWorkReport.objects.filter(employee=self.emp1, client_entry_id=shared_uuid).exists())
        self.assertTrue(ProjectWorkReport.objects.filter(employee=self.emp2, client_entry_id=shared_uuid).exists())

    def test_management_reopening_creates_persistent_audit_trail(self):
        from apps.web.models import AuditEvent
        project = Project.objects.create(
            title="Audited Project Reopening",
            employee=self.emp1,
            status=Project.Status.COMPLETED,
            completion_reviewed_at=timezone.now(),
            reviewed_by=self.admin,
        )

        # Admin reopens project
        res = self.api_admin.patch(f'/api/v1/mobile/employee/projects/{project.id}/', {
            'status': 'IN_PROGRESS',
            'reopen_reason': 'Client requested additional features',
        }, format='json')
        self.assertEqual(res.status_code, 200)

        project.refresh_from_db()
        self.assertEqual(project.status, Project.Status.IN_PROGRESS)
        self.assertIsNone(project.completion_reviewed_at)
        # Reason and actor persisted in review_comments
        self.assertIn("Client requested additional features", project.review_comments)
        self.assertIn(self.admin.username, project.review_comments)

        # AuditEvent persisted in database table
        audit_event = AuditEvent.objects.filter(category='PROJECT', actor=self.admin).order_by('-created_at').first()
        self.assertIsNotNone(audit_event)
        self.assertIn(f"reopened project #{project.pk}", audit_event.description)
        self.assertIn("Client requested additional features", audit_event.description)

    # =========================================================================
    # 4. CRM Web Table and Lifecycle Controls
    # =========================================================================

    def test_crm_projects_table_lifecycle_controls(self):
        p_active = Project.objects.create(title="Active Task", employee=self.emp1, status=Project.Status.STARTED)
        p_review = Project.objects.create(title="Review Task", employee=self.emp1, status=Project.Status.PENDING_REVIEW)
        p_changes = Project.objects.create(title="Changes Task", employee=self.emp1, status=Project.Status.NEEDS_CHANGES, review_comments="Fix indentation")
        p_done = Project.objects.create(title="Done Task", employee=self.emp1, status=Project.Status.COMPLETED)

        # 1. Management user views /projects/
        self.client.force_login(self.admin)
        res_mgmt = self.client.get('/projects/')
        self.assertEqual(res_mgmt.status_code, 200)
        # Management sees review options and comments
        self.assertContains(res_mgmt, "Pending review")
        self.assertContains(res_mgmt, "Needs changes")
        self.assertContains(res_mgmt, "Fix indentation")
        self.assertContains(res_mgmt, "Completed (Approve)")

        # 2. Ordinary employee views /projects/
        self.client.force_login(self.emp1)
        res_emp = self.client.get('/projects/')
        self.assertEqual(res_emp.status_code, 200)
        # Employee sees terminal badge for Completed, not an editable save form
        self.assertContains(res_emp, "Terminal (Completed)")
        # Employee sees request completion option for active projects
        self.assertContains(res_emp, "Request completion review")

    # =========================================================================
    # 5. Phase 2A.1: Project Response Serialization Tests
    # =========================================================================

    def test_project_list_serialization_includes_review_fields(self):
        p1 = Project.objects.create(
            title="Clean Project",
            employee=self.emp1,
            status=Project.Status.STARTED,
            completion_requested_at=None,
            review_comments="",
        )
        now_ts = timezone.now()
        p2 = Project.objects.create(
            title="Needs Changes Project",
            employee=self.emp1,
            status=Project.Status.NEEDS_CHANGES,
            completion_requested_at=now_ts,
            review_comments="Please add test coverage before requesting completion.",
        )

        res = self.api_emp1.get('/api/v1/mobile/employee/projects/')
        self.assertEqual(res.status_code, 200)
        projects = {p['id']: p for p in res.data['projects']}

        # Verify Project 1: null completion_requested_at and empty review_comments
        self.assertIn(p1.id, projects)
        item1 = projects[p1.id]
        self.assertIn('review_comments', item1)
        self.assertIn('completion_requested_at', item1)
        self.assertEqual(item1['review_comments'], "")
        self.assertIsNone(item1['completion_requested_at'])

        # Verify Project 2: populated review_comments and ISO timestamp
        self.assertIn(p2.id, projects)
        item2 = projects[p2.id]
        self.assertIn('review_comments', item2)
        self.assertIn('completion_requested_at', item2)
        self.assertEqual(item2['review_comments'], "Please add test coverage before requesting completion.")
        self.assertEqual(item2['completion_requested_at'], now_ts.isoformat())

    def test_project_detail_serialization_includes_review_fields(self):
        now_ts = timezone.now()
        project = Project.objects.create(
            title="Detail Serialization Project",
            employee=self.emp1,
            status=Project.Status.NEEDS_CHANGES,
            completion_requested_at=now_ts,
            review_comments="Feedback from reviewer: UI needs dark mode support.",
        )

        res = self.api_emp1.get(f'/api/v1/mobile/employee/projects/{project.id}/')
        self.assertEqual(res.status_code, 200)
        self.assertIn('review_comments', res.data)
        self.assertIn('completion_requested_at', res.data)
        self.assertEqual(res.data['review_comments'], "Feedback from reviewer: UI needs dark mode support.")
        self.assertEqual(res.data['completion_requested_at'], now_ts.isoformat())

    def test_null_completion_requested_at_is_returned_as_null(self):
        project = Project.objects.create(
            title="Unrequested Project",
            employee=self.emp1,
            status=Project.Status.STARTED,
            completion_requested_at=None,
        )

        res = self.api_emp1.get(f'/api/v1/mobile/employee/projects/{project.id}/')
        self.assertEqual(res.status_code, 200)
        self.assertIn('completion_requested_at', res.data)
        self.assertIsNone(res.data['completion_requested_at'])

    def test_review_comments_returned_correctly_after_needs_changes_review(self):
        project = Project.objects.create(
            title="Review Workflow Project",
            employee=self.emp1,
            status=Project.Status.STARTED,
        )
        # 1. Employee requests completion
        res_req = self.api_emp1.post(f'/api/v1/mobile/employee/projects/{project.id}/request-completion/')
        self.assertEqual(res_req.status_code, 200)

        # 2. Management reviews with NEEDS_CHANGES
        res_rev = self.api_admin.post(f'/api/v1/mobile/employee/projects/{project.id}/review-completion/', {
            'action': 'NEEDS_CHANGES',
            'comments': 'Please fix linting issues and re-submit.',
        }, format='json')
        self.assertEqual(res_rev.status_code, 200)

        # 3. Employee fetches project detail: comments and timestamp exposed
        res_detail = self.api_emp1.get(f'/api/v1/mobile/employee/projects/{project.id}/')
        self.assertEqual(res_detail.status_code, 200)
        self.assertEqual(res_detail.data['status'], 'NEEDS_CHANGES')
        self.assertEqual(res_detail.data['review_comments'], 'Please fix linting issues and re-submit.')
        self.assertIsNotNone(res_detail.data['completion_requested_at'])

        # 4. Employee fetches project list: comments and timestamp exposed
        res_list = self.api_emp1.get('/api/v1/mobile/employee/projects/')
        self.assertEqual(res_list.status_code, 200)
        proj_item = next(p for p in res_list.data['projects'] if p['id'] == project.id)
        self.assertEqual(proj_item['review_comments'], 'Please fix linting issues and re-submit.')
        self.assertIsNotNone(proj_item['completion_requested_at'])

    def test_project_serialization_preserves_permissions_and_visibility(self):
        p_emp2 = Project.objects.create(
            title="Private Project Emp2",
            employee=self.emp2,
            status=Project.Status.STARTED,
            review_comments="Secret feedback",
        )

        # Emp1 cannot access Emp2's project detail (404)
        res_forbidden_detail = self.api_emp1.get(f'/api/v1/mobile/employee/projects/{p_emp2.id}/')
        self.assertEqual(res_forbidden_detail.status_code, 404)

        # Emp1 does not see Emp2's project in their list
        res_list = self.api_emp1.get('/api/v1/mobile/employee/projects/')
        self.assertEqual(res_list.status_code, 200)
        emp1_project_ids = [p['id'] for p in res_list.data['projects']]
        self.assertNotIn(p_emp2.id, emp1_project_ids)

        # Admin can access Emp2's project detail and sees review fields
        res_admin = self.api_admin.get(f'/api/v1/mobile/employee/projects/{p_emp2.id}/')
        self.assertEqual(res_admin.status_code, 200)
        self.assertEqual(res_admin.data['review_comments'], "Secret feedback")
        self.assertIsNone(res_admin.data['completion_requested_at'])
