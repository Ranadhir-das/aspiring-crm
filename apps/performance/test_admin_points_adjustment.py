import concurrent.futures
import uuid
from django.db import connection, transaction
from django.db.models import Sum
from django.test import TestCase, TransactionTestCase
from django.urls import reverse
from django.utils import timezone
from apps.accounts.models import User
from apps.performance.forms import AdjustmentForm
from apps.performance.models import PointsAdjustment, PointsEntry
from apps.performance.services import adjust_points
from apps.web.models import AuditEvent


class AdminPointsAdjustmentTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user('admin_user', password='password123', role=User.Role.ADMIN, is_staff=True)
        self.superadmin = User.objects.create_user('superadmin_user', password='password123', role=User.Role.SUPER_ADMIN, is_superuser=True)
        self.manager = User.objects.create_user('manager_user', password='password123', role=User.Role.MANAGER)
        self.caller = User.objects.create_user('caller_user', password='password123', role=User.Role.CALLER)
        self.employee = User.objects.create_user('employee_user', password='password123', role=User.Role.EMPLOYEE)
        self.dual_employee = User.objects.create_user('dual_user', password='password123', role=User.Role.COUNSELOR, can_act_as_caller=True)

    # 1. PERMISSIONS
    def test_admin_can_view_adjustment_panel(self):
        self.client.force_login(self.admin)
        url = reverse('web:caller-detail', args=[self.caller.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['can_adjust'])
        self.assertContains(response, 'Admin Points Adjustment')
        self.assertContains(response, 'Submit Adjustment')

    def test_superuser_can_view_adjustment_panel(self):
        self.client.force_login(self.superadmin)
        url = reverse('web:caller-detail', args=[self.caller.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['can_adjust'])
        self.assertContains(response, 'Admin Points Adjustment')

    def test_manager_cannot_adjust_points(self):
        self.client.force_login(self.manager)
        url = reverse('web:caller-detail', args=[self.caller.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context['can_adjust'])
        self.assertNotContains(response, 'name="action" value="adjust"')

        # POST attempt must return 403 Forbidden
        post_res = self.client.post(url, {
            'action': 'adjust',
            'points': 50,
            'reason': 'Manager trying to adjust points',
            'reason_type': 'OTHER',
            'request_id': str(uuid.uuid4()),
        })
        self.assertEqual(post_res.status_code, 403)
        self.assertEqual(PointsEntry.objects.filter(caller=self.caller).count(), 0)

    def test_caller_cannot_adjust_points(self):
        self.client.force_login(self.caller)
        url = reverse('web:caller-detail', args=[self.caller.pk])
        post_res = self.client.post(url, {
            'action': 'adjust',
            'points': 100,
            'reason': 'Self award attempt',
            'reason_type': 'OTHER',
            'request_id': str(uuid.uuid4()),
        })
        self.assertEqual(post_res.status_code, 403)
        self.assertEqual(PointsEntry.objects.filter(caller=self.caller).count(), 0)

    def test_unauthenticated_cannot_adjust_points(self):
        self.client.logout()
        url = reverse('web:caller-detail', args=[self.caller.pk])
        post_res = self.client.post(url, {
            'action': 'adjust',
            'points': 50,
            'reason': 'Anonymous attempt',
            'reason_type': 'OTHER',
            'request_id': str(uuid.uuid4()),
        })
        self.assertIn(post_res.status_code, (302, 403))

    # 2. POSITIVE ADJUSTMENT
    def test_positive_points_adjustment_updates_balance_and_ledger(self):
        self.client.force_login(self.admin)
        url = reverse('web:caller-detail', args=[self.caller.pk])
        req_id = str(uuid.uuid4())
        response = self.client.post(url, {
            'action': 'adjust',
            'points': 75,
            'reason': 'Exemplary conversion during expo week',
            'reason_type': 'OTHER',
            'request_id': req_id,
        })
        self.assertEqual(response.status_code, 302)

        # Follow redirect
        follow_res = self.client.get(response.url)
        self.assertEqual(follow_res.status_code, 200)
        self.assertEqual(follow_res.context['stats']['lifetime_points'], 75)
        self.assertContains(follow_res, '+75')
        self.assertContains(follow_res, 'Exemplary conversion during expo week')
        self.assertContains(follow_res, self.admin.username)

        # Ledger checks
        entry = PointsEntry.objects.get(caller=self.caller)
        self.assertEqual(entry.points, 75)
        self.assertEqual(entry.event, PointsEntry.Event.ADMIN_ADJUSTMENT)
        self.assertEqual(entry.recorded_by, self.admin)
        self.assertEqual(entry.reason, 'Exemplary conversion during expo week')

        # Audit check
        adj = PointsAdjustment.objects.get(caller=self.caller)
        self.assertEqual(adj.points, 75)
        self.assertEqual(adj.recorded_by, self.admin)

    # 3. NEGATIVE ADJUSTMENT
    def test_negative_points_adjustment_updates_balance_and_ledger(self):
        # Give initial points first
        adjust_points(actor=self.admin, caller=self.caller, points=100, reason='Initial bonus')
        self.assertEqual(PointsEntry.objects.filter(caller=self.caller).aggregate(s=Sum('points'))['s'], 100)

        self.client.force_login(self.admin)
        url = reverse('web:caller-detail', args=[self.caller.pk])
        req_id = str(uuid.uuid4())
        response = self.client.post(url, {
            'action': 'adjust',
            'points': -40,
            'reason': 'Unexcused tardiness and missed team sync',
            'reason_type': 'OTHER',
            'request_id': req_id,
        })
        self.assertEqual(response.status_code, 302)

        follow_res = self.client.get(response.url)
        self.assertEqual(follow_res.status_code, 200)
        self.assertEqual(follow_res.context['stats']['lifetime_points'], 60)
        self.assertContains(follow_res, '-40')
        self.assertContains(follow_res, 'Unexcused tardiness and missed team sync')

        # Verify entry
        neg_entry = PointsEntry.objects.get(caller=self.caller, points=-40)
        self.assertEqual(neg_entry.event, PointsEntry.Event.ADMIN_ADJUSTMENT)
        self.assertEqual(neg_entry.recorded_by, self.admin)

    # 4. INPUT VALIDATION
    def test_zero_points_rejected(self):
        self.client.force_login(self.admin)
        url = reverse('web:caller-detail', args=[self.caller.pk])
        response = self.client.post(url, {
            'action': 'adjust',
            'points': 0,
            'reason': 'Zero points adjustment',
            'reason_type': 'OTHER',
            'request_id': str(uuid.uuid4()),
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'non-zero')
        self.assertEqual(PointsEntry.objects.filter(caller=self.caller).count(), 0)

    def test_empty_or_whitespace_reason_rejected(self):
        self.client.force_login(self.admin)
        url = reverse('web:caller-detail', args=[self.caller.pk])
        for bad_reason in ('', '   ', '\t\n'):
            response = self.client.post(url, {
                'action': 'adjust',
                'points': 50,
                'reason': bad_reason,
                'reason_type': 'OTHER',
                'request_id': str(uuid.uuid4()),
            })
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, 'reason is required')
            self.assertEqual(PointsEntry.objects.filter(caller=self.caller).count(), 0)

    # 5. AUDIT HISTORY & IMMUTABILITY
    def test_audit_history_and_immutability(self):
        self.client.force_login(self.admin)
        url = reverse('web:caller-detail', args=[self.caller.pk])
        self.client.post(url, {
            'action': 'adjust',
            'points': 25,
            'reason': 'Weekly performance award',
            'reason_type': 'OTHER',
            'request_id': str(uuid.uuid4()),
        })

        entry = PointsEntry.objects.get(caller=self.caller)
        self.assertEqual(entry.points, 25)
        self.assertEqual(entry.recorded_by, self.admin)
        self.assertIsNotNone(entry.occurred_at)

        # PointsEntry cannot be modified
        from django.core.exceptions import ValidationError
        with self.assertRaises(ValidationError):
            entry.points = 50
            entry.save()

        # PointsEntry cannot be deleted
        with self.assertRaises(ValidationError):
            entry.delete()

        # AuditEvent created
        audit = AuditEvent.objects.filter(category='POINTS').first()
        self.assertIsNotNone(audit)
        self.assertEqual(audit.actor, self.admin)
        self.assertIn('25', audit.description)

    # 6. IDEMPOTENCY & DUPLICATE SUBMISSION
    def test_idempotent_submission_prevents_duplicate(self):
        self.client.force_login(self.admin)
        url = reverse('web:caller-detail', args=[self.caller.pk])
        shared_key = str(uuid.uuid4())
        payload = {
            'action': 'adjust',
            'points': 50,
            'reason': 'Idempotent spot bonus',
            'reason_type': 'OTHER',
            'request_id': shared_key,
        }

        # First submit
        r1 = self.client.post(url, payload)
        self.assertEqual(r1.status_code, 302)

        # Second submit with identical payload & request_id
        r2 = self.client.post(url, payload)
        self.assertEqual(r2.status_code, 302)

        # Only 1 entry and 50 points total, never duplicated
        self.assertEqual(PointsEntry.objects.filter(caller=self.caller).count(), 1)
        self.assertEqual(PointsEntry.objects.filter(caller=self.caller).aggregate(s=Sum('points'))['s'], 50)

    # 7. DUAL-ROLE & NON-CALLER EMPLOYEE ADJUSTMENT
    def test_admin_can_adjust_points_for_dual_role_and_employee(self):
        self.client.force_login(self.admin)

        # Dual-role employee
        url_dual = reverse('web:caller-detail', args=[self.dual_employee.pk])
        res_dual = self.client.post(url_dual, {
            'action': 'adjust',
            'points': 60,
            'reason': 'Dual role counseling bonus',
            'reason_type': 'OTHER',
            'request_id': str(uuid.uuid4()),
        })
        self.assertEqual(res_dual.status_code, 302)
        self.assertEqual(PointsEntry.objects.filter(caller=self.dual_employee).aggregate(s=Sum('points'))['s'], 60)

        # General employee
        url_emp = reverse('web:caller-detail', args=[self.employee.pk])
        res_emp = self.client.post(url_emp, {
            'action': 'adjust',
            'points': 45,
            'reason': 'General staff recognition',
            'reason_type': 'OTHER',
            'request_id': str(uuid.uuid4()),
        })
        self.assertEqual(res_emp.status_code, 302)
        self.assertEqual(PointsEntry.objects.filter(caller=self.employee).aggregate(s=Sum('points'))['s'], 45)


class ConcurrentPointsAdjustmentTests(TransactionTestCase):
    def setUp(self):
        self.admin = User.objects.create_user('admin_concurrent', role=User.Role.ADMIN, is_staff=True)
        self.caller = User.objects.create_user('caller_concurrent', role=User.Role.CALLER)

    def tearDown(self):
        from django.db import connections
        connections.close_all()
        super().tearDown()

    def test_concurrent_adjustments_maintain_accurate_balance(self):
        adjustments_to_run = [
            (10, 'Concurrent bonus 1'),
            (20, 'Concurrent bonus 2'),
            (-15, 'Concurrent deduction 1'),
            (30, 'Concurrent bonus 3'),
            (-5, 'Concurrent deduction 2'),
        ]
        expected_total = sum(pts for pts, _ in adjustments_to_run)

        def perform_adjustment(pts, reason):
            from django.db import connections
            try:
                return adjust_points(
                    actor=self.admin,
                    caller=self.caller,
                    points=pts,
                    reason=reason,
                    key=uuid.uuid4(),
                )
            finally:
                connections.close_all()

        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
            futures = [executor.submit(perform_adjustment, pts, r) for pts, r in adjustments_to_run]
            results = [f.result() for f in concurrent.futures.as_completed(futures)]

        from django.db import connections
        connections.close_all()
        entries = PointsEntry.objects.filter(caller=self.caller)
        self.assertEqual(entries.count(), 5)
        total_balance = entries.aggregate(s=Sum('points'))['s']
        self.assertEqual(total_balance, expected_total)
