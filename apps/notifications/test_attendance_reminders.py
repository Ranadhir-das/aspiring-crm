"""
Automated unit tests for employee shift login and logout reminder notifications.

Covers:
1. LOGIN reminder for employees who have not started shift.
2. LOGIN reminder skipped if employee already started shift (WorkSession or Attendance).
3. LOGOUT reminder for employees with open unended shifts.
4. LOGOUT reminder skipped if employee already ended shift or never started.
5. Idempotent duplicate prevention on repeated scheduler runs.
6. Sunday (weekly off) global skip.
7. Company holiday global skip.
8. Approved leave per-employee skip.
9. Explicit attendance status WEEK_OFF skip.
10. Timezone handling and AUTO mode (morning -> LOGIN, evening -> LOGOUT).
11. Management command execution, arguments, and dry-run flag.
12. Targeted user_id filtering.
13. Super admin, inactive, and pending registration exclusions.
"""
from datetime import date, datetime, time, timedelta
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.utils import timezone
import zoneinfo

from apps.accounts.models import User
from apps.activity.models import WorkSession
from apps.notifications.attendance_reminders import (
    REMINDER_LOGIN,
    REMINDER_LOGOUT,
    send_attendance_reminders,
)
from apps.notifications.models import Notification
from apps.web.models import Attendance, Holiday, LeaveRequest


class AttendanceRemindersTests(TestCase):
    def setUp(self):
        super().setUp()
        self.tz = zoneinfo.ZoneInfo("Asia/Kolkata")

        # Mock Expo push notification dispatch
        self.expo_mock = patch("apps.notifications.team_events.expo_request").start()
        self.addCleanup(patch.stopall)
        self.expo_mock.return_value = {"data": []}

        # Target working date: Monday, 2 November 2026
        self.target_date = date(2026, 11, 2)
        self.morning_now = datetime(2026, 11, 2, 9, 30, tzinfo=self.tz)
        self.evening_now = datetime(2026, 11, 2, 18, 30, tzinfo=self.tz)

        # Standard active employees
        self.emp1 = User.objects.create_user(
            username="emp1_remind",
            email="emp1@example.com",
            role=User.Role.EMPLOYEE,
            is_active=True,
        )
        self.emp2 = User.objects.create_user(
            username="emp2_remind",
            email="emp2@example.com",
            role=User.Role.CALLER,
            is_active=True,
        )
        self.emp3 = User.objects.create_user(
            username="emp3_remind",
            email="emp3@example.com",
            role=User.Role.COUNSELOR,
            is_active=True,
        )

        # Inactive / Pending / SuperAdmin
        self.admin = User.objects.create_superuser(
            username="super_remind",
            email="admin@example.com",
            role=User.Role.SUPER_ADMIN,
        )
        self.inactive = User.objects.create_user(
            username="inactive_remind",
            is_active=False,
        )
        self.pending = User.objects.create_user(
            username="pending_remind",
            registration_pending=True,
        )

    def test_login_reminder_dispatched_to_unstarted_employees(self):
        """LOGIN reminder sent to all eligible scheduled employees who haven't started shift."""
        summary = send_attendance_reminders(
            reminder_type=REMINDER_LOGIN,
            now=self.morning_now,
        )

        self.assertEqual(summary["status"], "COMPLETED")
        self.assertEqual(summary["sent_count"], 3)
        self.assertCountEqual(summary["recipient_ids"], [self.emp1.pk, self.emp2.pk, self.emp3.pk])

        notifs = list(Notification.objects.filter(type=Notification.Type.ATTENDANCE_REMINDER))
        self.assertEqual(len(notifs), 3)
        for n in notifs:
            self.assertEqual(n.title, "Shift Start Reminder")
            self.assertIn("02 Nov 2026", n.body)
            self.assertEqual(n.data.get("reminder_type"), "LOGIN")
            self.assertEqual(n.data.get("target_date"), "2026-11-02")

    def test_login_reminder_skipped_if_worksession_already_started(self):
        """LOGIN reminder skipped for employees who already started a WorkSession."""
        # emp1 started WorkSession at 9:15 AM
        WorkSession.objects.create(
            user=self.emp1,
            date=self.target_date,
            started_at=datetime(2026, 11, 2, 9, 15, tzinfo=self.tz),
            status=WorkSession.Status.ACTIVE,
        )

        summary = send_attendance_reminders(
            reminder_type=REMINDER_LOGIN,
            now=self.morning_now,
        )

        self.assertEqual(summary["skipped_already_started"], 1)
        self.assertEqual(summary["sent_count"], 2)
        self.assertCountEqual(summary["recipient_ids"], [self.emp2.pk, self.emp3.pk])

    def test_login_reminder_skipped_if_legacy_attendance_already_started(self):
        """LOGIN reminder skipped for employees who checked in via legacy Attendance."""
        Attendance.objects.create(
            employee=self.emp2,
            date=self.target_date,
            checked_in=datetime(2026, 11, 2, 9, 10, tzinfo=self.tz),
            status="PRESENT",
        )

        summary = send_attendance_reminders(
            reminder_type=REMINDER_LOGIN,
            now=self.morning_now,
        )

        self.assertEqual(summary["skipped_already_started"], 1)
        self.assertEqual(summary["sent_count"], 2)
        self.assertCountEqual(summary["recipient_ids"], [self.emp1.pk, self.emp3.pk])

    def test_logout_reminder_dispatched_to_unended_shifts(self):
        """LOGOUT reminder sent to employees who started shift and have not ended it."""
        # emp1: active WorkSession
        WorkSession.objects.create(
            user=self.emp1,
            date=self.target_date,
            started_at=datetime(2026, 11, 2, 9, 30, tzinfo=self.tz),
            status=WorkSession.Status.ACTIVE,
        )
        # emp2: paused WorkSession (still unended)
        WorkSession.objects.create(
            user=self.emp2,
            date=self.target_date,
            started_at=datetime(2026, 11, 2, 9, 45, tzinfo=self.tz),
            status=WorkSession.Status.PAUSED,
        )
        # emp3: never started shift today

        summary = send_attendance_reminders(
            reminder_type=REMINDER_LOGOUT,
            now=self.evening_now,
        )

        self.assertEqual(summary["status"], "COMPLETED")
        self.assertEqual(summary["sent_count"], 2)
        self.assertCountEqual(summary["recipient_ids"], [self.emp1.pk, self.emp2.pk])
        self.assertEqual(summary["skipped_not_started"], 1)

        notifs = list(Notification.objects.filter(type=Notification.Type.ATTENDANCE_REMINDER))
        self.assertEqual(len(notifs), 2)
        for n in notifs:
            self.assertEqual(n.title, "Shift End Reminder")
            self.assertIn("02 Nov 2026", n.body)
            self.assertEqual(n.data.get("reminder_type"), "LOGOUT")

    def test_logout_reminder_skipped_if_shift_already_ended(self):
        """LOGOUT reminder skipped when shift is completed/checked-out."""
        # emp1 completed session
        WorkSession.objects.create(
            user=self.emp1,
            date=self.target_date,
            started_at=datetime(2026, 11, 2, 9, 30, tzinfo=self.tz),
            ended_at=datetime(2026, 11, 2, 18, 0, tzinfo=self.tz),
            status=WorkSession.Status.COMPLETED,
            closure_reason=WorkSession.ClosureReason.MANUAL,
        )
        # emp2 completed legacy attendance
        Attendance.objects.create(
            employee=self.emp2,
            date=self.target_date,
            checked_in=datetime(2026, 11, 2, 9, 30, tzinfo=self.tz),
            checked_out=datetime(2026, 11, 2, 18, 15, tzinfo=self.tz),
            status="PRESENT",
        )
        # emp3 has active session
        WorkSession.objects.create(
            user=self.emp3,
            date=self.target_date,
            started_at=datetime(2026, 11, 2, 10, 0, tzinfo=self.tz),
            status=WorkSession.Status.ACTIVE,
        )

        summary = send_attendance_reminders(
            reminder_type=REMINDER_LOGOUT,
            now=self.evening_now,
        )

        self.assertEqual(summary["sent_count"], 1)
        self.assertCountEqual(summary["recipient_ids"], [self.emp3.pk])
        self.assertEqual(summary["skipped_already_ended"], 2)

    def test_idempotent_duplicate_prevention(self):
        """Repeated scheduler runs on the same day do not create duplicate notifications."""
        # Run 1
        summary1 = send_attendance_reminders(
            reminder_type=REMINDER_LOGIN,
            now=self.morning_now,
        )
        self.assertEqual(summary1["sent_count"], 3)
        self.assertEqual(Notification.objects.filter(type=Notification.Type.ATTENDANCE_REMINDER).count(), 3)

        # Run 2 on same day
        summary2 = send_attendance_reminders(
            reminder_type=REMINDER_LOGIN,
            now=self.morning_now + timedelta(minutes=15),
        )
        self.assertEqual(summary2["sent_count"], 0)
        self.assertEqual(summary2["skipped_already_notified"], 3)
        self.assertEqual(Notification.objects.filter(type=Notification.Type.ATTENDANCE_REMINDER).count(), 3)

    def test_sunday_weekly_off_globally_skipped(self):
        """Sunday is globally recognized as weekly off and skips sending entirely."""
        sunday_now = datetime(2026, 11, 1, 9, 30, tzinfo=self.tz)  # Nov 1, 2026 is Sunday
        summary = send_attendance_reminders(
            reminder_type=REMINDER_LOGIN,
            now=sunday_now,
        )
        self.assertEqual(summary["status"], "SKIPPED_WEEK_OFF")
        self.assertEqual(summary["sent_count"], 0)
        self.assertEqual(Notification.objects.count(), 0)

    def test_company_holiday_globally_skipped(self):
        """Company holidays skip reminder dispatch globally."""
        Holiday.objects.create(date=self.target_date, name="Diwali Holiday")
        summary = send_attendance_reminders(
            reminder_type=REMINDER_LOGIN,
            now=self.morning_now,
        )
        self.assertEqual(summary["status"], "SKIPPED_HOLIDAY")
        self.assertEqual(summary["sent_count"], 0)
        self.assertEqual(Notification.objects.count(), 0)

    def test_approved_leave_per_employee_skipped(self):
        """Employees with approved leave on the target date are excluded."""
        LeaveRequest.objects.create(
            employee=self.emp1,
            start_date=self.target_date,
            end_date=self.target_date,
            status="APPROVED",
            reason="Family function",
        )
        # Pending leave request should NOT skip emp2
        LeaveRequest.objects.create(
            employee=self.emp2,
            start_date=self.target_date,
            end_date=self.target_date,
            status="PENDING",
            reason="Headache",
        )

        summary = send_attendance_reminders(
            reminder_type=REMINDER_LOGIN,
            now=self.morning_now,
        )

        self.assertEqual(summary["skipped_leave"], 1)
        self.assertEqual(summary["sent_count"], 2)
        self.assertNotIn(self.emp1.pk, summary["recipient_ids"])
        self.assertIn(self.emp2.pk, summary["recipient_ids"])
        self.assertIn(self.emp3.pk, summary["recipient_ids"])

    def test_explicit_attendance_week_off_skipped(self):
        """Employees marked WEEK_OFF in attendance are excluded."""
        Attendance.objects.create(
            employee=self.emp1,
            date=self.target_date,
            status="WEEK_OFF",
        )

        summary = send_attendance_reminders(
            reminder_type=REMINDER_LOGIN,
            now=self.morning_now,
        )
        self.assertEqual(summary["skipped_week_off"], 1)
        self.assertNotIn(self.emp1.pk, summary["recipient_ids"])
        self.assertEqual(summary["sent_count"], 2)

    def test_timezone_and_auto_resolution(self):
        """AUTO mode evaluates to LOGIN before 14:00 IST and LOGOUT at or after 14:00 IST."""
        # 04:00 UTC = 09:30 AM IST (morning)
        utc_morning = datetime(2026, 11, 2, 4, 0, tzinfo=zoneinfo.ZoneInfo("UTC"))
        summary_m = send_attendance_reminders(
            reminder_type="AUTO",
            now=utc_morning,
            dry_run=True,
        )
        self.assertEqual(summary_m["reminder_type"], "LOGIN")
        self.assertEqual(summary_m["target_date"], "2026-11-02")

        # 13:00 UTC = 18:30 PM IST (evening)
        utc_evening = datetime(2026, 11, 2, 13, 0, tzinfo=zoneinfo.ZoneInfo("UTC"))
        summary_e = send_attendance_reminders(
            reminder_type="AUTO",
            now=utc_evening,
            dry_run=True,
        )
        self.assertEqual(summary_e["reminder_type"], "LOGOUT")
        self.assertEqual(summary_e["target_date"], "2026-11-02")

    def test_user_id_filtering(self):
        """user_id argument limits evaluation to only the specified user."""
        summary = send_attendance_reminders(
            reminder_type=REMINDER_LOGIN,
            now=self.morning_now,
            user_id=self.emp2.pk,
        )
        self.assertEqual(summary["eligible_checked"], 1)
        self.assertEqual(summary["sent_count"], 1)
        self.assertEqual(summary["recipient_ids"], [self.emp2.pk])

    def test_dry_run_does_not_persist_notifications(self):
        """Dry-run identifies recipients without persisting records in the database."""
        summary = send_attendance_reminders(
            reminder_type=REMINDER_LOGIN,
            now=self.morning_now,
            dry_run=True,
        )
        self.assertTrue(summary["dry_run"])
        self.assertEqual(summary["sent_count"], 3)
        self.assertEqual(Notification.objects.count(), 0)

    def test_super_admin_and_inactive_users_excluded(self):
        """Super admins, inactive users, and registration pending users are excluded."""
        summary = send_attendance_reminders(
            reminder_type=REMINDER_LOGIN,
            now=self.morning_now,
            dry_run=True,
        )
        self.assertNotIn(self.admin.pk, summary["recipient_ids"])
        self.assertNotIn(self.inactive.pk, summary["recipient_ids"])
        self.assertNotIn(self.pending.pk, summary["recipient_ids"])

    def test_management_command_invocation(self):
        """Management command dispatches correctly and handles CLI arguments."""
        # 1. Test dry-run via call_command
        call_command(
            "send_attendance_reminders",
            type="LOGIN",
            dry_run=True,
            now_override="2026-11-02T09:30:00+05:30",
        )
        self.assertEqual(Notification.objects.count(), 0)

        # 2. Test actual run via call_command with user-id
        call_command(
            "send_attendance_reminders",
            type="LOGIN",
            user_id=self.emp1.pk,
            now_override="2026-11-02T09:30:00+05:30",
        )
        self.assertEqual(
            Notification.objects.filter(recipient=self.emp1, type=Notification.Type.ATTENDANCE_REMINDER).count(),
            1,
        )

        # 3. Test invalid now format raises CommandError
        with self.assertRaises(CommandError):
            call_command(
                "send_attendance_reminders",
                now_override="not-a-valid-date",
            )
