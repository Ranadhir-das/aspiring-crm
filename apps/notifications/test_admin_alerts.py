from django.test import TestCase
from datetime import date, timedelta
from apps.accounts.models import User
from apps.notifications.models import Notification
from apps.notifications.services import (
    notify_admins_about_leave_request,
    notify_admins_about_pending_leads,
)
from apps.web.models import LeaveRequest


class AdminAlertNotificationTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            username='admin_boss',
            password='TestPassword123!',
            role=User.Role.ADMIN,
            is_active=True,
        )
        self.employee = User.objects.create_user(
            username='staff_john',
            password='TestPassword123!',
            role=User.Role.CALLER,
            is_active=True,
            first_name='John',
            last_name='Doe',
        )

    def test_leave_request_notifies_admin(self):
        leave = LeaveRequest.objects.create(
            employee=self.employee,
            start_date=date.today() + timedelta(days=2),
            end_date=date.today() + timedelta(days=4),
            reason='Family emergency',
            status=LeaveRequest.Status.PENDING,
        )

        notify_admins_about_leave_request(leave)

        admin_notifs = Notification.objects.filter(
            recipient=self.admin,
            type=Notification.Type.LEAVE_REQUEST,
        )
        self.assertEqual(admin_notifs.count(), 1)
        notif = admin_notifs.first()
        self.assertIn("John Doe", notif.title)
        self.assertIn("Family emergency", notif.body)
        self.assertEqual(notif.data.get('leave_id'), leave.pk)

        # Ensure employee does not get alerted as admin
        emp_notifs = Notification.objects.filter(
            recipient=self.employee,
            type=Notification.Type.LEAVE_REQUEST,
        )
        self.assertEqual(emp_notifs.count(), 0)

    def test_pending_leads_alert(self):
        notify_admins_about_pending_leads(12)

        admin_notifs = Notification.objects.filter(
            recipient=self.admin,
            type=Notification.Type.PENDING_LEADS,
        )
        self.assertEqual(admin_notifs.count(), 1)
        notif = admin_notifs.first()
        self.assertIn("12 leads pending", notif.body)
        self.assertEqual(notif.data.get('count'), 12)
