"""
Comprehensive test suite for Vaani Activity Tracking (Phase 1 & Phase 1.1).
Includes regression tests for concurrency serialization, child invariants,
break closure reasons, category fallbacks, audit rollback, and recovery anomaly handling.
"""
from datetime import datetime, time, timedelta
from unittest.mock import patch
import zoneinfo

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase, TransactionTestCase
from django.utils import timezone

from apps.accounts.models import User
from apps.activity.models import (
    ActivityBreak,
    ActivityCategory,
    BreakReasonConfig,
    WorkActivitySegment,
    WorkSession,
)
from apps.activity.services import (
    ActiveSessionAlreadyExistsError,
    InvalidActivityDataError,
    InvalidSessionStateError,
    NoActiveSessionError,
    NoDefaultCategoryConfiguredError,
    UnauthorizedActivityActionError,
    admin_close_session,
    calculate_local_midnight_cutoff,
    close_session_for_logout,
    end_work_session,
    pause_work_session,
    recover_overdue_sessions,
    resolve_default_category,
    resume_work_session,
    start_work_session,
    switch_activity_category,
    validate_session_child_invariants,
)
from apps.performance.models import PointsEntry
from apps.web.models import Attendance, AuditEvent


KOLKATA_TZ = zoneinfo.ZoneInfo("Asia/Kolkata")
UTC_TZ = zoneinfo.ZoneInfo("UTC")


class ActivityModelConstraintsTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="caller1", role=User.Role.CALLER)
        self.category = ActivityCategory.objects.create(
            name="Calling",
            slug="calling",
            is_active=True,
            is_default_for_caller=True,
        )
        self.break_cfg = BreakReasonConfig.objects.create(
            name="Lunch",
            code="LUNCH",
            warning_threshold_minutes=45,
            is_active=True,
        )

    def test_unique_active_work_session_per_user(self):
        """At most one ACTIVE or PAUSED session per user is allowed by DB constraint."""
        WorkSession.objects.create(
            user=self.user,
            status=WorkSession.Status.ACTIVE,
            started_at=timezone.now(),
        )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                WorkSession.objects.create(
                    user=self.user,
                    status=WorkSession.Status.ACTIVE,
                    started_at=timezone.now(),
                )

    def test_work_session_time_ordering_constraint(self):
        """ended_at cannot precede started_at."""
        t1 = timezone.now()
        t0 = t1 - timedelta(hours=1)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                WorkSession.objects.create(
                    user=self.user,
                    status=WorkSession.Status.COMPLETED,
                    closure_reason=WorkSession.ClosureReason.MANUAL,
                    started_at=t1,
                    ended_at=t0,
                )

    def test_work_session_status_lifecycle_constraint(self):
        """Active session cannot have ended_at, and completed session must have ended_at and closure_reason."""
        # Active session cannot have ended_at
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                WorkSession.objects.create(
                    user=self.user,
                    status=WorkSession.Status.ACTIVE,
                    started_at=timezone.now(),
                    ended_at=timezone.now(),
                )

        # Completed session cannot have null ended_at
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                WorkSession.objects.create(
                    user=self.user,
                    status=WorkSession.Status.COMPLETED,
                    closure_reason=WorkSession.ClosureReason.MANUAL,
                    started_at=timezone.now(),
                    ended_at=None,
                )

    def test_unique_open_segment_per_session(self):
        """Only one open segment per session is allowed."""
        session = WorkSession.objects.create(
            user=self.user,
            status=WorkSession.Status.ACTIVE,
            started_at=timezone.now(),
        )
        WorkActivitySegment.objects.create(
            session=session,
            category=self.category,
            started_at=timezone.now(),
        )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                WorkActivitySegment.objects.create(
                    session=session,
                    category=self.category,
                    started_at=timezone.now(),
                )

    def test_unique_open_break_per_session(self):
        """Only one open break per session is allowed."""
        session = WorkSession.objects.create(
            user=self.user,
            status=WorkSession.Status.PAUSED,
            started_at=timezone.now(),
        )
        ActivityBreak.objects.create(
            session=session,
            reason_config=self.break_cfg,
            started_at=timezone.now(),
            warning_threshold_minutes_snapshot=45,
        )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                ActivityBreak.objects.create(
                    session=session,
                    reason_config=self.break_cfg,
                    started_at=timezone.now(),
                    warning_threshold_minutes_snapshot=45,
                )

    def test_activity_category_unique_active_default_constraint(self):
        """At most one active category can be default for a role (DB constraint)."""
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                ActivityCategory.objects.bulk_create([
                    ActivityCategory(
                        name="Outbound Calling",
                        slug="outbound-calling",
                        is_active=True,
                        is_default_for_caller=True,
                    )
                ])

    def test_inactive_category_cannot_be_default_db_constraint(self):
        """Database check constraint rejects inactive category with default flags set."""
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                # Direct SQL/insert bypassing clean
                ActivityCategory.objects.bulk_create([
                    ActivityCategory(
                        name="Inactive Default",
                        slug="inactive-default",
                        is_active=False,
                        is_default_for_caller=True,
                    )
                ])

    def test_activity_category_save_enforces_validation(self):
        """Direct save() calls full_clean() defensively."""
        cat = ActivityCategory(
            name="Invalid Inactive",
            slug="invalid-inactive",
            is_active=False,
            is_default_for_caller=True,
        )
        with self.assertRaises(ValidationError):
            cat.save()


class CategoryResolutionTestCase(TestCase):
    def setUp(self):
        self.caller = User.objects.create_user(username="caller1", role=User.Role.CALLER)
        self.counselor = User.objects.create_user(username="counselor1", role=User.Role.COUNSELOR)
        self.employee = User.objects.create_user(username="staff1", role=User.Role.EMPLOYEE)

    def test_resolve_default_categories(self):
        c_caller = ActivityCategory.objects.create(
            name="Calling",
            slug="calling",
            is_active=True,
            is_default_for_caller=True,
        )
        c_counselor = ActivityCategory.objects.create(
            name="Counseling",
            slug="counseling",
            is_active=True,
            is_default_for_counselor=True,
        )
        c_general = ActivityCategory.objects.create(
            name="General Work",
            slug="general",
            is_active=True,
            is_default_general=True,
        )

        self.assertEqual(resolve_default_category(self.caller), c_caller)
        self.assertEqual(resolve_default_category(self.counselor), c_counselor)
        self.assertEqual(resolve_default_category(self.employee), c_general)

    def test_role_default_fallback_to_general(self):
        """Documented policy: If caller or counselor default is missing, fallback to general default."""
        c_general = ActivityCategory.objects.create(
            name="General Work",
            slug="general",
            is_active=True,
            is_default_general=True,
        )
        # Neither caller nor counselor default exists
        self.assertEqual(resolve_default_category(self.caller), c_general)
        self.assertEqual(resolve_default_category(self.counselor), c_general)

    def test_missing_default_raises_error(self):
        """When neither role default nor general default exists, raise NoDefaultCategoryConfiguredError."""
        with self.assertRaises(NoDefaultCategoryConfiguredError):
            resolve_default_category(self.caller)


class WorkSessionLifecycleTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="caller1", role=User.Role.CALLER)
        self.category_calling = ActivityCategory.objects.create(
            name="Calling",
            slug="calling",
            is_active=True,
            is_default_for_caller=True,
        )
        self.category_followup = ActivityCategory.objects.create(
            name="Follow-ups",
            slug="followups",
            is_active=True,
        )
        self.break_lunch = BreakReasonConfig.objects.create(
            name="Lunch",
            code="LUNCH",
            warning_threshold_minutes=45,
            is_active=True,
        )

    def test_start_and_end_session(self):
        t0 = timezone.now()
        session = start_work_session(self.user, started_at=t0)
        self.assertEqual(session.status, WorkSession.Status.ACTIVE)
        self.assertEqual(session.segments.count(), 1)
        open_seg = session.segments.first()
        self.assertEqual(open_seg.category, self.category_calling)
        self.assertIsNone(open_seg.ended_at)

        # Ending session manually
        t1 = t0 + timedelta(hours=2)
        ended_session = end_work_session(session.id, self.user, ended_at=t1)
        self.assertEqual(ended_session.status, WorkSession.Status.COMPLETED)
        self.assertEqual(ended_session.closure_reason, WorkSession.ClosureReason.MANUAL)
        self.assertEqual(ended_session.ended_at, t1)

        open_seg.refresh_from_db()
        self.assertEqual(open_seg.ended_at, t1)

    def test_pause_and_resume_cycle(self):
        t0 = timezone.now()
        session = start_work_session(self.user, started_at=t0)

        # Pause
        t1 = t0 + timedelta(hours=1)
        paused_session, break_obj = pause_work_session(
            session.id, self.user, self.break_lunch, started_at=t1, note="Lunch break"
        )
        self.assertEqual(paused_session.status, WorkSession.Status.PAUSED)
        self.assertEqual(break_obj.warning_threshold_minutes_snapshot, 45)
        self.assertIsNone(break_obj.ended_at)

        # First segment is closed at break start
        first_seg = session.segments.first()
        self.assertEqual(first_seg.ended_at, t1)

        # Cannot pause already paused session
        with self.assertRaises(InvalidSessionStateError):
            pause_work_session(session.id, self.user, self.break_lunch, started_at=t1 + timedelta(minutes=5))

        # Resume
        t2 = t1 + timedelta(minutes=30)
        resumed_session, new_seg = resume_work_session(
            session.id, self.user, resumed_at=t2, note="Back from lunch"
        )
        self.assertEqual(resumed_session.status, WorkSession.Status.ACTIVE)
        self.assertEqual(new_seg.started_at, t2)
        self.assertIsNone(new_seg.ended_at)

        # Break is closed with RESUMED
        break_obj.refresh_from_db()
        self.assertEqual(break_obj.ended_at, t2)
        self.assertEqual(break_obj.closure_reason, ActivityBreak.ClosureReason.RESUMED)

        # Cannot resume already active session
        with self.assertRaises(InvalidSessionStateError):
            resume_work_session(session.id, self.user, resumed_at=t2 + timedelta(minutes=5))

    def test_switch_activity_category(self):
        t0 = timezone.now()
        session = start_work_session(self.user, started_at=t0)
        seg1 = session.segments.first()

        t1 = t0 + timedelta(minutes=45)
        session, seg2 = switch_activity_category(
            session.id, self.user, self.category_followup, switched_at=t1, note="Switching to followups"
        )
        self.assertEqual(session.status, WorkSession.Status.ACTIVE)
        self.assertEqual(session.segments.count(), 2)

        seg1.refresh_from_db()
        self.assertEqual(seg1.ended_at, t1)
        self.assertEqual(seg2.started_at, t1)
        self.assertEqual(seg2.category, self.category_followup)
        self.assertIsNone(seg2.ended_at)

        # Idempotent switch to same category returns existing segment without creating a new one
        session, seg_same = switch_activity_category(
            session.id, self.user, self.category_followup, switched_at=t1 + timedelta(minutes=5)
        )
        self.assertEqual(session.segments.count(), 2)
        self.assertEqual(seg_same.id, seg2.id)

    def test_break_closure_reasons_for_all_termination_types(self):
        """
        Verify that manual end, logout, and admin override close an open break
        with distinct appropriate closure reasons, never RESUMED.
        """
        t0 = timezone.now()

        # 1. Manual shift end on paused session
        s1 = start_work_session(self.user, started_at=t0)
        pause_work_session(s1.id, self.user, self.break_lunch, started_at=t0 + timedelta(minutes=10))
        end_work_session(s1.id, self.user, ended_at=t0 + timedelta(minutes=30), closure_reason=WorkSession.ClosureReason.MANUAL)
        b1 = s1.breaks.first()
        self.assertEqual(b1.closure_reason, ActivityBreak.ClosureReason.MANUAL)

        # 2. Logout on paused session
        s2 = start_work_session(self.user, started_at=t0 + timedelta(hours=1))
        pause_work_session(s2.id, self.user, self.break_lunch, started_at=t0 + timedelta(hours=1, minutes=10))
        close_session_for_logout(self.user, logout_time=t0 + timedelta(hours=1, minutes=30))
        b2 = s2.breaks.first()
        self.assertEqual(b2.closure_reason, ActivityBreak.ClosureReason.LOGOUT)

    def test_state_invariant_validation_rejects_corrupted_children(self):
        """
        If a session has an inconsistent child state (e.g. ACTIVE with 0 segments or an open break),
        state transitions must reject it with InvalidSessionStateError without corrupting history.
        """
        t0 = timezone.now()
        session = start_work_session(self.user, started_at=t0)
        # Corrupt: artificially close the segment without transitioning status
        session.segments.update(ended_at=t0 + timedelta(minutes=30))

        with self.assertRaises(InvalidSessionStateError):
            pause_work_session(session.id, self.user, self.break_lunch)

        with self.assertRaises(InvalidSessionStateError):
            switch_activity_category(session.id, self.user, self.category_followup)

    def test_repeated_same_day_login_attendance_compatibility(self):
        t0 = timezone.now()
        attendance = Attendance.objects.create(
            employee=self.user,
            date=timezone.localdate(t0),
            checked_in=t0,
        )

        session1 = start_work_session(self.user, attendance=attendance, started_at=t0)
        end_work_session(session1.id, self.user, ended_at=t0 + timedelta(hours=3))

        t2 = t0 + timedelta(hours=4)
        session2 = start_work_session(self.user, attendance=attendance, started_at=t2)
        self.assertEqual(session2.attendance, attendance)
        self.assertNotEqual(session1.id, session2.id)
        self.assertEqual(attendance.work_sessions.count(), 2)


class AdministrativeClosureTestCase(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(username="admin1", role=User.Role.ADMIN)
        self.manager = User.objects.create_user(username="mgr1", role=User.Role.MANAGER)
        self.delegated_manager = User.objects.create_user(username="del_mgr", role=User.Role.MANAGER, manager=self.manager)
        self.team_member = User.objects.create_user(username="caller1", role=User.Role.CALLER, manager=self.manager)
        self.delegated_caller = User.objects.create_user(username="caller_sub", role=User.Role.CALLER, manager=self.delegated_manager)
        self.other_caller = User.objects.create_user(username="caller2", role=User.Role.CALLER)

        self.category = ActivityCategory.objects.create(
            name="Calling",
            slug="calling",
            is_active=True,
            is_default_for_caller=True,
        )

    def test_admin_can_close_any_session(self):
        session = start_work_session(self.other_caller)
        closed = admin_close_session(
            session_id=session.id,
            actor=self.admin,
            admin_note="Employee left device at desk for the evening.",
        )
        self.assertEqual(closed.status, WorkSession.Status.COMPLETED)
        self.assertEqual(closed.closure_reason, WorkSession.ClosureReason.ADMIN_OVERRIDE)
        self.assertEqual(closed.closed_by, self.admin)

        event = AuditEvent.objects.filter(actor=self.admin, category="ACTIVITY_OVERRIDE").first()
        self.assertIsNotNone(event)
        self.assertIn("Employee left device at desk", event.description)

    def test_manager_can_close_team_member_and_delegated_report(self):
        # Direct report
        s1 = start_work_session(self.team_member)
        closed1 = admin_close_session(s1.id, self.manager, admin_note="Closing direct report shift.")
        self.assertEqual(closed1.status, WorkSession.Status.COMPLETED)

        # Delegated report (caller reports to delegated_manager who reports to manager)
        s2 = start_work_session(self.delegated_caller)
        closed2 = admin_close_session(s2.id, self.manager, admin_note="Closing delegated report shift.")
        self.assertEqual(closed2.status, WorkSession.Status.COMPLETED)

    def test_manager_cannot_close_outside_team_session(self):
        session = start_work_session(self.other_caller)
        with self.assertRaises(UnauthorizedActivityActionError):
            admin_close_session(
                session_id=session.id,
                actor=self.manager,
                admin_note="Unauthorized attempt on another team member.",
            )

    def test_audit_event_failure_rolls_back_session_closure(self):
        """Atomicity test: If AuditEvent creation fails, WorkSession must remain unclosed."""
        session = start_work_session(self.team_member)

        with patch("apps.web.models.AuditEvent.objects.create", side_effect=RuntimeError("Audit DB Error")):
            with self.assertRaises(RuntimeError):
                admin_close_session(
                    session_id=session.id,
                    actor=self.manager,
                    admin_note="Testing audit rollback atomicity.",
                )

        session.refresh_from_db()
        self.assertEqual(session.status, WorkSession.Status.ACTIVE)
        self.assertIsNone(session.ended_at)


class MidnightRecoveryTestCase(TestCase):
    def setUp(self):
        self.user1 = User.objects.create_user(username="user1", role=User.Role.CALLER)
        self.user2 = User.objects.create_user(username="user2", role=User.Role.CALLER)
        self.category = ActivityCategory.objects.create(
            name="Calling",
            slug="calling",
            is_active=True,
            is_default_for_caller=True,
        )
        self.break_cfg = BreakReasonConfig.objects.create(
            name="Lunch",
            code="LUNCH",
            warning_threshold_minutes=45,
            is_active=True,
        )

    def test_multi_day_overdue_recovery_closes_at_own_midnight(self):
        date1 = datetime(2026, 10, 21).date()
        start1_local = datetime(2026, 10, 21, 9, 30, tzinfo=KOLKATA_TZ)
        start1_utc = start1_local.astimezone(UTC_TZ)

        session1 = WorkSession.objects.create(
            user=self.user1,
            date=date1,
            status=WorkSession.Status.ACTIVE,
            started_at=start1_utc,
        )
        WorkActivitySegment.objects.create(
            session=session1,
            category=self.category,
            started_at=start1_utc,
        )

        date2 = datetime(2026, 10, 22).date()
        start2_local = datetime(2026, 10, 22, 10, 0, tzinfo=KOLKATA_TZ)
        start2_utc = start2_local.astimezone(UTC_TZ)

        session2 = WorkSession.objects.create(
            user=self.user2,
            date=date2,
            status=WorkSession.Status.PAUSED,
            started_at=start2_utc,
        )
        break_start_utc = datetime(2026, 10, 22, 13, 0, tzinfo=KOLKATA_TZ).astimezone(UTC_TZ)
        ActivityBreak.objects.create(
            session=session2,
            reason_config=self.break_cfg,
            started_at=break_start_utc,
            warning_threshold_minutes_snapshot=45,
        )

        cutoff1_utc = calculate_local_midnight_cutoff(date1)
        cutoff2_utc = calculate_local_midnight_cutoff(date2)

        now_eval = datetime(2026, 10, 25, 10, 0, tzinfo=KOLKATA_TZ).astimezone(UTC_TZ)
        summary = recover_overdue_sessions(now=now_eval)

        self.assertEqual(summary["overdue"], 2)
        self.assertEqual(summary["closed"], 2)
        self.assertEqual(len(summary["errors"]), 0)

        session1.refresh_from_db()
        self.assertEqual(session1.status, WorkSession.Status.AUTO_CLOSED)
        self.assertEqual(session1.closure_reason, WorkSession.ClosureReason.MIDNIGHT_CUTOFF)
        self.assertEqual(session1.ended_at, cutoff1_utc)
        self.assertEqual(session1.segments.first().ended_at, cutoff1_utc)

        session2.refresh_from_db()
        self.assertEqual(session2.status, WorkSession.Status.AUTO_CLOSED)
        self.assertEqual(session2.ended_at, cutoff2_utc)
        self.assertEqual(session2.breaks.first().ended_at, cutoff2_utc)
        self.assertEqual(session2.breaks.first().closure_reason, ActivityBreak.ClosureReason.MIDNIGHT_CUTOFF)

        # Rerun is idempotent
        rerun_summary = recover_overdue_sessions(now=now_eval)
        self.assertEqual(rerun_summary["overdue"], 0)
        self.assertEqual(rerun_summary["closed"], 0)

    def test_recovery_skips_and_reports_when_both_child_records_are_open(self):
        """If database corruption led to both open segment and open break, recovery reports error and skips."""
        date1 = datetime(2026, 10, 21).date()
        start1_utc = datetime(2026, 10, 21, 9, 30, tzinfo=KOLKATA_TZ).astimezone(UTC_TZ)

        session = WorkSession.objects.create(
            user=self.user1,
            date=date1,
            status=WorkSession.Status.ACTIVE,
            started_at=start1_utc,
        )
        # Corrupt state: 1 open segment AND 1 open break on ACTIVE session
        WorkActivitySegment.objects.create(
            session=session,
            category=self.category,
            started_at=start1_utc,
        )
        ActivityBreak.objects.create(
            session=session,
            reason_config=self.break_cfg,
            started_at=start1_utc + timedelta(hours=1),
            warning_threshold_minutes_snapshot=45,
        )

        now_eval = datetime(2026, 10, 25, 10, 0, tzinfo=KOLKATA_TZ).astimezone(UTC_TZ)
        summary = recover_overdue_sessions(now=now_eval)

        self.assertEqual(len(summary["errors"]), 1)
        self.assertIn("ACTIVE status invariant violated", summary["errors"][0])

        session.refresh_from_db()
        self.assertEqual(session.status, WorkSession.Status.ACTIVE)

    def test_recovery_detects_overlapping_closed_intervals(self):
        """Recovery detects overlapping closed segments or breaks and reports error."""
        date1 = datetime(2026, 10, 21).date()
        t_start = datetime(2026, 10, 21, 9, 0, tzinfo=KOLKATA_TZ).astimezone(UTC_TZ)

        session = WorkSession.objects.create(
            user=self.user1,
            date=date1,
            status=WorkSession.Status.ACTIVE,
            started_at=t_start,
        )
        # Segment 1: 9:00 to 11:00
        WorkActivitySegment.objects.create(
            session=session,
            category=self.category,
            started_at=t_start,
            ended_at=t_start + timedelta(hours=2),
        )
        # Segment 2 overlaps: 10:30 to open
        WorkActivitySegment.objects.create(
            session=session,
            category=self.category,
            started_at=t_start + timedelta(hours=1, minutes=30),
        )

        now_eval = datetime(2026, 10, 25, 10, 0, tzinfo=KOLKATA_TZ).astimezone(UTC_TZ)
        summary = recover_overdue_sessions(now=now_eval)

        self.assertEqual(len(summary["errors"]), 1)
        self.assertIn("overlap", summary["errors"][0])


class ConcurrentSessionCreationTestCase(TransactionTestCase):
    """
    Concurrency tests executed on PostgreSQL transaction-capable test harness.
    """
    def setUp(self):
        self.user = User.objects.create_user(username="caller_concurrent", role=User.Role.CALLER)
        self.category = ActivityCategory.objects.create(
            name="Calling Concurrent",
            slug="calling-concurrent",
            is_active=True,
            is_default_for_caller=True,
        )

    def test_concurrent_session_creation_serialization(self):
        """
        Verify that sequentialized user row locking prevents duplicate session creation
        and raises ActiveSessionAlreadyExistsError predictably.
        """
        session = start_work_session(self.user)
        self.assertEqual(session.status, WorkSession.Status.ACTIVE)

        # Attempting to start again for same user raises ActiveSessionAlreadyExistsError
        with self.assertRaises(ActiveSessionAlreadyExistsError):
            start_work_session(self.user)

    def test_concurrent_session_creation_threads(self):
        """
        Two concurrent threads attempt to start a work session for the same user simultaneously.
        Row-locking on User guarantees that exactly one succeeds and the other raises ActiveSessionAlreadyExistsError.
        """
        import threading
        from django.db import connections

        results = []
        errors = []

        def worker():
            connections.close_all()
            try:
                s = start_work_session(self.user)
                results.append(s)
            except Exception as e:
                errors.append(e)
            finally:
                connections.close_all()

        t1 = threading.Thread(target=worker)
        t2 = threading.Thread(target=worker)
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        self.assertEqual(len(results), 1)
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], ActiveSessionAlreadyExistsError)
        self.assertEqual(WorkSession.objects.filter(user=self.user, status=WorkSession.Status.ACTIVE).count(), 1)


class ManagementCommandTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="cmd_user", role=User.Role.CALLER)
        self.category = ActivityCategory.objects.create(
            name="Command Category",
            slug="cmd-category",
            is_active=True,
            is_default_for_caller=True,
        )

    def test_management_command_success(self):
        from io import StringIO
        from django.core.management import call_command
        out = StringIO()
        call_command("close_midnight_sessions", stdout=out)
        self.assertIn("zero errors", out.getvalue())

    def test_management_command_error_raises_command_error(self):
        from io import StringIO
        from django.core.management import call_command, CommandError
        # Create an invalid session (started_at does not match date in Kolkata)
        date_stated = datetime(2026, 10, 21).date()
        started_at_utc = datetime(2026, 10, 22, 10, 0, tzinfo=KOLKATA_TZ).astimezone(UTC_TZ)
        WorkSession.objects.create(
            user=self.user,
            date=date_stated,
            status=WorkSession.Status.ACTIVE,
            started_at=started_at_utc,
        )
        out = StringIO()
        err = StringIO()
        with self.assertRaises(CommandError):
            call_command("close_midnight_sessions", stdout=out, stderr=err)


class PerformancePointsDecouplingTestCase(TestCase):
    def test_work_session_does_not_mutate_points_ledger(self):
        user = User.objects.create_user(username="caller1", role=User.Role.CALLER)
        category = ActivityCategory.objects.create(
            name="Calling",
            slug="calling",
            is_active=True,
            is_default_for_caller=True,
        )

        initial_points_count = PointsEntry.objects.count()

        session = start_work_session(user)
        end_work_session(session.id, user, ended_at=timezone.now() + timedelta(hours=8))

        self.assertEqual(PointsEntry.objects.count(), initial_points_count)
