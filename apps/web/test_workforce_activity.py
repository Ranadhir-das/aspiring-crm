"""
Tests for Workforce Activity CRM views, templates, permissions, and settings.
"""
from datetime import date, timedelta
from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from apps.activity.models import (
    ActivityBreak,
    ActivityCategory,
    BreakReasonConfig,
    WorkActivitySegment,
    WorkSession,
)

User = get_user_model()


class WorkforceActivityTestCase(TestCase):
    def setUp(self):
        self.client = Client()

        # Users
        self.admin = User.objects.create_superuser(
            username='admin_act',
            password='Password123!',
            email='admin_act@example.com',
            role=User.Role.ADMIN,
        )
        self.manager = User.objects.create_user(
            username='manager_act',
            password='Password123!',
            email='manager_act@example.com',
            role=User.Role.MANAGER,
        )
        self.team_caller = User.objects.create_user(
            username='team_caller',
            password='Password123!',
            email='team_caller@example.com',
            role=User.Role.CALLER,
            manager=self.manager,
        )
        self.other_caller = User.objects.create_user(
            username='other_caller',
            password='Password123!',
            email='other_caller@example.com',
            role=User.Role.CALLER,
        )

        # Categories
        self.cat_call = ActivityCategory.objects.create(
            name="Outbound Calling",
            slug="outbound-calling",
            icon_key="phone",
            color_hex="#005DF2",
            is_active=True,
            is_default_for_caller=True,
            sort_order=1,
        )
        self.cat_training = ActivityCategory.objects.create(
            name="Training",
            slug="training",
            icon_key="book-open",
            color_hex="#10B981",
            is_active=True,
            sort_order=2,
        )

        # Break reasons
        self.break_lunch = BreakReasonConfig.objects.create(
            name="Lunch Break",
            code="LUNCH",
            warning_threshold_minutes=45,
            requires_note=False,
            is_active=True,
            sort_order=1,
        )
        self.break_tea = BreakReasonConfig.objects.create(
            name="Tea Break",
            code="TEA",
            warning_threshold_minutes=15,
            requires_note=False,
            is_active=True,
            sort_order=2,
        )

        # Sessions
        self.now = timezone.now()
        # 1. Team caller completed session yesterday
        t1 = self.now - timedelta(days=1, hours=4)
        t2 = self.now - timedelta(days=1, hours=3)
        t3 = self.now - timedelta(days=1, hours=2, minutes=30)
        t4 = self.now - timedelta(days=1)
        self.team_session = WorkSession.objects.create(
            user=self.team_caller,
            date=(self.now - timedelta(days=1)).date(),
            status=WorkSession.Status.COMPLETED,
            started_at=t1,
            ended_at=t4,
            closure_reason=WorkSession.ClosureReason.MANUAL,
        )
        # Segment 1: t1 -> t2
        WorkActivitySegment.objects.create(
            session=self.team_session,
            category=self.cat_call,
            started_at=t1,
            ended_at=t2,
        )
        # Break: t2 -> t3 (30 mins)
        ActivityBreak.objects.create(
            session=self.team_session,
            reason_config=self.break_lunch,
            started_at=t2,
            ended_at=t3,
            warning_threshold_minutes_snapshot=45,
            closure_reason=ActivityBreak.ClosureReason.RESUMED,
        )
        # Segment 2: t3 -> t4
        WorkActivitySegment.objects.create(
            session=self.team_session,
            category=self.cat_training,
            started_at=t3,
            ended_at=t4,
        )

        # 2. Other caller active session today
        t_start = self.now - timedelta(hours=1)
        self.other_session = WorkSession.objects.create(
            user=self.other_caller,
            date=self.now.date(),
            status=WorkSession.Status.ACTIVE,
            started_at=t_start,
        )
        WorkActivitySegment.objects.create(
            session=self.other_session,
            category=self.cat_call,
            started_at=t_start,
        )

    # -------------------------------------------------------------------------
    # Dashboard & Scoping Tests
    # -------------------------------------------------------------------------

    def test_admin_dashboard_sees_all_sessions(self):
        self.client.force_login(self.admin)
        url = reverse('web:workforce-activity')
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        content = resp.content.decode()

        self.assertIn("team_caller", content)
        self.assertIn("other_caller", content)
        self.assertIn(reverse('web:workforce-activity-settings'), content)

    def test_manager_dashboard_sees_only_own_team(self):
        self.client.force_login(self.manager)
        url = reverse('web:workforce-activity')
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        content = resp.content.decode()

        self.assertIn("team_caller", content)
        self.assertNotIn("other_caller", content)
        # Manager cannot access activity settings
        self.assertNotIn(reverse('web:workforce-activity-settings'), content)

    def test_caller_dashboard_sees_only_own_session(self):
        self.client.force_login(self.team_caller)
        url = reverse('web:workforce-activity')
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        content = resp.content.decode()

        self.assertIn("team_caller", content)
        self.assertNotIn("other_caller", content)

    def test_dashboard_filtering_by_status(self):
        self.client.force_login(self.admin)
        url = reverse('web:workforce-activity')

        # Filter by COMPLETED
        resp = self.client.get(url, {'status': WorkSession.Status.COMPLETED})
        self.assertEqual(resp.status_code, 200)
        records = resp.context['records']
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].id, self.team_session.id)
        self.assertEqual(records[0].user, self.team_caller)
        self.assertEqual(records[0].status, WorkSession.Status.COMPLETED)
        # Verify active other_session is excluded from filtered records
        self.assertNotIn(self.other_session.id, [r.id for r in records])

        # Filter by ACTIVE
        resp = self.client.get(url, {'status': WorkSession.Status.ACTIVE})
        self.assertEqual(resp.status_code, 200)
        records = resp.context['records']
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].id, self.other_session.id)
        self.assertEqual(records[0].user, self.other_caller)
        self.assertEqual(records[0].status, WorkSession.Status.ACTIVE)
        # Verify completed team_session is excluded from filtered records
        self.assertNotIn(self.team_session.id, [r.id for r in records])

    def test_dashboard_summary_metrics_capping_disclosure(self):
        from unittest.mock import patch
        self.client.force_login(self.admin)
        url = reverse('web:workforce-activity')

        with patch('apps.web.workforce_activity_views._calculate_summary_metrics') as mock_calc:
            mock_calc.return_value = {
                'active_count': 1,
                'total_matching': 1500,
                'analyzed_count': 1000,
                'is_capped': True,
                'total_gross_seconds': 3600000,
                'total_break_seconds': 360000,
                'total_net_seconds': 3240000,
            }
            resp = self.client.get(url)
            self.assertEqual(resp.status_code, 200)
            content = resp.content.decode()
            self.assertIn("Telemetry cap active", content)
            self.assertIn("capped", content)
            self.assertIn("1500", content)

    # -------------------------------------------------------------------------
    # Detail & Timeline Tests
    # -------------------------------------------------------------------------

    def test_admin_can_view_any_detail(self):
        self.client.force_login(self.admin)
        url = reverse('web:workforce-activity-detail', args=[self.team_session.id])
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        content = resp.content.decode()

        self.assertIn("Outbound Calling", content)
        self.assertIn("Lunch Break", content)
        self.assertIn("Training", content)

    def test_manager_access_control_detail(self):
        self.client.force_login(self.manager)

        # Allowed for team member
        url_team = reverse('web:workforce-activity-detail', args=[self.team_session.id])
        resp = self.client.get(url_team)
        self.assertEqual(resp.status_code, 200)

        # Forbidden for other caller
        url_other = reverse('web:workforce-activity-detail', args=[self.other_session.id])
        resp_forbidden = self.client.get(url_other)
        self.assertEqual(resp_forbidden.status_code, 403)

    def test_caller_access_control_detail(self):
        self.client.force_login(self.team_caller)

        # Allowed for self
        url_self = reverse('web:workforce-activity-detail', args=[self.team_session.id])
        resp = self.client.get(url_self)
        self.assertEqual(resp.status_code, 200)

        # Forbidden for another caller's session
        url_other = reverse('web:workforce-activity-detail', args=[self.other_session.id])
        resp_forbidden = self.client.get(url_other)
        self.assertEqual(resp_forbidden.status_code, 403)

    def test_admin_force_close_active_session(self):
        self.client.force_login(self.admin)
        url = reverse('web:workforce-activity-detail', args=[self.other_session.id])

        # POST with note >= 10 chars
        resp = self.client.post(url, {
            'action': 'close_session',
            'admin_note': 'Employee forgot to logout before leaving office.',
        }, follow=True)
        self.assertEqual(resp.status_code, 200)

        self.other_session.refresh_from_db()
        self.assertEqual(self.other_session.status, WorkSession.Status.COMPLETED)
        self.assertEqual(self.other_session.closure_reason, WorkSession.ClosureReason.ADMIN_OVERRIDE)
        self.assertEqual(self.other_session.closed_by, self.admin)
        self.assertIn("forgot to logout", self.other_session.admin_closure_note)

    def test_admin_force_close_rejects_short_note(self):
        self.client.force_login(self.admin)
        url = reverse('web:workforce-activity-detail', args=[self.other_session.id])

        resp = self.client.post(url, {
            'action': 'close_session',
            'admin_note': 'Too short',
        })
        self.assertEqual(resp.status_code, 200)
        self.other_session.refresh_from_db()
        self.assertEqual(self.other_session.status, WorkSession.Status.ACTIVE)

    # -------------------------------------------------------------------------
    # Activity Settings Tests
    # -------------------------------------------------------------------------

    def test_settings_permissions(self):
        # Caller gets 403
        self.client.force_login(self.team_caller)
        resp = self.client.get(reverse('web:workforce-activity-settings'))
        self.assertEqual(resp.status_code, 403)

        # Manager gets 403
        self.client.force_login(self.manager)
        resp = self.client.get(reverse('web:workforce-activity-settings'))
        self.assertEqual(resp.status_code, 403)

        # Admin gets 200
        self.client.force_login(self.admin)
        resp = self.client.get(reverse('web:workforce-activity-settings'))
        self.assertEqual(resp.status_code, 200)

    def test_admin_create_activity_category(self):
        self.client.force_login(self.admin)
        url = reverse('web:workforce-activity-settings')

        resp = self.client.post(url, {
            'action': 'create_category',
            'cat-name': 'Student Counseling',
            'cat-slug': 'student-counseling',
            'cat-icon_key': 'message-square',
            'cat-color_hex': '#8B5CF6',
            'cat-sort_order': 3,
            'cat-is_active': 'on',
            'cat-is_default_for_counselor': 'on',
        }, follow=True)
        self.assertEqual(resp.status_code, 200)

        created = ActivityCategory.objects.filter(slug='student-counseling').first()
        self.assertIsNotNone(created)
        self.assertTrue(created.is_default_for_counselor)
        self.assertEqual(created.color_hex, '#8B5CF6')

    def test_admin_toggle_category_deactivation_clears_defaults(self):
        self.client.force_login(self.admin)
        url = reverse('web:workforce-activity-settings')

        # Deactivate calling category (currently caller default)
        self.assertTrue(self.cat_call.is_default_for_caller)
        resp = self.client.post(url, {
            'action': 'toggle_category',
            'category_id': self.cat_call.id,
        }, follow=True)
        self.assertEqual(resp.status_code, 200)

        self.cat_call.refresh_from_db()
        self.assertFalse(self.cat_call.is_active)
        self.assertFalse(self.cat_call.is_default_for_caller)

    def test_admin_create_break_reason(self):
        self.client.force_login(self.admin)
        url = reverse('web:workforce-activity-settings')

        resp = self.client.post(url, {
            'action': 'create_break_reason',
            'brk-name': 'Emergency Personal',
            'brk-code': 'emergency',
            'brk-warning_threshold_minutes': 30,
            'brk-requires_note': 'on',
            'brk-sort_order': 5,
            'brk-is_active': 'on',
        }, follow=True)
        self.assertEqual(resp.status_code, 200)

        reason = BreakReasonConfig.objects.filter(code='EMERGENCY').first()
        self.assertIsNotNone(reason)
        self.assertEqual(reason.warning_threshold_minutes, 30)
        self.assertTrue(reason.requires_note)

    def test_admin_toggle_break_reason(self):
        self.client.force_login(self.admin)
        url = reverse('web:workforce-activity-settings')

        resp = self.client.post(url, {
            'action': 'toggle_break_reason',
            'reason_id': self.break_tea.id,
        }, follow=True)
        self.assertEqual(resp.status_code, 200)

        self.break_tea.refresh_from_db()
        self.assertFalse(self.break_tea.is_active)

    # -------------------------------------------------------------------------
    # Sidebar Navigation Test
    # -------------------------------------------------------------------------

    def test_sidebar_includes_workforce_activity_link(self):
        for user in [self.admin, self.manager, self.team_caller]:
            self.client.force_login(user)
            resp = self.client.get(reverse('web:dashboard'))
            self.assertEqual(resp.status_code, 200)
            self.assertIn(reverse('web:workforce-activity'), resp.content.decode())

