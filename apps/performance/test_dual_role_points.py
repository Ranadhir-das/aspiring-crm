import uuid
from datetime import timedelta
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.calls.models import Call
from apps.followups.models import FollowUp
from apps.leads.models import Lead
from apps.performance.models import LeadMilestone, PointsAdjustment, PointsEntry
from apps.performance.reporting import metrics, report_window
from apps.performance.services import (
    adjust_points,
    reconcile_daily_calls,
    score_call,
    score_followup,
)


class DualRolePointsTests(TestCase):
    def setUp(self):
        self.api = APIClient()

        # Admin
        self.admin = User.objects.create_user(
            'admin_user', password='password123', role='ADMIN', is_staff=True
        )

        # Pure caller
        self.caller = User.objects.create_user(
            'pure_caller', password='password123', role='CALLER',
            can_act_as_caller=False, can_act_as_counselor=False
        )

        # Pure counselor (no caller capability)
        self.pure_counselor = User.objects.create_user(
            'pure_counselor', password='password123', role='COUNSELOR',
            can_act_as_caller=False, can_act_as_counselor=False
        )

        # Dual-role employee: primary COUNSELOR + can_act_as_caller
        self.dual_employee = User.objects.create_user(
            'dual_emp', password='password123', role='COUNSELOR',
            can_act_as_caller=True, can_act_as_counselor=False
        )

        # Other caller for isolation checks
        self.other_caller = User.objects.create_user(
            'other_caller', password='password123', role='CALLER'
        )

        # Test lead
        self.lead = Lead.objects.create(
            name='Test Lead', phone='9876543210', assigned_caller=self.dual_employee,
            service='MBBS', preferred_course='MBBS'
        )

    # 1. Caller-only employee can view their own points
    def test_caller_only_can_view_own_points(self):
        self.api.force_authenticate(self.caller)
        response = self.api.get('/api/v1/points/me/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['caller'], self.caller.pk)
        self.assertIn('summary', response.data)
        self.assertIn('results', response.data)

    # 2. Dual-role employee (role=COUNSELOR, can_act_as_caller=True) can view their own points
    def test_dual_role_employee_can_view_own_points(self):
        self.api.force_authenticate(self.dual_employee)
        response = self.api.get('/api/v1/points/me/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['caller'], self.dual_employee.pk)
        self.assertIn('summary', response.data)
        self.assertIn('results', response.data)

    # 3. Pure counselor without caller capability cannot access caller points
    def test_pure_counselor_cannot_access_caller_points(self):
        self.api.force_authenticate(self.pure_counselor)
        response = self.api.get('/api/v1/points/me/')
        self.assertEqual(response.status_code, 403)
        self.assertIn('detail', response.data)
        self.assertEqual(response.data['detail'], 'You do not have permission to perform this action.')

    # 4. An employee cannot access another employee's private points
    def test_employee_cannot_access_another_employees_points(self):
        # Caller trying to view other caller's points
        self.api.force_authenticate(self.caller)
        res1 = self.api.get(f'/api/v1/points/callers/{self.other_caller.pk}/')
        self.assertEqual(res1.status_code, 403)

        # Dual-role employee trying to view other caller's points
        self.api.force_authenticate(self.dual_employee)
        res2 = self.api.get(f'/api/v1/points/callers/{self.other_caller.pk}/')
        self.assertEqual(res2.status_code, 403)

        # Dual-role employee trying to view regular caller's points
        res3 = self.api.get(f'/api/v1/points/callers/{self.caller.pk}/')
        self.assertEqual(res3.status_code, 403)

    # 5. Admin permissions continue to work
    def test_admin_permissions_continue_to_work(self):
        self.api.force_authenticate(self.admin)
        # Admin can view regular caller
        res1 = self.api.get(f'/api/v1/points/callers/{self.caller.pk}/')
        self.assertEqual(res1.status_code, 200)

        # Admin can view dual-role employee
        res2 = self.api.get(f'/api/v1/points/callers/{self.dual_employee.pk}/')
        self.assertEqual(res2.status_code, 200)
        self.assertEqual(res2.data['caller'], self.dual_employee.pk)

        # Admin can adjust points for dual-role employee
        adj_payload = {
            'request_id': str(uuid.uuid4()),
            'caller': self.dual_employee.pk,
            'points': 10,
            'reason': 'Bonus performance award',
            'reason_type': 'OTHER',
        }
        res3 = self.api.post('/api/v1/points/adjustments/', adj_payload)
        self.assertEqual(res3.status_code, 201)

        # Admin can record milestone for dual-role employee
        milestone_payload = {
            'caller': self.dual_employee.pk,
            'lead': self.lead.pk,
            'event': 'VERIFIED_ADMISSION',
            'reason': 'Milestone achieved',
        }
        res4 = self.api.post('/api/v1/points/milestones/', milestone_payload)
        self.assertEqual(res4.status_code, 201)

        # Admin query on pure counselor returns 404 (not a caller)
        res5 = self.api.get(f'/api/v1/points/callers/{self.pure_counselor.pk}/')
        self.assertEqual(res5.status_code, 404)

    # 6. Existing point-award and idempotency rules remain intact for dual-role caller
    def test_point_award_and_idempotency_for_dual_role(self):
        # Create a call with outcome INTERESTED
        call = Call.objects.create(
            caller=self.dual_employee,
            lead=self.lead,
            started_at=timezone.now(),
            duration_seconds=120,
            outcome='INTERESTED',
            selected_course='MBBS',
            expected_admission_year=2027,
            course_classification='OWN',
        )

        score_call(call.pk)

        entries = PointsEntry.objects.filter(caller=self.dual_employee, lead=self.lead)
        self.assertEqual(entries.count(), 1)
        self.assertEqual(entries.first().event, PointsEntry.Event.INTERESTED_LEAD)
        self.assertEqual(entries.first().points, 15)

        # Idempotency: scoring the same call again must NOT duplicate the award
        score_call(call.pk)
        self.assertEqual(PointsEntry.objects.filter(caller=self.dual_employee, lead=self.lead).count(), 1)

        # Follow-up on-time scoring
        followup = FollowUp.objects.create(
            lead=self.lead,
            caller=self.dual_employee,
            scheduled_at=timezone.now() + timedelta(hours=1),
            status='COMPLETED',
            completed_at=timezone.now(),
        )
        score_followup(followup.pk)
        fu_entry = PointsEntry.objects.filter(caller=self.dual_employee, followup=followup).first()
        self.assertIsNotNone(fu_entry)
        self.assertEqual(fu_entry.event, PointsEntry.Event.FOLLOWUP_COMPLETED)
        self.assertEqual(fu_entry.points, 2)

        # Idempotency check on followup
        score_followup(followup.pk)
        self.assertEqual(PointsEntry.objects.filter(caller=self.dual_employee, followup=followup).count(), 1)

    # 7. Existing performance totals include the dual-role employee's legitimate caller points
    def test_performance_totals_include_dual_role_caller_points(self):
        PointsEntry.objects.create(
            caller=self.dual_employee,
            event='CALL_DAILY_BONUS',
            points=25,
            reason='50 dials achieved',
            event_key=f'daily_calls:{self.dual_employee.pk}:2026-10-23',
            occurred_at=timezone.now(),
        )

        _, window, _ = report_window({})
        perf_metrics = metrics(self.dual_employee, window)
        self.assertEqual(perf_metrics['total_points'], 25)
        self.assertEqual(perf_metrics['lifetime_points'], 25)

        # Progress view includes dual-role employee
        self.api.force_authenticate(self.dual_employee)
        res = self.api.get('/api/v1/points/progress/?timeframe=week')
        self.assertEqual(res.status_code, 200)
        usernames = [c['username'] for c in res.data['callers']]
        self.assertIn(self.dual_employee.username, usernames)
        dual_caller_row = next(c for c in res.data['callers'] if c['username'] == self.dual_employee.username)
        self.assertEqual(dual_caller_row['points'], 25)
        self.assertTrue(dual_caller_row['is_me'])

    # 9. Capability revocation is enforced on subsequent authorized requests
    def test_capability_revocation_enforced(self):
        # Initially authorized
        self.api.force_authenticate(self.dual_employee)
        res1 = self.api.get('/api/v1/points/me/')
        self.assertEqual(res1.status_code, 200)

        # Revoke caller capability
        self.dual_employee.can_act_as_caller = False
        self.dual_employee.save(update_fields=['can_act_as_caller'])

        # Refresh from DB
        self.dual_employee.refresh_from_db()
        self.api.force_authenticate(self.dual_employee)

        # Subsequent request must be rejected
        res2 = self.api.get('/api/v1/points/me/')
        self.assertEqual(res2.status_code, 403)
        self.assertEqual(res2.data['detail'], 'You do not have permission to perform this action.')
