import re
from datetime import timedelta
from django.test import TestCase, TransactionTestCase, skipUnlessDBFeature
from django.urls import reverse
from django.utils import timezone
from django.contrib.admin.sites import AdminSite
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.web.models import Project, ProjectWorkReport, WorkReport, AuditEvent
from apps.web.admin import ProjectAdmin


class CrmProjectReviewTests(TestCase):
    def setUp(self):
        self.superadmin = User.objects.create_user('superadmin-crm', role='SUPER_ADMIN', is_superuser=True, is_staff=True)
        self.admin = User.objects.create_user('admin-crm', role='ADMIN', is_staff=True)
        self.manager = User.objects.create_user('manager-crm', role='MANAGER')
        self.emp1 = User.objects.create_user('emp1-crm', role='IT')
        self.emp2 = User.objects.create_user('emp2-crm', role='VIDEO_EDITOR')
        self.caller = User.objects.create_user('caller-crm', role='CALLER')
        self.counselor = User.objects.create_user('counselor-crm', role='COUNSELOR')
        self.accountant = User.objects.create_user('accountant-crm', role='ACCOUNTANT')

        self.today = timezone.localdate()

    # =========================================================================
    # 1. Permission and Role Isolation Tests
    # =========================================================================

    def test_management_roles_can_access_project_detail_and_review(self):
        project = Project.objects.create(
            title="Portal Redesign",
            employee=self.emp1,
            status=Project.Status.PENDING_REVIEW,
            completion_requested_at=timezone.now()
        )

        for mgmt_user in [self.superadmin, self.admin, self.manager]:
            self.client.force_login(mgmt_user)
            res = self.client.get(reverse('web:project-detail', args=[project.pk]))
            self.assertEqual(res.status_code, 200)
            self.assertContains(res, "Portal Redesign")
            self.assertContains(res, "Review Completion")

    def test_ordinary_roles_cannot_perform_review_or_reopen(self):
        project_pending = Project.objects.create(
            title="Pending Task",
            employee=self.emp1,
            status=Project.Status.PENDING_REVIEW,
            completion_requested_at=timezone.now()
        )
        project_completed = Project.objects.create(
            title="Completed Task",
            employee=self.emp1,
            status=Project.Status.COMPLETED
        )

        for denied_user in [self.emp1, self.emp2, self.caller, self.counselor, self.accountant]:
            self.client.force_login(denied_user)

            # 1. Cannot review
            res_rev = self.client.post(reverse('web:project-review', args=[project_pending.pk]), {
                'action': 'APPROVE',
                'comments': 'Hacked'
            })
            self.assertEqual(res_rev.status_code, 403)

            # 2. Cannot reopen
            res_reopen = self.client.post(reverse('web:project-reopen', args=[project_completed.pk]), {
                'reopen_reason': 'Hacked'
            })
            self.assertEqual(res_reopen.status_code, 403)

    def test_self_review_restrictions(self):
        """Admin and Manager cannot review their own project; Super Admin can."""
        proj_admin = Project.objects.create(
            title="Admin Own Task",
            employee=self.admin,
            status=Project.Status.PENDING_REVIEW,
            completion_requested_at=timezone.now()
        )
        proj_manager = Project.objects.create(
            title="Manager Own Task",
            employee=self.manager,
            status=Project.Status.PENDING_REVIEW,
            completion_requested_at=timezone.now()
        )
        proj_super = Project.objects.create(
            title="SuperAdmin Own Task",
            employee=self.superadmin,
            status=Project.Status.PENDING_REVIEW,
            completion_requested_at=timezone.now()
        )

        # 1. Admin attempts to self-approve: blocked
        self.client.force_login(self.admin)
        res_admin = self.client.post(reverse('web:project-review', args=[proj_admin.pk]), {
            'action': 'APPROVE',
            'comments': 'Self approval attempt'
        }, follow=True)
        proj_admin.refresh_from_db()
        self.assertEqual(proj_admin.status, Project.Status.PENDING_REVIEW)
        self.assertContains(res_admin, "Employees cannot review their own project completion")

        # 2. Manager attempts to self-approve: blocked
        self.client.force_login(self.manager)
        res_mgr = self.client.post(reverse('web:project-review', args=[proj_manager.pk]), {
            'action': 'APPROVE',
            'comments': 'Self approval attempt'
        }, follow=True)
        proj_manager.refresh_from_db()
        self.assertEqual(proj_manager.status, Project.Status.PENDING_REVIEW)
        self.assertContains(res_mgr, "Employees cannot review their own project completion")

        # 3. Super Admin self-review: permitted per existing domain method
        self.client.force_login(self.superadmin)
        res_super = self.client.post(reverse('web:project-review', args=[proj_super.pk]), {
            'action': 'APPROVE',
            'comments': 'SuperAdmin verified own delivery'
        }, follow=True)
        proj_super.refresh_from_db()
        self.assertEqual(proj_super.status, Project.Status.COMPLETED)
        self.assertEqual(proj_super.reviewed_by, self.superadmin)

    # =========================================================================
    # 2. Lifecycle Review Actions (Approve, Needs Changes, Reject)
    # =========================================================================

    def test_approve_action_workflow_and_audit(self):
        project = Project.objects.create(
            title="Website Migration",
            employee=self.emp1,
            status=Project.Status.PENDING_REVIEW,
            completion_requested_at=timezone.now()
        )
        self.client.force_login(self.admin)

        res = self.client.post(reverse('web:project-review', args=[project.pk]), {
            'action': 'APPROVE',
            'comments': 'Delivered flawlessly ahead of schedule.'
        }, follow=True)

        self.assertRedirects(res, reverse('web:project-detail', args=[project.pk]))
        project.refresh_from_db()
        self.assertEqual(project.status, Project.Status.COMPLETED)
        self.assertEqual(project.reviewed_by, self.admin)
        self.assertIsNotNone(project.completion_reviewed_at)
        self.assertEqual(project.review_comments, 'Delivered flawlessly ahead of schedule.')

        # Verify persistent AuditEvent created with complete comments
        event = AuditEvent.objects.filter(category='PROJECT', actor=self.admin).latest('created_at')
        self.assertIn(f"project #{project.pk}", event.description.lower())
        self.assertIn("APPROVE", event.description)
        self.assertIn("Delivered flawlessly ahead of schedule.", event.description)

    def test_needs_changes_action_workflow_and_audit(self):
        project = Project.objects.create(
            title="Billing Module",
            employee=self.emp1,
            status=Project.Status.PENDING_REVIEW,
            completion_requested_at=timezone.now()
        )
        self.client.force_login(self.manager)

        # 1. Missing feedback comments should fail
        res_fail = self.client.post(reverse('web:project-review', args=[project.pk]), {
            'action': 'NEEDS_CHANGES',
            'comments': '   '
        }, follow=True)
        project.refresh_from_db()
        self.assertEqual(project.status, Project.Status.PENDING_REVIEW)
        self.assertContains(res_fail, "Feedback comments are required when requesting changes.")

        # 2. With valid feedback comments
        res_ok = self.client.post(reverse('web:project-review', args=[project.pk]), {
            'action': 'NEEDS_CHANGES',
            'comments': 'Missing unit tests for invoice rounding edge cases.'
        }, follow=True)
        project.refresh_from_db()
        self.assertEqual(project.status, Project.Status.NEEDS_CHANGES)
        self.assertEqual(project.reviewed_by, self.manager)
        self.assertEqual(project.review_comments, 'Missing unit tests for invoice rounding edge cases.')

        event = AuditEvent.objects.filter(category='PROJECT', actor=self.manager).latest('created_at')
        self.assertIn("NEEDS_CHANGES", event.description)
        self.assertIn("Missing unit tests for invoice rounding edge cases.", event.description)

    def test_reject_action_workflow_and_audit(self):
        project = Project.objects.create(
            title="Video Reel",
            employee=self.emp2,
            status=Project.Status.PENDING_REVIEW,
            completion_requested_at=timezone.now()
        )
        self.client.force_login(self.admin)

        res = self.client.post(reverse('web:project-review', args=[project.pk]), {
            'action': 'REJECT',
            'comments': 'Only 30% of key frames rendered. Continue working.'
        }, follow=True)

        project.refresh_from_db()
        self.assertEqual(project.status, Project.Status.IN_PROGRESS)
        self.assertEqual(project.reviewed_by, self.admin)
        self.assertEqual(project.review_comments, 'Only 30% of key frames rendered. Continue working.')

        event = AuditEvent.objects.filter(category='PROJECT', actor=self.admin).latest('created_at')
        self.assertIn("REJECT", event.description)
        self.assertIn("Only 30% of key frames rendered.", event.description)

    def test_review_non_pending_project_fails(self):
        project = Project.objects.create(
            title="In-Progress Task",
            employee=self.emp1,
            status=Project.Status.IN_PROGRESS
        )
        self.client.force_login(self.admin)

        res = self.client.post(reverse('web:project-review', args=[project.pk]), {
            'action': 'APPROVE',
            'comments': 'premature approval'
        }, follow=True)

        project.refresh_from_db()
        self.assertEqual(project.status, Project.Status.IN_PROGRESS)
        self.assertContains(res, "Project is not pending review")

    def test_review_normal_username_accepted_350_char_comment(self):
        """A normal username with a 350-character comment fits within 500-char audit limit and is accepted."""
        project = Project.objects.create(
            title="Normal User 350 Comment Task",
            employee=self.emp1,
            status=Project.Status.PENDING_REVIEW,
            completion_requested_at=timezone.now()
        )
        self.client.force_login(self.admin)

        comment_350 = "A" * 350
        res = self.client.post(reverse('web:project-review', args=[project.pk]), {
            'action': 'APPROVE',
            'comments': comment_350
        }, follow=True)

        self.assertRedirects(res, reverse('web:project-detail', args=[project.pk]))
        project.refresh_from_db()
        self.assertEqual(project.status, Project.Status.COMPLETED)
        self.assertEqual(project.reviewed_by, self.admin)
        self.assertEqual(len(project.review_comments), 350)
        self.assertEqual(project.review_comments, comment_350)

        # Audit event contains full 350-char comment without silent truncation
        event = AuditEvent.objects.filter(category='PROJECT', actor=self.admin).latest('created_at')
        self.assertLessEqual(len(event.description), 500)
        self.assertIn(comment_350, event.description)

    def test_review_long_username_fitting_comment(self):
        """A user with a long username can submit a comment that exactly fits remaining audit space."""
        long_username = "mgr_" + "x" * 136  # 140 chars total (<= 150)
        long_manager = User.objects.create_user(username=long_username, role=User.Role.MANAGER, password="password")
        project = Project.objects.create(
            title="Fitting Comment Task",
            employee=self.emp1,
            status=Project.Status.PENDING_REVIEW,
            completion_requested_at=timezone.now()
        )
        self.client.force_login(long_manager)

        audit_prefix = f'Management {long_username} reviewed project #{project.pk} (NEEDS_CHANGES): "'
        max_comment_length = 500 - len(audit_prefix) - 1
        fitting_comment = "B" * max_comment_length

        res = self.client.post(reverse('web:project-review', args=[project.pk]), {
            'action': 'NEEDS_CHANGES',
            'comments': fitting_comment
        }, follow=True)

        self.assertRedirects(res, reverse('web:project-detail', args=[project.pk]))
        project.refresh_from_db()
        self.assertEqual(project.status, Project.Status.NEEDS_CHANGES)
        self.assertEqual(project.reviewed_by, long_manager)
        self.assertEqual(project.review_comments, fitting_comment)

        # Audit description must be exactly 500 chars and contain the entire comment
        event = AuditEvent.objects.filter(category='PROJECT', actor=long_manager).latest('created_at')
        self.assertEqual(len(event.description), 500)
        self.assertIn(fitting_comment, event.description)

    def test_review_long_username_oversized_comment_rejected_without_state_change(self):
        """Oversized comments for long usernames are rejected before modifying state or creating audit events."""
        long_username = "mgr_" + "y" * 136  # 140 chars
        long_manager = User.objects.create_user(username=long_username, role=User.Role.MANAGER, password="password")
        project = Project.objects.create(
            title="Oversized Comment Task",
            employee=self.emp1,
            status=Project.Status.PENDING_REVIEW,
            completion_requested_at=timezone.now()
        )
        self.client.force_login(long_manager)

        audit_prefix = f'Management {long_username} reviewed project #{project.pk} (NEEDS_CHANGES): "'
        max_comment_length = 500 - len(audit_prefix) - 1
        oversized_comment = "Z" * (max_comment_length + 1)

        initial_audit_count = AuditEvent.objects.filter(category='PROJECT').count()

        res = self.client.post(reverse('web:project-review', args=[project.pk]), {
            'action': 'NEEDS_CHANGES',
            'comments': oversized_comment
        }, follow=True)

        self.assertRedirects(res, reverse('web:project-detail', args=[project.pk]))
        self.assertContains(res, f"Review comments cannot exceed {max_comment_length} characters")

        # Project state and review fields must remain untouched
        project.refresh_from_db()
        self.assertEqual(project.status, Project.Status.PENDING_REVIEW)
        self.assertIsNone(project.reviewed_by)
        self.assertIsNone(project.completion_reviewed_at)
        self.assertEqual(project.review_comments, "")

        # No incomplete or misleading audit event created
        self.assertEqual(AuditEvent.objects.filter(category='PROJECT').count(), initial_audit_count)
        self.assertFalse(AuditEvent.objects.filter(category='PROJECT', description__contains="Z" * 10).exists())

    # =========================================================================
    # 3. Reopening Workflow Tests
    # =========================================================================

    def test_reopen_completed_project_with_audit(self):
        project = Project.objects.create(
            title="Released Mobile App",
            employee=self.emp1,
            status=Project.Status.COMPLETED,
            completion_reviewed_at=timezone.now(),
            reviewed_by=self.admin,
            review_comments="Initial release approved"
        )
        self.client.force_login(self.manager)

        res = self.client.post(reverse('web:project-reopen', args=[project.pk]), {
            'reopen_reason': 'Critical crash discovered on Android 14 devices.'
        }, follow=True)

        project.refresh_from_db()
        self.assertEqual(project.status, Project.Status.IN_PROGRESS)
        self.assertIsNone(project.completion_reviewed_at)
        self.assertIn("Initial release approved", project.review_comments)
        self.assertIn("Critical crash discovered on Android 14 devices.", project.review_comments)
        self.assertIn(f"Reopened by {self.manager.username}", project.review_comments)

        event = AuditEvent.objects.filter(category='PROJECT', actor=self.manager).latest('created_at')
        self.assertIn(f"project #{project.pk}", event.description.lower())
        self.assertIn("Critical crash discovered on Android 14 devices.", event.description)

    def test_reopen_requires_non_empty_reason(self):
        project = Project.objects.create(
            title="Closed Project",
            employee=self.emp1,
            status=Project.Status.COMPLETED
        )
        self.client.force_login(self.admin)

        res = self.client.post(reverse('web:project-reopen', args=[project.pk]), {
            'reopen_reason': '   '
        }, follow=True)

        project.refresh_from_db()
        self.assertEqual(project.status, Project.Status.COMPLETED)
        self.assertContains(res, "A non-empty reopening reason is required")

    def test_reopen_non_completed_project_fails(self):
        project = Project.objects.create(
            title="Started Project",
            employee=self.emp1,
            status=Project.Status.STARTED
        )
        self.client.force_login(self.admin)

        res = self.client.post(reverse('web:project-reopen', args=[project.pk]), {
            'reopen_reason': 'Accidental reopening attempt'
        }, follow=True)

        project.refresh_from_db()
        self.assertEqual(project.status, Project.Status.STARTED)
        self.assertContains(res, "Only completed projects can be reopened")

    def test_reopen_long_username_fitting_reason(self):
        """A user with a long username can reopen with a reason that exactly fits remaining audit space."""
        long_username = "adm_" + "k" * 136  # 140 chars total
        long_admin = User.objects.create_user(username=long_username, role=User.Role.ADMIN, is_staff=True, password="password")
        project = Project.objects.create(
            title="Completed Task Reopen Test",
            employee=self.emp1,
            status=Project.Status.COMPLETED,
            completion_reviewed_at=timezone.now(),
            reviewed_by=self.admin,
            review_comments="Initial release approved"
        )
        self.client.force_login(long_admin)

        audit_prefix = f"Project #{project.pk} reopened by {long_username}: "
        max_reason_len = 500 - len(audit_prefix)
        fitting_reason = "R" * max_reason_len

        res = self.client.post(reverse('web:project-reopen', args=[project.pk]), {
            'reopen_reason': fitting_reason
        }, follow=True)

        self.assertRedirects(res, reverse('web:project-detail', args=[project.pk]))
        project.refresh_from_db()
        self.assertEqual(project.status, Project.Status.IN_PROGRESS)
        self.assertIsNone(project.completion_reviewed_at)
        self.assertIn(fitting_reason, project.review_comments)

        event = AuditEvent.objects.filter(category='PROJECT', actor=long_admin).latest('created_at')
        self.assertEqual(len(event.description), 500)
        self.assertIn(fitting_reason, event.description)

    def test_reopen_oversized_reason_rejected_without_state_change(self):
        """Oversized reopening reasons are rejected without changing project status, review fields, or creating audit events."""
        long_username = "adm_" + "z" * 136  # 140 chars
        long_admin = User.objects.create_user(username=long_username, role=User.Role.ADMIN, is_staff=True, password="password")
        initial_reviewed_at = timezone.now()
        project = Project.objects.create(
            title="Oversized Reopen Task",
            employee=self.emp1,
            status=Project.Status.COMPLETED,
            completion_reviewed_at=initial_reviewed_at,
            reviewed_by=self.admin,
            review_comments="Completed initially"
        )
        self.client.force_login(long_admin)

        audit_prefix = f"Project #{project.pk} reopened by {long_username}: "
        max_reason_len = 500 - len(audit_prefix)
        oversized_reason = "W" * (max_reason_len + 1)

        initial_audit_count = AuditEvent.objects.filter(category='PROJECT').count()

        res = self.client.post(reverse('web:project-reopen', args=[project.pk]), {
            'reopen_reason': oversized_reason
        }, follow=True)

        self.assertRedirects(res, reverse('web:project-detail', args=[project.pk]))
        self.assertContains(res, f"Reopening reason cannot exceed {max_reason_len} characters")

        # Project state and review fields must remain completely unchanged
        project.refresh_from_db()
        self.assertEqual(project.status, Project.Status.COMPLETED)
        self.assertEqual(project.completion_reviewed_at, initial_reviewed_at)
        self.assertEqual(project.reviewed_by, self.admin)
        self.assertEqual(project.review_comments, "Completed initially")

        # No incomplete audit event created
        self.assertEqual(AuditEvent.objects.filter(category='PROJECT').count(), initial_audit_count)
        self.assertFalse(AuditEvent.objects.filter(category='PROJECT', description__contains="W" * 10).exists())

    # =========================================================================
    # 4. Status Update Route Hardening Tests
    # =========================================================================

    def test_generic_project_update_blocks_direct_completed_setting_by_management(self):
        project = Project.objects.create(
            title="Active Task",
            employee=self.emp1,
            status=Project.Status.STARTED
        )
        self.client.force_login(self.admin)

        res = self.client.post(reverse('web:project-update', args=[project.pk]), {
            'status': 'COMPLETED'
        }, follow=True)

        project.refresh_from_db()
        self.assertEqual(project.status, Project.Status.STARTED)
        self.assertContains(res, "Approving project completion must be performed through the Project Review workflow")

    def test_generic_project_update_blocks_direct_needs_changes_setting_by_management(self):
        project = Project.objects.create(
            title="Active Task",
            employee=self.emp1,
            status=Project.Status.STARTED
        )
        self.client.force_login(self.admin)

        res = self.client.post(reverse('web:project-update', args=[project.pk]), {
            'status': 'NEEDS_CHANGES'
        }, follow=True)

        project.refresh_from_db()
        self.assertEqual(project.status, Project.Status.STARTED)
        self.assertContains(res, "Requesting changes must be performed through the Project Review workflow")

    def test_generic_project_update_blocks_reopening_completed_project(self):
        project = Project.objects.create(
            title="Done Project",
            employee=self.emp1,
            status=Project.Status.COMPLETED
        )
        self.client.force_login(self.admin)

        res = self.client.post(reverse('web:project-update', args=[project.pk]), {
            'status': 'IN_PROGRESS'
        }, follow=True)

        project.refresh_from_db()
        self.assertEqual(project.status, Project.Status.COMPLETED)
        self.assertContains(res, "Completed projects must be reopened through the dedicated Reopen Project workflow")

    def test_generic_project_update_preserves_permitted_transitions_and_employee_requests(self):
        project = Project.objects.create(
            title="Task Lifecycle",
            employee=self.emp1,
            status=Project.Status.ASSIGNED
        )

        # 1. Management sets STARTED: permitted
        self.client.force_login(self.admin)
        self.client.post(reverse('web:project-update', args=[project.pk]), {'status': 'STARTED'})
        project.refresh_from_db()
        self.assertEqual(project.status, Project.Status.STARTED)

        # 2. Employee requests completion: routes to PENDING_REVIEW
        self.client.force_login(self.emp1)
        self.client.post(reverse('web:project-update', args=[project.pk]), {'status': 'COMPLETED'})
        project.refresh_from_db()
        self.assertEqual(project.status, Project.Status.PENDING_REVIEW)
        self.assertIsNotNone(project.completion_requested_at)

    # =========================================================================
    # 5. Mobile API Generic PATCH Hardening Tests
    # =========================================================================

    def test_mobile_api_patch_blocks_management_from_directly_setting_completed(self):
        project = Project.objects.create(
            title="API Project",
            employee=self.emp1,
            status=Project.Status.STARTED
        )
        api_admin = APIClient()
        api_admin.force_authenticate(self.admin)

        res = api_admin.patch(f'/api/v1/mobile/employee/projects/{project.pk}/', {
            'status': 'COMPLETED'
        })
        self.assertEqual(res.status_code, 400)
        self.assertIn("Management must use the dedicated review-completion endpoint", res.data['detail'])

        project.refresh_from_db()
        self.assertEqual(project.status, Project.Status.STARTED)

    # =========================================================================
    # 6. Django Admin Protection Tests
    # =========================================================================

    def test_django_admin_protects_lifecycle_fields(self):
        admin_site = AdminSite()
        project_admin = ProjectAdmin(Project, admin_site)
        readonly = project_admin.get_readonly_fields(None)
        self.assertIn('status', readonly)
        self.assertIn('completion_requested_at', readonly)
        self.assertIn('completion_reviewed_at', readonly)
        self.assertIn('reviewed_by', readonly)
        self.assertIn('review_comments', readonly)

    # =========================================================================
    # 7. Duration Aggregation and Reporting Tests
    # =========================================================================

    def test_duration_aggregation_from_project_work_reports(self):
        project_zero = Project.objects.create(title="Zero Hours Task", employee=self.emp1)
        project_work = Project.objects.create(title="Multi Session Task", employee=self.emp1)

        # Zero reports produces 0
        self.assertEqual(project_zero.total_duration_minutes, 0)
        self.assertEqual(project_zero.total_duration_formatted, "0m")

        # Session 1: 90 mins (1h 30m)
        ProjectWorkReport.objects.create(
            project=project_work,
            employee=self.emp1,
            work_date=self.today,
            duration_minutes=90,
            description="Initial coding session"
        )
        # Session 2: 45 mins (45m)
        ProjectWorkReport.objects.create(
            project=project_work,
            employee=self.emp1,
            work_date=self.today,
            duration_minutes=45,
            description="Code review and cleanup"
        )

        project_work.refresh_from_db()
        self.assertEqual(project_work.total_duration_minutes, 135)
        self.assertEqual(project_work.total_duration_formatted, "2h 15m")

    def test_no_double_counting_with_legacy_json(self):
        """Verifies duration aggregates exclusively from ProjectWorkReport without double counting WorkReport JSON."""
        project = Project.objects.create(title="Dual Storage Task", employee=self.emp1)

        daily_report = WorkReport.objects.create(
            employee=self.emp1,
            date=self.today,
            project_reports=[{
                'project_id': project.pk,
                'project_name': project.title,
                'duration': '120 mins',
                'status': 'In Progress'
            }]
        )
        ProjectWorkReport.objects.create(
            project=project,
            employee=self.emp1,
            daily_report=daily_report,
            work_date=self.today,
            duration_minutes=120,
            description="Frontend development"
        )

        project.refresh_from_db()
        self.assertEqual(project.total_duration_minutes, 120)
        self.assertEqual(project.total_duration_formatted, "2h")

    # =========================================================================
    # 8. Audit Trail Word-Bounded Matching and History Limit Tests
    # =========================================================================

    def test_audit_trail_avoids_matching_cross_project_ids(self):
        """Ensures project #1 does not display audit records belonging to project #10 or #100."""
        p1 = Project.objects.create(title="Project One", employee=self.emp1)
        p10 = Project.objects.create(title="Project Ten", employee=self.emp1)
        p100 = Project.objects.create(title="Project Hundred", employee=self.emp1)

        AuditEvent.objects.create(actor=self.admin, category='PROJECT', description=f'Assigned project #{p1.pk} to employee #5')
        AuditEvent.objects.create(actor=self.admin, category='PROJECT', description=f'Assigned project #{p10.pk} to employee #5')
        AuditEvent.objects.create(actor=self.admin, category='PROJECT', description=f'Assigned project #{p100.pk} to employee #5')

        self.client.force_login(self.admin)
        res_p1 = self.client.get(reverse('web:project-detail', args=[p1.pk]))
        self.assertContains(res_p1, f"Assigned project #{p1.pk}")
        self.assertNotContains(res_p1, f"Assigned project #{p10.pk}")
        self.assertNotContains(res_p1, f"Assigned project #{p100.pk}")

    def test_audit_history_older_event_survives_over_100_newer_events(self):
        """Database-level filtering ensures an older event for a project is not pushed out by >100 newer events for other projects."""
        p1 = Project.objects.create(title="Project One Target", employee=self.emp1)
        p_other = Project.objects.create(title="Other Project", employee=self.emp2)

        # 1. Create older event for target project p1
        older_target_event = AuditEvent.objects.create(
            actor=self.admin,
            category='PROJECT',
            description=f'Assigned project #{p1.pk} to employee #{self.emp1.pk}'
        )

        # 2. Create 115 newer events for other projects
        for i in range(115):
            AuditEvent.objects.create(
                actor=self.admin,
                category='PROJECT',
                description=f'Assigned project #{p_other.pk} to employee #{self.emp2.pk}'
            )

        self.client.force_login(self.admin)
        res = self.client.get(reverse('web:project-detail', args=[p1.pk]))
        self.assertEqual(res.status_code, 200)

        # Target event must survive and appear in the rendered detail and context
        audit_events = res.context['audit_events']
        self.assertIn(older_target_event, audit_events)
        self.assertContains(res, f"Assigned project #{p1.pk}")
        self.assertNotContains(res, f"Assigned project #{p_other.pk}")

    def test_audit_history_ordering_newest_first_and_50_limit(self):
        """Verifies audit events for a project are ordered newest-first and capped at 50."""
        project = Project.objects.create(title="High Traffic Project", employee=self.emp1)
        now = timezone.now()

        # Create 55 audit events with strictly incrementing timestamps
        created_events = []
        for i in range(55):
            ev = AuditEvent.objects.create(
                actor=self.admin,
                category='PROJECT',
                description=f'Project #{project.pk} status changed from STARTED to IN_PROGRESS by admin (iter {i})'
            )
            # Update created_at directly to ensure distinct descending timestamps
            AuditEvent.objects.filter(pk=ev.pk).update(created_at=now + timedelta(seconds=i * 10))
            ev.refresh_from_db()
            created_events.append(ev)

        self.client.force_login(self.admin)
        res = self.client.get(reverse('web:project-detail', args=[project.pk]))
        self.assertEqual(res.status_code, 200)

        audit_events = res.context['audit_events']
        # Must cap at exactly 50
        self.assertEqual(len(audit_events), 50)

        # Must be ordered newest first
        for i in range(len(audit_events) - 1):
            self.assertGreaterEqual(audit_events[i].created_at, audit_events[i + 1].created_at)

        # The newest event (iter 54) must be first
        self.assertEqual(audit_events[0].description, f'Project #{project.pk} status changed from STARTED to IN_PROGRESS by admin (iter 54)')
        # The oldest event in the 50-limit should be iter 5 (iters 0..4 are excluded by limit)
        self.assertEqual(audit_events[-1].description, f'Project #{project.pk} status changed from STARTED to IN_PROGRESS by admin (iter 5)')

    # =========================================================================
    # 9. Project Detail View and Secure Photo Endpoint Tests
    # =========================================================================

    def test_project_detail_view_permissions_and_timeline(self):
        project = Project.objects.create(title="Secret Task", employee=self.emp1)
        ProjectWorkReport.objects.create(
            project=project,
            employee=self.emp1,
            work_date=self.today,
            duration_minutes=60,
            description="Investigated database indexing",
            reference_link="https://github.com/example/pr/123"
        )

        # 1. Assigned employee can view
        self.client.force_login(self.emp1)
        res_emp1 = self.client.get(reverse('web:project-detail', args=[project.pk]))
        self.assertEqual(res_emp1.status_code, 200)
        self.assertContains(res_emp1, "Secret Task")
        self.assertContains(res_emp1, "Investigated database indexing")
        self.assertContains(res_emp1, "https://github.com/example/pr/123")

        # 2. Other ordinary employee cannot view (scoped returns 404)
        self.client.force_login(self.emp2)
        res_emp2 = self.client.get(reverse('web:project-detail', args=[project.pk]))
        self.assertEqual(res_emp2.status_code, 404)

        # 3. Manager can view
        self.client.force_login(self.manager)
        res_mgr = self.client.get(reverse('web:project-detail', args=[project.pk]))
        self.assertEqual(res_mgr.status_code, 200)

    def test_secure_project_work_report_photo_endpoint(self):
        project = Project.objects.create(title="Design Task", employee=self.emp1)
        dummy_jpeg = b'\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x01\x00`\x00`\x00\x00\xff\xdb\x00C\x00\xff\xd9'
        pwr = ProjectWorkReport.objects.create(
            project=project,
            employee=self.emp1,
            work_date=self.today,
            duration_minutes=30,
            description="Logo proof",
            photo=dummy_jpeg
        )

        # 1. Assigned employee can download photo
        self.client.force_login(self.emp1)
        res = self.client.get(reverse('web:project-work-report-photo', args=[pwr.pk]))
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res['Content-Type'], 'image/jpeg')
        self.assertEqual(res['X-Content-Type-Options'], 'nosniff')
        self.assertIn('private', res['Cache-Control'])

        # 2. Management can download photo
        self.client.force_login(self.admin)
        res_admin = self.client.get(reverse('web:project-work-report-photo', args=[pwr.pk]))
        self.assertEqual(res_admin.status_code, 200)

        # 3. Unauthorized employee blocked (403)
        self.client.force_login(self.emp2)
        res_blocked = self.client.get(reverse('web:project-work-report-photo', args=[pwr.pk]))
        self.assertEqual(res_blocked.status_code, 403)

    # =========================================================================
    # 11. SEC-01: Work-Report Completion Review Bypass Prevention Tests
    # =========================================================================

    def test_ordinary_employee_reporting_completed_requests_review_not_approval(self):
        """Ordinary employee reporting Completed transitions project to PENDING_REVIEW, not COMPLETED."""
        project = Project.objects.create(title="Emp Work Task", employee=self.emp1, status=Project.Status.STARTED)
        api = APIClient()
        api.force_authenticate(self.emp1)

        res = api.post('/api/v1/mobile/employee/reports/', {
            'date': self.today.isoformat(),
            'project_reports': [{
                'entry_id': 'sec01-uuid-emp',
                'project_id': project.id,
                'project_name': project.title,
                'duration_minutes': 60,
                'status': 'Completed',
                'notes': 'Finished dev work',
            }],
        }, format='json')
        self.assertEqual(res.status_code, 200, res.data)

        project.refresh_from_db()
        self.assertEqual(project.status, Project.Status.PENDING_REVIEW)
        self.assertIsNone(project.reviewed_by)
        self.assertIsNone(project.completion_reviewed_at)
        self.assertIsNotNone(project.completion_requested_at)

    def test_admin_reporting_completed_requests_review_not_approval(self):
        """Admin reporting Completed transitions project to PENDING_REVIEW, not COMPLETED."""
        project = Project.objects.create(title="Admin Work Task", employee=self.admin, status=Project.Status.IN_PROGRESS)
        api = APIClient()
        api.force_authenticate(self.admin)

        res = api.post('/api/v1/mobile/employee/reports/', {
            'date': self.today.isoformat(),
            'project_reports': [{
                'entry_id': 'sec01-uuid-admin',
                'project_id': project.id,
                'project_name': project.title,
                'duration_minutes': 45,
                'status': 'Completed',
                'notes': 'Admin completed own deliverables',
            }],
        }, format='json')
        self.assertEqual(res.status_code, 200, res.data)

        project.refresh_from_db()
        self.assertEqual(project.status, Project.Status.PENDING_REVIEW)
        self.assertIsNone(project.reviewed_by)
        self.assertIsNone(project.completion_reviewed_at)
        self.assertIsNotNone(project.completion_requested_at)

    def test_manager_reporting_completed_on_own_project_cannot_self_approve(self):
        """Manager reporting Completed on their own assigned project cannot self-approve; sets PENDING_REVIEW."""
        project = Project.objects.create(title="Manager Own Task", employee=self.manager, status=Project.Status.STARTED)
        api = APIClient()
        api.force_authenticate(self.manager)

        res = api.post('/api/v1/mobile/employee/reports/', {
            'date': self.today.isoformat(),
            'project_reports': [{
                'entry_id': 'sec01-uuid-mgr-self',
                'project_id': project.id,
                'project_name': project.title,
                'duration_minutes': 90,
                'status': 'Completed',
                'notes': 'Manager finished implementation',
            }],
        }, format='json')
        self.assertEqual(res.status_code, 200, res.data)

        project.refresh_from_db()
        self.assertEqual(project.status, Project.Status.PENDING_REVIEW)
        self.assertIsNone(project.reviewed_by)
        self.assertIsNone(project.completion_reviewed_at)
        self.assertIsNotNone(project.completion_requested_at)

    def test_manager_reporting_completed_on_other_employee_project_cannot_bypass_review(self):
        """Manager reporting Completed on another employee's project cannot bypass review; sets PENDING_REVIEW."""
        project = Project.objects.create(title="Delegated Task", employee=self.emp1, status=Project.Status.IN_PROGRESS)
        api = APIClient()
        api.force_authenticate(self.manager)

        res = api.post('/api/v1/mobile/employee/reports/', {
            'date': self.today.isoformat(),
            'project_reports': [{
                'entry_id': 'sec01-uuid-mgr-other',
                'project_id': project.id,
                'project_name': project.title,
                'duration_minutes': 30,
                'status': 'Completed',
                'notes': 'Assisted employee with final phase',
            }],
        }, format='json')
        self.assertEqual(res.status_code, 200, res.data)

        project.refresh_from_db()
        self.assertEqual(project.status, Project.Status.PENDING_REVIEW)
        self.assertIsNone(project.reviewed_by)
        self.assertIsNone(project.completion_reviewed_at)

    def test_superadmin_reporting_completed_does_not_bypass_formal_review(self):
        """Super Admin reporting Completed via work report also transitions to PENDING_REVIEW."""
        project = Project.objects.create(title="Super Admin Task", employee=self.superadmin, status=Project.Status.STARTED)
        api = APIClient()
        api.force_authenticate(self.superadmin)

        res = api.post('/api/v1/mobile/employee/reports/', {
            'date': self.today.isoformat(),
            'project_reports': [{
                'entry_id': 'sec01-uuid-super',
                'project_id': project.id,
                'project_name': project.title,
                'duration_minutes': 60,
                'status': 'Completed',
                'notes': 'Superadmin work session',
            }],
        }, format='json')
        self.assertEqual(res.status_code, 200, res.data)

        project.refresh_from_db()
        self.assertEqual(project.status, Project.Status.PENDING_REVIEW)
        self.assertIsNone(project.reviewed_by)
        self.assertIsNone(project.completion_reviewed_at)

    def test_dedicated_review_endpoint_still_approves_eligible_projects(self):
        """After work report requests completion, dedicated management review endpoint successfully approves."""
        project = Project.objects.create(title="Work Report to Review Task", employee=self.emp1, status=Project.Status.STARTED)
        api = APIClient()
        api.force_authenticate(self.emp1)

        # 1. Report work as Completed -> PENDING_REVIEW
        api.post('/api/v1/mobile/employee/reports/', {
            'date': self.today.isoformat(),
            'project_reports': [{
                'entry_id': 'sec01-flow-1',
                'project_id': project.id,
                'project_name': project.title,
                'duration_minutes': 60,
                'status': 'Completed',
                'notes': 'Ready for review',
            }],
        }, format='json')
        project.refresh_from_db()
        self.assertEqual(project.status, Project.Status.PENDING_REVIEW)

        # 2. Formal management review via dedicated endpoint approves project
        self.client.force_login(self.manager)
        res_review = self.client.post(reverse('web:project-review', args=[project.pk]), {
            'action': 'APPROVE',
            'comments': 'Formal approval by manager after code inspection.',
        }, follow=True)
        self.assertRedirects(res_review, reverse('web:project-detail', args=[project.pk]))

        project.refresh_from_db()
        self.assertEqual(project.status, Project.Status.COMPLETED)
        self.assertEqual(project.reviewed_by, self.manager)
        self.assertIsNotNone(project.completion_reviewed_at)
        self.assertEqual(project.review_comments, 'Formal approval by manager after code inspection.')

    def test_repeated_or_already_pending_completion_submission_leaves_state_consistent(self):
        """Submitting a second Completed entry for a project already in PENDING_REVIEW leaves state consistent."""
        project = Project.objects.create(
            title="Already Pending Task",
            employee=self.emp1,
            status=Project.Status.PENDING_REVIEW,
            completion_requested_at=timezone.now()
        )
        api = APIClient()
        api.force_authenticate(self.emp1)

        res = api.post('/api/v1/mobile/employee/reports/', {
            'date': self.today.isoformat(),
            'project_reports': [{
                'entry_id': 'sec01-repeat-1',
                'project_id': project.id,
                'project_name': project.title,
                'duration_minutes': 30,
                'status': 'Completed',
                'notes': 'Extra fix while pending review',
            }],
        }, format='json')
        self.assertEqual(res.status_code, 200, res.data)

        project.refresh_from_db()
        self.assertEqual(project.status, Project.Status.PENDING_REVIEW)
        self.assertIsNone(project.reviewed_by)
        self.assertIsNone(project.completion_reviewed_at)

    def test_work_report_idempotency_and_json_mirror_preserved(self):
        """Work report idempotency, relational ProjectWorkReport rows, and JSON mirror remain intact."""
        project = Project.objects.create(title="Idempotency Task", employee=self.emp1, status=Project.Status.STARTED)
        api = APIClient()
        api.force_authenticate(self.emp1)

        payload = {
            'date': self.today.isoformat(),
            'project_reports': [{
                'entry_id': 'sec01-idemp-1',
                'project_id': project.id,
                'project_name': project.title,
                'duration_minutes': 45,
                'status': 'In Progress',
                'expected_completion_date': (self.today + timedelta(days=5)).isoformat(),
                'notes': 'Midday progress',
            }],
        }
        res1 = api.post('/api/v1/mobile/employee/reports/', payload, format='json')
        self.assertEqual(res1.status_code, 200)

        # Retry identical payload
        res2 = api.post('/api/v1/mobile/employee/reports/', payload, format='json')
        self.assertEqual(res2.status_code, 200)

        # Verify exactly one relational ProjectWorkReport exists
        reports = ProjectWorkReport.objects.filter(employee=self.emp1, client_entry_id='sec01-idemp-1')
        self.assertEqual(reports.count(), 1)

        # Verify daily report JSON mirror
        daily = WorkReport.objects.get(employee=self.emp1, date=self.today)
        self.assertEqual(len(daily.project_reports), 1)

    # =========================================================================
    # 12. API-01: Mobile Reopening Reason Length Validation Tests
    # =========================================================================

    def test_mobile_reopen_normal_username_and_valid_reason_succeeds(self):
        """Normal username with a valid reopening reason succeeds via mobile API and records full reason in audit."""
        project = Project.objects.create(
            title="Mobile Reopened Task",
            employee=self.emp1,
            status=Project.Status.COMPLETED,
            completion_reviewed_at=timezone.now(),
            reviewed_by=self.admin,
            review_comments="Initial release approved"
        )
        api = APIClient()
        api.force_authenticate(self.manager)

        res = api.patch(f'/api/v1/mobile/employee/projects/{project.pk}/', {
            'status': 'IN_PROGRESS',
            'reopen_reason': 'New client requirements requested for Phase 2.',
        }, format='json')
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(res.data['status'], 'IN_PROGRESS')

        project.refresh_from_db()
        self.assertEqual(project.status, Project.Status.IN_PROGRESS)
        self.assertIsNone(project.completion_reviewed_at)
        self.assertIn("New client requirements requested for Phase 2.", project.review_comments)

        event = AuditEvent.objects.filter(category='PROJECT', actor=self.manager).latest('created_at')
        self.assertIn("New client requirements requested for Phase 2.", event.description)
        self.assertLessEqual(len(event.description), 500)

    def test_mobile_reopen_long_username_fitting_reason_succeeds(self):
        """Long valid username with a reason exactly filling available audit space succeeds without truncation."""
        long_username = "adm_" + "m" * 136  # 140 chars total
        long_admin = User.objects.create_user(username=long_username, role=User.Role.ADMIN, is_staff=True, password='pw')
        project = Project.objects.create(
            title="Mobile Long Admin Reopen Task",
            employee=self.emp1,
            status=Project.Status.COMPLETED,
            completion_reviewed_at=timezone.now(),
            reviewed_by=self.admin,
            review_comments="Done"
        )
        audit_prefix = f'Management {long_username} reopened project #{project.pk}: '
        max_reason_len = 500 - len(audit_prefix)
        fitting_reason = "R" * max_reason_len

        api = APIClient()
        api.force_authenticate(long_admin)

        res = api.patch(f'/api/v1/mobile/employee/projects/{project.pk}/', {
            'status': 'IN_PROGRESS',
            'reopen_reason': fitting_reason,
        }, format='json')
        self.assertEqual(res.status_code, 200, res.data)

        project.refresh_from_db()
        self.assertEqual(project.status, Project.Status.IN_PROGRESS)
        self.assertIsNone(project.completion_reviewed_at)

        event = AuditEvent.objects.filter(category='PROJECT', actor=long_admin).latest('created_at')
        self.assertEqual(len(event.description), 500)
        self.assertIn(fitting_reason, event.description)

    def test_mobile_reopen_oversized_reason_rejected_with_http_400(self):
        """Oversized reopening reason returns HTTP 400, leaving project fields and audit events completely untouched."""
        long_username = "adm_" + "n" * 136  # 140 chars total
        long_admin = User.objects.create_user(username=long_username, role=User.Role.ADMIN, is_staff=True, password='pw')
        initial_reviewed_at = timezone.now()
        project = Project.objects.create(
            title="Mobile Oversized Reopen Task",
            employee=self.emp1,
            status=Project.Status.COMPLETED,
            completion_reviewed_at=initial_reviewed_at,
            reviewed_by=self.admin,
            review_comments="Initial release approved"
        )
        audit_prefix = f'Management {long_username} reopened project #{project.pk}: '
        max_reason_len = 500 - len(audit_prefix)
        oversized_reason = "O" * (max_reason_len + 1)

        initial_audit_count = AuditEvent.objects.filter(category='PROJECT').count()

        api = APIClient()
        api.force_authenticate(long_admin)

        res = api.patch(f'/api/v1/mobile/employee/projects/{project.pk}/', {
            'status': 'IN_PROGRESS',
            'reopen_reason': oversized_reason,
        }, format='json')
        self.assertEqual(res.status_code, 400)
        self.assertIn("Reopening reason cannot exceed", res.data['detail'])

        # Lifecycle fields must remain untouched
        project.refresh_from_db()
        self.assertEqual(project.status, Project.Status.COMPLETED)
        self.assertEqual(project.completion_reviewed_at, initial_reviewed_at)
        self.assertEqual(project.reviewed_by, self.admin)
        self.assertEqual(project.review_comments, "Initial release approved")

        # Zero audit events created
        self.assertEqual(AuditEvent.objects.filter(category='PROJECT').count(), initial_audit_count)
        self.assertFalse(AuditEvent.objects.filter(category='PROJECT', description__contains="O" * 10).exists())

    def test_unauthorized_employee_cannot_use_mobile_reopening_branch(self):
        """Ordinary employee attempting to reopen completed project via mobile PATCH receives 400 or 403."""
        project = Project.objects.create(
            title="Completed Task Sealed",
            employee=self.emp1,
            status=Project.Status.COMPLETED,
            completion_reviewed_at=timezone.now(),
            reviewed_by=self.admin,
        )
        api = APIClient()
        api.force_authenticate(self.emp1)

        res = api.patch(f'/api/v1/mobile/employee/projects/{project.pk}/', {
            'status': 'IN_PROGRESS',
            'reopen_reason': 'Employee unauthorized reopen attempt',
        }, format='json')
        self.assertIn(res.status_code, {400, 403})

        project.refresh_from_db()
        self.assertEqual(project.status, Project.Status.COMPLETED)

    def test_existing_valid_web_reopening_remains_unchanged(self):
        """Existing web reopening workflow remains functional and unchanged."""
        project = Project.objects.create(
            title="Web Reopen Target",
            employee=self.emp1,
            status=Project.Status.COMPLETED,
            completion_reviewed_at=timezone.now(),
            reviewed_by=self.admin,
            review_comments="Initial approval"
        )
        self.client.force_login(self.manager)

        res = self.client.post(reverse('web:project-reopen', args=[project.pk]), {
            'reopen_reason': 'Reopening via Web CRM interface.',
        }, follow=True)
        self.assertRedirects(res, reverse('web:project-detail', args=[project.pk]))

        project.refresh_from_db()
        self.assertEqual(project.status, Project.Status.IN_PROGRESS)
        self.assertIsNone(project.completion_reviewed_at)
        self.assertIn("Reopening via Web CRM interface.", project.review_comments)

    def test_valid_completion_request_moves_project_to_pending_review(self):
        """Ordinary employee reporting 'Completed' on an active project moves it to PENDING_REVIEW."""
        project = Project.objects.create(
            title="Phase 2F.1.1 Valid Completion",
            employee=self.emp1,
            status=Project.Status.IN_PROGRESS
        )
        api = APIClient()
        api.force_authenticate(self.emp1)

        res = api.post('/api/v1/mobile/employee/reports/', {
            'date': self.today.isoformat(),
            'notes': 'Completed project work',
            'project_reports': [{
                'project_id': project.pk,
                'project_name': project.title,
                'status': 'Completed',
                'duration': '2h',
            }]
        }, format='json')
        self.assertEqual(res.status_code, 200, res.data)

        project.refresh_from_db()
        self.assertEqual(project.status, Project.Status.PENDING_REVIEW)
        self.assertIsNotNone(project.completion_requested_at)

    def test_completion_request_on_on_hold_project_fails_and_rolls_back(self):
        """Reporting 'Completed' on an ON_HOLD project raises ValidationError, returns HTTP 400, and rolls back transaction."""
        project = Project.objects.create(
            title="Phase 2F.1.1 On Hold Task",
            employee=self.emp1,
            status=Project.Status.ON_HOLD
        )
        initial_work_reports = WorkReport.objects.filter(employee=self.emp1).count()
        initial_project_reports = ProjectWorkReport.objects.filter(employee=self.emp1).count()

        api = APIClient()
        api.force_authenticate(self.emp1)

        res = api.post('/api/v1/mobile/employee/reports/', {
            'date': self.today.isoformat(),
            'notes': 'Attempt to complete on hold project',
            'project_reports': [{
                'project_id': project.pk,
                'project_name': project.title,
                'status': 'Completed',
                'duration': '1h',
            }]
        }, format='json')
        self.assertEqual(res.status_code, 400)
        self.assertIn("Cannot request completion", str(res.data))

        # Project status must remain ON_HOLD
        project.refresh_from_db()
        self.assertEqual(project.status, Project.Status.ON_HOLD)

        # Entire transaction rolled back: zero work reports persisted
        self.assertEqual(WorkReport.objects.filter(employee=self.emp1).count(), initial_work_reports)
        self.assertEqual(ProjectWorkReport.objects.filter(employee=self.emp1).count(), initial_project_reports)

    def test_unauthorized_completion_request_rolls_back_entire_work_report(self):
        """Unauthorized employee attempting to report on another employee's project raises PermissionError, returns 400, and rolls back."""
        project = Project.objects.create(
            title="Isolated Project For Emp2",
            employee=self.emp2,
            status=Project.Status.IN_PROGRESS
        )
        initial_work_reports = WorkReport.objects.filter(employee=self.emp1).count()
        initial_project_reports = ProjectWorkReport.objects.filter(employee=self.emp1).count()

        api = APIClient()
        api.force_authenticate(self.emp1)

        res = api.post('/api/v1/mobile/employee/reports/', {
            'date': self.today.isoformat(),
            'notes': 'Unauthorized work report attempt',
            'project_reports': [{
                'project_id': project.pk,
                'project_name': project.title,
                'status': 'Completed',
                'duration': '1h',
            }]
        }, format='json')
        self.assertEqual(res.status_code, 400)
        self.assertIn("not permitted", str(res.data.get('detail', '')))

        # Zero records persisted
        self.assertEqual(WorkReport.objects.filter(employee=self.emp1).count(), initial_work_reports)
        self.assertEqual(ProjectWorkReport.objects.filter(employee=self.emp1).count(), initial_project_reports)
        project.refresh_from_db()
        self.assertEqual(project.status, Project.Status.IN_PROGRESS)

    def test_completion_request_on_already_pending_review_preserves_state(self):
        """Submitting a work report with Completed on a project already in PENDING_REVIEW succeeds and preserves state."""
        requested_time = timezone.now() - timedelta(hours=2)
        project = Project.objects.create(
            title="Already Pending Task",
            employee=self.emp1,
            status=Project.Status.PENDING_REVIEW,
            completion_requested_at=requested_time
        )
        api = APIClient()
        api.force_authenticate(self.emp1)

        res = api.post('/api/v1/mobile/employee/reports/', {
            'date': self.today.isoformat(),
            'notes': 'Additional completion work entry',
            'project_reports': [{
                'project_id': project.pk,
                'project_name': project.title,
                'status': 'Completed',
                'duration': '1h',
            }]
        }, format='json')
        self.assertEqual(res.status_code, 200, res.data)

        project.refresh_from_db()
        self.assertEqual(project.status, Project.Status.PENDING_REVIEW)
        self.assertEqual(project.completion_requested_at, requested_time)
        self.assertTrue(ProjectWorkReport.objects.filter(project=project, employee=self.emp1).exists())


@skipUnlessDBFeature('has_select_for_update')
class ProjectReviewConcurrencyTests(TransactionTestCase):
    def test_concurrent_reviews_are_serialized_with_row_locks(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        from django.db import connections, close_old_connections, transaction

        admin1 = User.objects.create_user('admin-conc-1', role='ADMIN', is_staff=True)
        admin2 = User.objects.create_user('admin-conc-2', role='ADMIN', is_staff=True)
        emp = User.objects.create_user('worker-conc', role='IT')
        project = Project.objects.create(
            title="Concurrent Review Task",
            employee=emp,
            status=Project.Status.PENDING_REVIEW,
            completion_requested_at=timezone.now()
        )
        barrier = Barrier(2)
        results = []

        def review_task(user, action, comments):
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                with transaction.atomic():
                    p = Project.objects.select_for_update().get(pk=project.pk)
                    p.review_completion(user, action, comments)
                results.append(('SUCCESS', user.username, action))
            except Exception as e:
                results.append(('ERROR', user.username, str(e)))
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as executor:
            f1 = executor.submit(review_task, admin1, 'APPROVE', 'Looks good')
            f2 = executor.submit(review_task, admin2, 'NEEDS_CHANGES', 'Fix bug')
            f1.result(timeout=30)
            f2.result(timeout=30)

        project.refresh_from_db()
        successes = [r for r in results if r[0] == 'SUCCESS']
        errors = [r for r in results if r[0] == 'ERROR']
        self.assertEqual(len(successes), 1)
        self.assertEqual(len(errors), 1)
        self.assertIn("Project is not pending review", errors[0][2])
