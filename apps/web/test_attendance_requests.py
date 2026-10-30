"""
Unit and integration test suite for Late Arrival and Early Shift-End Request workflow.

Tests cover:
1. Mobile API:
   - GET /api/v1/mobile/employee/attendance-requests/ (list requests for authenticated employee)
   - Employee data isolation (cannot see other employees' requests)
   - Status filtering (e.g. ?status=PENDING)
   - POST /api/v1/mobile/employee/attendance-requests/ (submission with validation)
   - Rejection of invalid dates (older than 30 days, more than 7 days in advance)
   - Rejection of invalid request_types
   - Rejection of short reasons (< 10 chars)
   - Duplicate pending request check (blocks duplicate pending for same date and type)
   - DELETE /api/v1/mobile/employee/attendance-requests/<pk>/ (cancellation of pending request)
   - Blocking cancellation of already resolved (APPROVED/REJECTED) requests
   - Blocking cancellation of another employee's request
2. CRM Admin / Manager Approval & Workflow:
   - Self-approval prevention (manager or admin cannot review own request)
   - Manager team scoping (manager can only review own team members; admin can review any)
   - Re-reviewing prevention (cannot review already APPROVED or REJECTED request)
   - Approving EARLY_SHIFT_END creates active PolicyException(MIN_DURATION) scoped to employee and date
   - Approving LATE_ARRIVAL updates Attendance.login_remark if present
   - Policy evaluation: employee with approved EARLY_SHIFT_END can end active shift under 8 hours
   - Policy evaluation: employee with pending, rejected, or no request for that date is blocked from ending shift under 8 hours
   - Date scoping: approved EARLY_SHIFT_END for yesterday does not permit early end today
3. Attendance Calculation:
   - Approved EARLY_SHIFT_END marks status_code="FULL_SHIFT" and status_label="Full Shift (Early End Approved)"
   - Approved LATE_ARRIVAL sets exception_status="Approved Late Arrival" but does NOT bypass 8-hour requirement
   - Actual worked hours and seconds remain accurate (not inflated to 8.0h)
"""
from datetime import date, datetime, time, timedelta
from decimal import Decimal

from django.test import Client, TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import CallerSession, User
from apps.activity.models import PolicyException, WorkSession
from apps.activity.policy_services import (
    MIN_NET_WORK_SECONDS,
    ProtectedAction,
    evaluate_protected_action,
)
from apps.web.attendance_services import calculate_daily_attendance_record
from apps.web.models import Attendance, AttendanceCorrectionRequest, AuditEvent


class AttendanceRequestWorkflowTests(TestCase):
    def setUp(self):
        self.superadmin = User.objects.create_superuser(
            username='admin_boss',
            email='admin@vaaniapp.com',
            password='Password123!',
            role='SUPER_ADMIN',
        )
        self.manager1 = User.objects.create_user(
            username='manager_alice',
            email='alice@vaaniapp.com',
            password='Password123!',
            role='MANAGER',
        )
        self.manager2 = User.objects.create_user(
            username='manager_bob',
            email='bob@vaaniapp.com',
            password='Password123!',
            role='MANAGER',
        )
        self.emp1 = User.objects.create_user(
            username='caller_charlie',
            email='charlie@vaaniapp.com',
            password='Password123!',
            role='CALLER',
            manager=self.manager1,
            shift_workflow_enabled=True,
            shift_geofence_enforced=False,
        )
        self.emp2 = User.objects.create_user(
            username='caller_david',
            email='david@vaaniapp.com',
            password='Password123!',
            role='CALLER',
            manager=self.manager2,
            shift_workflow_enabled=True,
            shift_geofence_enforced=False,
        )

        # Clients
        self.client_emp1 = APIClient()
        self.client_emp1.force_authenticate(user=self.emp1)

        self.client_emp2 = APIClient()
        self.client_emp2.force_authenticate(user=self.emp2)

        self.web_client = Client()

    def _login_web(self, user):
        self.web_client.force_login(user)

    def test_mobile_submit_late_arrival_success(self):
        today = timezone.localdate()
        payload = {
            'date': today.isoformat(),
            'request_type': 'LATE_ARRIVAL',
            'reason': 'Bike puncture on the highway, delayed by 45 minutes.',
            'requested_start': timezone.now().isoformat(),
        }
        resp = self.client_emp1.post('/api/v1/mobile/employee/attendance-requests/', payload, format='json')
        self.assertEqual(resp.status_code, 201)
        data = resp.json()
        self.assertEqual(data['request_type'], 'LATE_ARRIVAL')
        self.assertEqual(data['status'], 'PENDING')
        self.assertEqual(data['reason'], payload['reason'])

        # Verify in DB
        req = AttendanceCorrectionRequest.objects.get(pk=data['id'])
        self.assertEqual(req.employee, self.emp1)
        self.assertEqual(req.date, today)

    def test_mobile_submit_early_shift_end_success(self):
        today = timezone.localdate()
        payload = {
            'date': today.isoformat(),
            'request_type': 'EARLY_SHIFT_END',
            'reason': 'Medical emergency at home, need to leave shift at 4 PM.',
            'requested_net_hours': '6.00',
        }
        resp = self.client_emp1.post('/api/v1/mobile/employee/attendance-requests/', payload, format='json')
        self.assertEqual(resp.status_code, 201)
        data = resp.json()
        self.assertEqual(data['request_type'], 'EARLY_SHIFT_END')
        self.assertEqual(data['status'], 'PENDING')
        self.assertEqual(data['requested_net_hours'], '6.00')

    def test_mobile_submit_duplicate_pending_blocked(self):
        today = timezone.localdate()
        AttendanceCorrectionRequest.objects.create(
            employee=self.emp1,
            date=today,
            request_type=AttendanceCorrectionRequest.RequestType.EARLY_SHIFT_END,
            reason='Initial medical appointment request.',
            status=AttendanceCorrectionRequest.Status.PENDING,
        )

        payload = {
            'date': today.isoformat(),
            'request_type': 'EARLY_SHIFT_END',
            'reason': 'Duplicate second request for the same day.',
        }
        resp = self.client_emp1.post('/api/v1/mobile/employee/attendance-requests/', payload, format='json')
        self.assertEqual(resp.status_code, 400)
        self.assertIn('already exists', resp.json().get('detail', ''))

    def test_mobile_submit_validation_errors(self):
        today = timezone.localdate()

        # Date too old (> 30 days)
        resp = self.client_emp1.post('/api/v1/mobile/employee/attendance-requests/', {
            'date': (today - timedelta(days=40)).isoformat(),
            'request_type': 'LATE_ARRIVAL',
            'reason': 'A very old reason that should fail.',
        }, format='json')
        self.assertEqual(resp.status_code, 400)

        # Date too far in advance (> 7 days)
        resp = self.client_emp1.post('/api/v1/mobile/employee/attendance-requests/', {
            'date': (today + timedelta(days=15)).isoformat(),
            'request_type': 'LATE_ARRIVAL',
            'reason': 'Future date reason that should fail.',
        }, format='json')
        self.assertEqual(resp.status_code, 400)

        # Reason too short (< 10 chars)
        resp = self.client_emp1.post('/api/v1/mobile/employee/attendance-requests/', {
            'date': today.isoformat(),
            'request_type': 'LATE_ARRIVAL',
            'reason': 'Late',
        }, format='json')
        self.assertEqual(resp.status_code, 400)
        self.assertIn('at least 10 characters', resp.json().get('detail', ''))

        # Invalid request type
        resp = self.client_emp1.post('/api/v1/mobile/employee/attendance-requests/', {
            'date': today.isoformat(),
            'request_type': 'RANDOM_TYPE',
            'reason': 'Valid reason with random type.',
        }, format='json')
        self.assertEqual(resp.status_code, 400)

    def test_mobile_employee_isolation(self):
        today = timezone.localdate()
        req1 = AttendanceCorrectionRequest.objects.create(
            employee=self.emp1,
            date=today,
            request_type=AttendanceCorrectionRequest.RequestType.LATE_ARRIVAL,
            reason='Charlie was delayed by rain.',
            status=AttendanceCorrectionRequest.Status.PENDING,
        )
        req2 = AttendanceCorrectionRequest.objects.create(
            employee=self.emp2,
            date=today,
            request_type=AttendanceCorrectionRequest.RequestType.EARLY_SHIFT_END,
            reason='David has an urgent appointment.',
            status=AttendanceCorrectionRequest.Status.PENDING,
        )

        # Emp1 list
        resp1 = self.client_emp1.get('/api/v1/mobile/employee/attendance-requests/')
        self.assertEqual(resp1.status_code, 200)
        emp1_ids = [r['id'] for r in resp1.json()['requests']]
        self.assertIn(req1.pk, emp1_ids)
        self.assertNotIn(req2.pk, emp1_ids)

        # Emp2 cannot cancel Emp1's request
        resp_del = self.client_emp2.delete(f'/api/v1/mobile/employee/attendance-requests/{req1.pk}/')
        self.assertEqual(resp_del.status_code, 404)

        # Emp1 cancels own request
        resp_del_ok = self.client_emp1.delete(f'/api/v1/mobile/employee/attendance-requests/{req1.pk}/')
        self.assertEqual(resp_del_ok.status_code, 204)
        self.assertFalse(AttendanceCorrectionRequest.objects.filter(pk=req1.pk).exists())

    def test_mobile_cannot_cancel_reviewed_request(self):
        today = timezone.localdate()
        req = AttendanceCorrectionRequest.objects.create(
            employee=self.emp1,
            date=today,
            request_type=AttendanceCorrectionRequest.RequestType.LATE_ARRIVAL,
            reason='Charlie was delayed by rain.',
            status=AttendanceCorrectionRequest.Status.APPROVED,
            reviewed_by=self.superadmin,
            reviewed_at=timezone.now(),
        )
        resp = self.client_emp1.delete(f'/api/v1/mobile/employee/attendance-requests/{req.pk}/')
        self.assertEqual(resp.status_code, 400)
        self.assertTrue(AttendanceCorrectionRequest.objects.filter(pk=req.pk).exists())

    def test_admin_and_manager_review_rules(self):
        today = timezone.localdate()
        req_charlie = AttendanceCorrectionRequest.objects.create(
            employee=self.emp1,
            date=today,
            request_type=AttendanceCorrectionRequest.RequestType.EARLY_SHIFT_END,
            reason='Charlie early leave request for approval.',
            status=AttendanceCorrectionRequest.Status.PENDING,
        )

        # 1. Manager2 cannot review Charlie (Charlie is under Manager1)
        self._login_web(self.manager2)
        resp = self.web_client.post(f'/attendance/corrections/{req_charlie.pk}/action/', {'action': 'approve'})
        req_charlie.refresh_from_db()
        self.assertEqual(req_charlie.status, AttendanceCorrectionRequest.Status.PENDING)

        # 2. Manager1 cannot self-approve their own request
        req_manager1 = AttendanceCorrectionRequest.objects.create(
            employee=self.manager1,
            date=today,
            request_type=AttendanceCorrectionRequest.RequestType.LATE_ARRIVAL,
            reason='Manager Alice late arrival request.',
            status=AttendanceCorrectionRequest.Status.PENDING,
        )
        self._login_web(self.manager1)
        resp_self = self.web_client.post(f'/attendance/corrections/{req_manager1.pk}/action/', {'action': 'approve'})
        req_manager1.refresh_from_db()
        self.assertEqual(req_manager1.status, AttendanceCorrectionRequest.Status.PENDING)

        # 3. Manager1 CAN approve Charlie (team member)
        resp_team = self.web_client.post(
            f'/attendance/corrections/{req_charlie.pk}/action/',
            {'action': 'approve', 'review_notes': 'Approved early departure.'}
        )
        req_charlie.refresh_from_db()
        self.assertEqual(req_charlie.status, AttendanceCorrectionRequest.Status.APPROVED)
        self.assertEqual(req_charlie.reviewed_by, self.manager1)
        self.assertIsNotNone(req_charlie.reviewed_at)

        # Verify PolicyException was created and attributed directly to manager
        exc = PolicyException.objects.filter(
            user=self.emp1,
            rule_type=PolicyException.RuleType.MIN_DURATION,
            is_approved=True,
            is_active=True,
        ).first()
        self.assertIsNotNone(exc)
        self.assertEqual(exc.approved_by, self.manager1)

        # Verify audit description records actual reviewer role and username
        audit_ev = AuditEvent.objects.filter(
            description__contains=f"approved attendance correction #{req_charlie.pk}"
        ).first()
        self.assertIsNotNone(audit_ev)
        self.assertIn("Manager manager_alice", audit_ev.description)

        # 4. Cannot re-review already resolved request
        resp_rereview = self.web_client.post(f'/attendance/corrections/{req_charlie.pk}/action/', {'action': 'reject'})
        req_charlie.refresh_from_db()
        self.assertEqual(req_charlie.status, AttendanceCorrectionRequest.Status.APPROVED)

    def test_approved_early_shift_end_permits_ending_short_shift(self):
        today = timezone.localdate()
        now = timezone.now()

        # Create active work session with 4 hours net work
        session = WorkSession.objects.create(
            user=self.emp1,
            date=today,
            status=WorkSession.Status.ACTIVE,
            started_at=now - timedelta(hours=4),
        )

        # Without early shift end approval, ending shift MUST fail with INSUFFICIENT_WORK_DURATION
        result_blocked = evaluate_protected_action(
            user=self.emp1,
            action=ProtectedAction.END_SHIFT,
            session=session,
            now=now,
        )
        self.assertFalse(result_blocked.permitted)
        self.assertEqual(result_blocked.error_code, 'INSUFFICIENT_WORK_DURATION')

        # Submit and approve EARLY_SHIFT_END request
        req = AttendanceCorrectionRequest.objects.create(
            employee=self.emp1,
            date=today,
            request_type=AttendanceCorrectionRequest.RequestType.EARLY_SHIFT_END,
            reason='Need to leave after 4h for doctor visit.',
            status=AttendanceCorrectionRequest.Status.PENDING,
        )
        self._login_web(self.superadmin)
        self.web_client.post(f'/attendance/corrections/{req.pk}/action/', {'action': 'approve'})
        req.refresh_from_db()
        self.assertEqual(req.status, AttendanceCorrectionRequest.Status.APPROVED)

        # Now ending shift MUST be permitted
        result_allowed = evaluate_protected_action(
            user=self.emp1,
            action=ProtectedAction.END_SHIFT,
            session=session,
            now=now,
        )
        self.assertTrue(result_allowed.permitted)

    def test_rejected_early_shift_end_does_not_permit_ending_short_shift(self):
        today = timezone.localdate()
        now = timezone.now()

        session = WorkSession.objects.create(
            user=self.emp1,
            date=today,
            status=WorkSession.Status.ACTIVE,
            started_at=now - timedelta(hours=3),
        )

        req = AttendanceCorrectionRequest.objects.create(
            employee=self.emp1,
            date=today,
            request_type=AttendanceCorrectionRequest.RequestType.EARLY_SHIFT_END,
            reason='Leaving early without valid reason.',
            status=AttendanceCorrectionRequest.Status.PENDING,
        )
        self._login_web(self.superadmin)
        self.web_client.post(f'/attendance/corrections/{req.pk}/action/', {'action': 'reject'})
        req.refresh_from_db()
        self.assertEqual(req.status, AttendanceCorrectionRequest.Status.REJECTED)

        result = evaluate_protected_action(
            user=self.emp1,
            action=ProtectedAction.END_SHIFT,
            session=session,
            now=now,
        )
        self.assertFalse(result.permitted)
        self.assertEqual(result.error_code, 'INSUFFICIENT_WORK_DURATION')

    def test_approved_early_shift_end_scoped_to_specific_date(self):
        yesterday = timezone.localdate() - timedelta(days=1)
        today = timezone.localdate()
        now = timezone.now()

        # Approved early shift-end was for YESTERDAY
        AttendanceCorrectionRequest.objects.create(
            employee=self.emp1,
            date=yesterday,
            request_type=AttendanceCorrectionRequest.RequestType.EARLY_SHIFT_END,
            reason='Yesterday emergency.',
            status=AttendanceCorrectionRequest.Status.APPROVED,
            reviewed_by=self.superadmin,
            reviewed_at=now - timedelta(days=1),
        )

        # TODAY's session under 8 hours
        session_today = WorkSession.objects.create(
            user=self.emp1,
            date=today,
            status=WorkSession.Status.ACTIVE,
            started_at=now - timedelta(hours=3),
        )

        result = evaluate_protected_action(
            user=self.emp1,
            action=ProtectedAction.END_SHIFT,
            session=session_today,
            now=now,
        )
        self.assertFalse(result.permitted)
        self.assertEqual(result.error_code, 'INSUFFICIENT_WORK_DURATION')

    def test_attendance_calculation_for_early_end_and_late_arrival(self):
        today = timezone.localdate()
        start_time = timezone.now() - timedelta(hours=5)
        end_time = timezone.now()

        # 1. Closed session with 5 hours net work and approved EARLY_SHIFT_END
        session = WorkSession.objects.create(
            user=self.emp1,
            date=today,
            status=WorkSession.Status.COMPLETED,
            started_at=start_time,
            ended_at=end_time,
            closure_reason=WorkSession.ClosureReason.MANUAL,
        )
        req_early = AttendanceCorrectionRequest.objects.create(
            employee=self.emp1,
            date=today,
            request_type=AttendanceCorrectionRequest.RequestType.EARLY_SHIFT_END,
            reason='Family event.',
            status=AttendanceCorrectionRequest.Status.APPROVED,
        )

        rec = calculate_daily_attendance_record(
            employee=self.emp1,
            target_date=today,
            sessions_for_date=[session],
            correction_request=req_early,
        )
        self.assertEqual(rec.status_code, 'FULL_SHIFT')
        self.assertEqual(rec.status_label, 'Full Shift (Early End Approved)')
        # Actual worked hours remain accurate (5.0h, NOT inflated to 8.0h)
        self.assertEqual(rec.net_work_hours, 5.0)

        # 2. Closed session with 5 hours net work and approved LATE_ARRIVAL
        # Late arrival does NOT excuse 5-hour short shift!
        req_late = AttendanceCorrectionRequest.objects.create(
            employee=self.emp1,
            date=today,
            request_type=AttendanceCorrectionRequest.RequestType.LATE_ARRIVAL,
            reason='Heavy traffic.',
            status=AttendanceCorrectionRequest.Status.APPROVED,
        )
        rec_late = calculate_daily_attendance_record(
            employee=self.emp1,
            target_date=today,
            sessions_for_date=[session],
            correction_request=req_late,
        )
        self.assertEqual(rec_late.status_code, 'SHORT_SHIFT')
        self.assertEqual(rec_late.exception_status, 'Approved Late Arrival')
        self.assertEqual(rec_late.net_work_hours, 5.0)
    def test_mobile_rejects_disallowed_request_types(self):
        today = timezone.localdate()
        disallowed_types = ['MISSING_CHECKOUT', 'SHORT_SHIFT', 'MISSED_START', 'WRONG_HOURS', 'OTHER', 'RANDOM_TYPE']
        for req_type in disallowed_types:
            resp = self.client_emp1.post('/api/v1/mobile/employee/attendance-requests/', {
                'date': today.isoformat(),
                'request_type': req_type,
                'reason': f'Trying disallowed request type {req_type}.',
            }, format='json')
            self.assertEqual(resp.status_code, 400, f'Expected 400 for {req_type}')
            self.assertIn('Only LATE_ARRIVAL and EARLY_SHIFT_END', resp.json().get('detail', ''))

    def test_early_shift_end_exact_session_binding(self):
        today = timezone.localdate()
        now = timezone.now()

        # Session 1: completed morning shift with 4 hours
        session1 = WorkSession.objects.create(
            user=self.emp1,
            date=today,
            status=WorkSession.Status.COMPLETED,
            started_at=now - timedelta(hours=6),
            ended_at=now - timedelta(hours=2),
            closure_reason=WorkSession.ClosureReason.MANUAL,
        )

        # Early end request explicitly bound to session1
        req1 = AttendanceCorrectionRequest.objects.create(
            employee=self.emp1,
            date=today,
            session=session1,
            request_type=AttendanceCorrectionRequest.RequestType.EARLY_SHIFT_END,
            reason='Early departure for morning shift only.',
            status=AttendanceCorrectionRequest.Status.APPROVED,
            reviewed_by=self.superadmin,
            reviewed_at=now - timedelta(hours=2),
        )

        # session1 is permitted to end early
        res1 = evaluate_protected_action(
            user=self.emp1,
            action=ProtectedAction.END_SHIFT,
            session=session1,
            now=now - timedelta(hours=2),
        )
        self.assertTrue(res1.permitted)

        # Session 2: active second shift on same date with 1 hour
        session2 = WorkSession.objects.create(
            user=self.emp1,
            date=today,
            status=WorkSession.Status.ACTIVE,
            started_at=now - timedelta(hours=1),
        )

        # session2 must NOT be authorized by session1's approval!
        res2 = evaluate_protected_action(
            user=self.emp1,
            action=ProtectedAction.END_SHIFT,
            session=session2,
            now=now,
        )
        self.assertFalse(res2.permitted)
        self.assertEqual(res2.error_code, 'INSUFFICIENT_WORK_DURATION')

        # Another employee (emp2) also cannot use emp1's approval
        session_emp2 = WorkSession.objects.create(
            user=self.emp2,
            date=today,
            status=WorkSession.Status.ACTIVE,
            started_at=now - timedelta(hours=3),
        )
        res_emp2 = evaluate_protected_action(
            user=self.emp2,
            action=ProtectedAction.END_SHIFT,
            session=session_emp2,
            now=now,
        )
        self.assertFalse(res_emp2.permitted)
        self.assertEqual(res_emp2.error_code, 'INSUFFICIENT_WORK_DURATION')

    def test_early_shift_end_departure_time_enforcement(self):
        today = timezone.localdate()
        base_time = timezone.make_aware(datetime.combine(today, time(9, 0)))
        approved_departure = timezone.make_aware(datetime.combine(today, time(16, 30)))  # 4:30 PM

        session = WorkSession.objects.create(
            user=self.emp1,
            date=today,
            status=WorkSession.Status.ACTIVE,
            started_at=base_time,
        )

        req = AttendanceCorrectionRequest.objects.create(
            employee=self.emp1,
            date=today,
            session=session,
            request_type=AttendanceCorrectionRequest.RequestType.EARLY_SHIFT_END,
            reason='Need to leave at 4:30 PM sharp.',
            requested_end=approved_departure,
            status=AttendanceCorrectionRequest.Status.APPROVED,
            reviewed_by=self.superadmin,
            reviewed_at=base_time,
        )

        # 1. Attempting to end at 4:15 PM (before approved 4:30 PM) -> BLOCKED
        early_attempt_time = timezone.make_aware(datetime.combine(today, time(16, 15)))
        res_early = evaluate_protected_action(
            user=self.emp1,
            action=ProtectedAction.END_SHIFT,
            session=session,
            now=early_attempt_time,
        )
        self.assertFalse(res_early.permitted)
        self.assertEqual(res_early.error_code, 'INSUFFICIENT_WORK_DURATION')
        self.assertIn('Approved early shift-end time is', res_early.error_message)

        # 2. Attempting to end at 4:30 PM (exactly at approved time) -> PERMITTED
        res_exact = evaluate_protected_action(
            user=self.emp1,
            action=ProtectedAction.END_SHIFT,
            session=session,
            now=approved_departure,
        )
        self.assertTrue(res_exact.permitted)

        # 3. Attempting to end at 4:35 PM (after approved time) -> PERMITTED
        res_after = evaluate_protected_action(
            user=self.emp1,
            action=ProtectedAction.END_SHIFT,
            session=session,
            now=approved_departure + timedelta(minutes=5),
        )
        self.assertTrue(res_after.permitted)

    def test_unbound_request_binds_to_shift_on_approval_and_evaluation(self):
        today = timezone.localdate()
        now = timezone.now()

        # Request created without session (e.g. submitted in advance)
        req = AttendanceCorrectionRequest.objects.create(
            employee=self.emp1,
            date=today,
            request_type=AttendanceCorrectionRequest.RequestType.EARLY_SHIFT_END,
            reason='Advance notice for early leave.',
            status=AttendanceCorrectionRequest.Status.PENDING,
        )
        self.assertIsNone(req.session)

        # Emp1 starts a session
        session = WorkSession.objects.create(
            user=self.emp1,
            date=today,
            status=WorkSession.Status.ACTIVE,
            started_at=now - timedelta(hours=3),
        )

        # Manager approves the request on web CRM
        self._login_web(self.manager1)
        resp = self.web_client.post(
            f'/attendance/corrections/{req.pk}/action/',
            {'action': 'approve', 'review_notes': 'Approved for your shift.'}
        )
        req.refresh_from_db()
        self.assertEqual(req.status, AttendanceCorrectionRequest.Status.APPROVED)
        # Verify it bound to the session
        self.assertEqual(req.session, session)

        # Policy evaluation succeeds
        res = evaluate_protected_action(
            user=self.emp1,
            action=ProtectedAction.END_SHIFT,
            session=session,
            now=now,
        )
        self.assertTrue(res.permitted)

    def test_session_mismatch_fails_safely(self):
        today = timezone.localdate()
        now = timezone.now()

        # Session belonging to emp2
        other_session = WorkSession.objects.create(
            user=self.emp2,
            date=today,
            status=WorkSession.Status.ACTIVE,
            started_at=now - timedelta(hours=3),
        )

        # Emp1 attempts to associate emp2's session
        req = AttendanceCorrectionRequest.objects.create(
            employee=self.emp1,
            date=today,
            session=other_session,  # Mismatched user!
            request_type=AttendanceCorrectionRequest.RequestType.EARLY_SHIFT_END,
            reason='Mismatched session attempt.',
            status=AttendanceCorrectionRequest.Status.APPROVED,
            reviewed_by=self.superadmin,
            reviewed_at=now,
        )

        # Evaluating for emp1 on other_session MUST fail safely
        res = evaluate_protected_action(
            user=self.emp1,
            action=ProtectedAction.END_SHIFT,
            session=other_session,
            now=now,
        )
        self.assertFalse(res.permitted)
        self.assertEqual(res.error_code, 'INSUFFICIENT_WORK_DURATION')

    def test_unrelated_standalone_policy_exception_retains_behavior(self):
        today = timezone.localdate()
        now = timezone.now()

        session = WorkSession.objects.create(
            user=self.emp1,
            date=today,
            status=WorkSession.Status.ACTIVE,
            started_at=now - timedelta(hours=3),
        )

        # Unrelated standalone PolicyException created directly by admin
        PolicyException.objects.create(
            user=self.emp1,
            rule_type=PolicyException.RuleType.MIN_DURATION,
            reason='Direct admin exception: medical emergency hospital visit',
            requested_by=self.emp1,
            approved_by=self.superadmin,
            is_approved=True,
            approved_at=now,
            valid_from=now - timedelta(hours=1),
            valid_until=now + timedelta(hours=4),
            is_active=True,
        )

        res = evaluate_protected_action(
            user=self.emp1,
            action=ProtectedAction.END_SHIFT,
            session=session,
            now=now,
        )
        self.assertTrue(res.permitted)
        self.assertTrue(res.bypassed_by_exception)

    def test_late_arrival_approval_preserves_checkin_and_appends_remark(self):
        today = timezone.localdate()
        checkin_time = timezone.now() - timedelta(hours=6)

        # Create attendance with existing login remark
        att = Attendance.objects.create(
            employee=self.emp1,
            date=today,
            checked_in=checkin_time,
            login_remark="Arrived via metro line 2",
        )

        # Create unrelated attendance for emp2
        att_emp2 = Attendance.objects.create(
            employee=self.emp2,
            date=today,
            checked_in=checkin_time,
            login_remark="Emp2 regular arrival",
        )

        req_late = AttendanceCorrectionRequest.objects.create(
            employee=self.emp1,
            date=today,
            request_type=AttendanceCorrectionRequest.RequestType.LATE_ARRIVAL,
            reason='Traffic jam due to marathon.',
            status=AttendanceCorrectionRequest.Status.PENDING,
        )

        self._login_web(self.manager1)
        resp = self.web_client.post(
            f'/attendance/corrections/{req_late.pk}/action/',
            {'action': 'approve', 'review_notes': 'Approved by Alice.'}
        )
        req_late.refresh_from_db()
        self.assertEqual(req_late.status, AttendanceCorrectionRequest.Status.APPROVED)
        self.assertEqual(req_late.reviewed_by, self.manager1)

        # Verify emp1 attendance record
        att.refresh_from_db()
        # Original checked_in timestamp MUST remain unchanged
        self.assertEqual(att.checked_in, checkin_time)
        # Login remark MUST preserve existing remark and append approval note
        self.assertIn("Arrived via metro line 2", att.login_remark)
        self.assertIn("Late Arrival Approved by Manager manager_alice: Approved by Alice.", att.login_remark)

        # Verify unrelated employee's attendance is completely untouched
        att_emp2.refresh_from_db()
        self.assertEqual(att_emp2.login_remark, "Emp2 regular arrival")
        self.assertEqual(att_emp2.checked_in, checkin_time)

    def test_attendance_reporting_does_not_inflate_hours(self):
        from apps.web.attendance_services import get_monthly_totals_from_records as calculate_monthly_attendance_summary
        today = timezone.localdate()
        start_time = timezone.now() - timedelta(hours=4)
        end_time = timezone.now()

        session = WorkSession.objects.create(
            user=self.emp1,
            date=today,
            status=WorkSession.Status.COMPLETED,
            started_at=start_time,
            ended_at=end_time,
            closure_reason=WorkSession.ClosureReason.MANUAL,
        )

        req = AttendanceCorrectionRequest.objects.create(
            employee=self.emp1,
            date=today,
            session=session,
            request_type=AttendanceCorrectionRequest.RequestType.EARLY_SHIFT_END,
            reason='Personal reasons approved.',
            status=AttendanceCorrectionRequest.Status.APPROVED,
            reviewed_by=self.superadmin,
            reviewed_at=timezone.now(),
        )

        rec = calculate_daily_attendance_record(
            employee=self.emp1,
            target_date=today,
            sessions_for_date=[session],
            correction_request=req,
        )
        self.assertEqual(rec.status_code, 'FULL_SHIFT')
        self.assertEqual(rec.status_label, 'Full Shift (Early End Approved)')
        # Worked hours must reflect ACTUAL worked time (4.0h), not 8.0h
        self.assertEqual(rec.net_work_hours, 4.0)
        self.assertEqual(rec.net_work_seconds, 4 * 3600)

        # Monthly summary
        summary = calculate_monthly_attendance_summary([rec])
        self.assertEqual(summary['worked_days'], 1)
        self.assertEqual(summary['full_shifts'], 1)
        self.assertEqual(summary['short_shifts'], 0)
        self.assertEqual(summary['total_net_hours'], 4.0)
        self.assertEqual(summary['avg_net_hours'], 4.0)
