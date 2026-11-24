"""
Comprehensive automated tests for the 4 urgent Vaani updates:
1. Caller visibility into counselor lead progress, statuses (PENDING / CONTACTED / CONTACTED_BY_MESSAGE),
   admission request status, and counselor notes (CALL / MESSAGE / NOTE) with strict object-level privacy.
2. Employee Workspace Attendance section: IN TIME = Shift Start, OUT TIME = Shift End,
   TOTAL HOURS = Shift Start -> Shift End, plus All-Months & Monthly breakdown of
   Present, Absent, Holiday, Sunday, and Approved Leave.
3. Work Report Reminder notification before shift end when net work time >= 7 hours 45 minutes (27,900s),
   with strict idempotency, break subtraction, and already-submitted report exclusion.
4. Responsive Notices page & CSS rules preventing mobile title clipping on long titles/audiences.
"""
from datetime import date, datetime, time, timedelta
from pathlib import Path
from unittest.mock import patch
import uuid
import zoneinfo

from django.conf import settings
from django.core.management import call_command
from django.test import Client, TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.activity.models import (
    ActivityBreak,
    ActivityCategory,
    BreakReasonConfig,
    WorkActivitySegment,
    WorkSession,
)
from apps.calls.models import Call
from apps.leads.counselor import AdmissionRequest, CounselorNote
from apps.leads.models import Lead
from apps.notifications.attendance_reminders import (
    REMINDER_WORK_REPORT,
    WORK_REPORT_REMINDER_NET_SECONDS,
    check_and_send_work_report_reminder_for_user,
    send_attendance_reminders,
)
from apps.notifications.models import Notification
from apps.web.models import Holiday, LeaveRequest, Notice, WorkReport


class Feature1CallerCounselorProgressTests(TestCase):
    def setUp(self):
        self.patcher = patch("apps.notifications.team_events.deliver_notifications", lambda records: None)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

        self.api = APIClient()
        self.web = Client()

        self.caller = User.objects.create_user(
            username="urgent_caller_1",
            first_name="Riya",
            last_name="Caller",
            role=User.Role.CALLER,
            is_active=True,
        )
        self.other_caller = User.objects.create_user(
            username="urgent_caller_2",
            first_name="Mohan",
            last_name="Caller",
            role=User.Role.CALLER,
            is_active=True,
        )
        self.counselor = User.objects.create_user(
            username="urgent_counselor_1",
            first_name="Kabir",
            last_name="Advisor",
            role=User.Role.COUNSELOR,
            can_contact_via_message=True,
            is_active=True,
        )
        self.other_counselor = User.objects.create_user(
            username="urgent_counselor_2",
            first_name="Neha",
            last_name="Advisor",
            role=User.Role.COUNSELOR,
            is_active=True,
        )
        self.manager = User.objects.create_user(
            username="urgent_manager_1",
            first_name="Arjun",
            last_name="Manager",
            role=User.Role.MANAGER,
            is_active=True,
        )

        self.lead = Lead.objects.create(
            name="Aarav Student",
            phone="9876543299",
            assigned_caller=self.caller,
            source="Website",
            status=Lead.Status.INTERESTED,
        )
        now = timezone.now()
        self.interested_call = Call.objects.create(
            lead=self.lead,
            caller=self.caller,
            phone_number=self.lead.phone,
            started_at=now - timedelta(minutes=5),
            ended_at=now - timedelta(minutes=4),
            duration_seconds=60,
            outcome="INTERESTED",
            selected_course="MBBS",
            expected_admission_year=2027,
            notes="Interested in MBBS admission",
        )

        # Forward lead to counselor as caller
        self.api.force_authenticate(self.caller)
        fw_res = self.api.post(
            f"/api/v1/mobile/leads/{self.lead.pk}/forward-counselor/",
            {"counselor_id": self.counselor.pk},
            format="json",
        )
        self.assertEqual(fw_res.status_code, 201)

    def test_assigned_caller_sees_counselor_progress_statuses_and_all_note_kinds(self):
        # 1. Initially PENDING with 0 notes
        self.api.force_authenticate(self.caller)
        state0 = self.api.get(f"/api/v1/mobile/leads/{self.lead.pk}/counselor/")
        self.assertEqual(state0.status_code, 200)
        self.assertEqual(state0.data["counselor_contact_status"], "PENDING")
        self.assertEqual(state0.data["counselor_contact_status_display"], "Pending Counselling")
        self.assertEqual(state0.data["counselor_notes"], [])

        # 2. Counselor adds general NOTE -> status stays PENDING, note visible to caller
        self.api.force_authenticate(self.counselor)
        r_note = self.api.post(
            f"/api/v1/mobile/counselor/leads/{self.lead.pk}/notes/",
            {"notes": "Reviewed student NEET score and preferences."},
            format="json",
        )
        self.assertEqual(r_note.status_code, 201)

        # 3. Counselor marks Contacted via Message -> status becomes CONTACTED_BY_MESSAGE
        r_msg = self.api.post(
            f"/api/v1/mobile/counselor/leads/{self.lead.pk}/message-contact/",
            {"notes": "Sent Georgia MBBS brochure on WhatsApp for 2027 intake."},
            format="json",
        )
        self.assertEqual(r_msg.status_code, 201)

        self.api.force_authenticate(self.caller)
        state_msg = self.api.get(f"/api/v1/mobile/leads/{self.lead.pk}/counselor/")
        self.assertEqual(state_msg.status_code, 200)
        self.assertEqual(state_msg.data["counselor_contact_status"], "CONTACTED_BY_MESSAGE")
        self.assertEqual(state_msg.data["counselor_contact_status_display"], "Contacted via Message")
        self.assertEqual(state_msg.data["counselor_message_count"], 1)
        self.assertEqual(len(state_msg.data["counselor_notes"]), 2)

        # 4. Counselor logs a Call Consultation + submits Admission Request (and manager adds internal review_note)
        self.api.force_authenticate(self.counselor)
        now = timezone.now()
        r_call = self.api.post(
            f"/api/v1/mobile/counselor/leads/{self.lead.pk}/notes/",
            {
                "notes": "Spoke with student and father for 4 minutes; ready for admission.",
                "client_event_id": str(uuid.uuid4()),
                "call": {
                    "started_at": (now - timedelta(seconds=240)).isoformat(),
                    "ended_at": now.isoformat(),
                    "duration_seconds": 240,
                },
            },
            format="json",
        )
        self.assertEqual(r_call.status_code, 201)

        r_adm = self.api.post(
            f"/api/v1/mobile/counselor/leads/{self.lead.pk}/admission-request/",
            {"message": "Passport and 12th marksheet verified."},
            format="json",
        )
        self.assertEqual(r_adm.status_code, 201)
        adm_req = AdmissionRequest.objects.get(lead=self.lead)
        adm_req.review_note = "CONFIDENTIAL_INTERNAL_MANAGER_AUDIT_NOTE"
        adm_req.save(update_fields=["review_note"])

        # 5. Caller checks LeadCounselorView even after lead status changes (e.g. ADMISSION_DONE or B2B)
        Lead.objects.filter(pk=self.lead.pk).update(status=Lead.Status.B2B)
        self.api.force_authenticate(self.caller)
        state_final = self.api.get(f"/api/v1/mobile/leads/{self.lead.pk}/counselor/")
        self.assertEqual(state_final.status_code, 200)
        self.assertEqual(state_final.data["counselor_contact_status"], "CONTACTED")
        self.assertEqual(state_final.data["counselor_contact_status_display"], "Contacted by Call")
        self.assertEqual(state_final.data["counselor_call_count"], 1)
        self.assertEqual(state_final.data["counselor_message_count"], 1)
        self.assertEqual(state_final.data["last_call_duration"], 240)
        self.assertTrue(state_final.data["has_pending_admission_request"])
        self.assertEqual(state_final.data["latest_admission_request_status"], "PENDING")
        self.assertEqual(len(state_final.data["admission_requests"]), 1)
        self.assertEqual(
            state_final.data["admission_requests"][0]["message"],
            "Passport and 12th marksheet verified.",
        )
        # Internal admin review_note must never leak to caller
        self.assertNotIn("review_note", state_final.data["admission_requests"][0])

        notes = state_final.data["counselor_notes"]
        self.assertEqual(len(notes), 3)
        self.assertEqual([n["kind"] for n in notes], ["CALL", "MESSAGE", "NOTE"])

        # 6. Mobile caller lead list includes counselor_progress summary
        list_res = self.api.get("/api/v1/mobile/leads/")
        self.assertEqual(list_res.status_code, 200)
        self.assertEqual(len(list_res.data), 1)
        prog = list_res.data[0]["counselor_progress"]
        self.assertIsNotNone(prog)
        self.assertTrue(prog["is_forwarded"])
        self.assertEqual(prog["counselor_name"], "Kabir Advisor")
        self.assertEqual(prog["contact_status"], "CONTACTED")
        self.assertEqual(prog["call_count"], 1)
        self.assertEqual(prog["message_count"], 1)
        self.assertEqual(prog["notes_count"], 3)
        self.assertEqual(prog["admission_request_status"], "PENDING")

        # 7. Unrelated caller and unrelated counselor are strictly blocked (404)
        self.api.force_authenticate(self.other_caller)
        self.assertEqual(
            self.api.get(f"/api/v1/mobile/leads/{self.lead.pk}/counselor/").status_code,
            404,
        )
        self.assertEqual(self.api.get("/api/v1/mobile/leads/").data, [])

        self.api.force_authenticate(self.other_counselor)
        self.assertEqual(
            self.api.get(f"/api/v1/mobile/counselor/leads/{self.lead.pk}/").status_code,
            404,
        )

        # 8. Web CRM lead detail shows counselor progress badges and notes to assigned caller
        self.web.force_login(self.caller)
        web_res = self.web.get(f"/leads/{self.lead.pk}/")
        self.assertEqual(web_res.status_code, 200)
        self.assertContains(web_res, "Kabir Advisor")
        self.assertContains(web_res, "Contacted by Call")
        self.assertContains(web_res, "Sent Georgia MBBS brochure on WhatsApp")


class Feature2EmployeeShiftAttendanceOverviewTests(TestCase):
    def setUp(self):
        self.tz = zoneinfo.ZoneInfo("Asia/Kolkata")
        self.api = APIClient()

        # Employee joined on 28 Oct 2026
        self.join_dt = datetime(2026, 10, 28, 9, 0, tzinfo=self.tz)
        self.emp = User.objects.create_user(
            username="shift_emp_att",
            first_name="Vikram",
            last_name="Staff",
            role=User.Role.EMPLOYEE,
            is_active=True,
            date_joined=self.join_dt,
        )
        self.cat = ActivityCategory.objects.create(
            name="General Office Work",
            slug="general-office-test",
            is_active=True,
            is_default_general=True,
        )
        self.break_reason = BreakReasonConfig.objects.create(
            name="Lunch Break",
            code="LUNCH_TEST",
            warning_threshold_minutes=45,
            is_active=True,
        )

    def _create_completed_shift(self, target_date: date, start_hm: tuple[int, int], end_hm: tuple[int, int], break_mins: int = 0):
        s_dt = datetime(target_date.year, target_date.month, target_date.day, start_hm[0], start_hm[1], tzinfo=self.tz)
        e_dt = datetime(target_date.year, target_date.month, target_date.day, end_hm[0], end_hm[1], tzinfo=self.tz)
        ws = WorkSession.objects.create(
            user=self.emp,
            date=target_date,
            status=WorkSession.Status.COMPLETED,
            closure_reason=WorkSession.ClosureReason.MANUAL,
            started_at=s_dt,
            ended_at=e_dt,
        )
        if break_mins > 0:
            b_start = s_dt + timedelta(hours=3)
            b_end = b_start + timedelta(minutes=break_mins)
            WorkActivitySegment.objects.create(session=ws, category=self.cat, started_at=s_dt, ended_at=b_start)
            ActivityBreak.objects.create(
                session=ws,
                reason_config=self.break_reason,
                started_at=b_start,
                ended_at=b_end,
                closure_reason=ActivityBreak.ClosureReason.RESUMED,
                warning_threshold_minutes_snapshot=45,
            )
            WorkActivitySegment.objects.create(session=ws, category=self.cat, started_at=b_end, ended_at=e_dt)
        else:
            WorkActivitySegment.objects.create(session=ws, category=self.cat, started_at=s_dt, ended_at=e_dt)
        return ws

    def test_shift_in_out_total_hours_and_all_months_breakdown(self):
        """
        Timeline from join date 2026-10-28 through 2026-11-05 (now = Thu 2026-11-05 15:00 IST):
        - 2026-10-28 (Wed): Full shift 09:30 -> 18:00 (8h 30m gross total hours, 30m break -> 8h 0m net) => FULL_SHIFT
        - 2026-10-29 (Thu): Holiday ("Dussehra Special") => HOLIDAY
        - 2026-10-30 (Fri): Approved Leave => LEAVE
        - 2026-10-31 (Sat): No shift => ABSENT
        (Oct 2026 totals: present=1, absent=1, holiday=1, sunday=0, leave=1, shift=8h 30m)

        - 2026-11-01 (Sun): Weekly off => WEEK_OFF (sunday=1)
        - 2026-11-02 (Mon): Short shift 10:00 -> 15:00 (5h 0m total hours) => SHORT_SHIFT
        - 2026-11-03 (Tue): No shift => ABSENT
        - 2026-11-04 (Wed): Full shift 09:00 -> 17:30 (8h 30m total hours, 0 break) => FULL_SHIFT
        - 2026-11-05 (Thu, today): Active shift started 10:00 (5h 0m elapsed at 15:00) => ACTIVE_SHIFT
        (Nov 2026 totals: present=3 [full=1, short=1, active=1], absent=1, holiday=0, sunday=1, leave=0)
        (All-months totals: present=4, absent=2, holiday=1, sunday=1, leave=1, months_count=2)
        """
        self._create_completed_shift(date(2026, 10, 28), (9, 30), (18, 0), break_mins=30)
        Holiday.objects.create(date=date(2026, 10, 29), name="Festival Holiday")
        LeaveRequest.objects.create(
            employee=self.emp,
            start_date=date(2026, 10, 30),
            end_date=date(2026, 10, 30),
            reason="Personal work",
            status="APPROVED",
        )
        # 2026-10-31 left empty -> ABSENT
        # 2026-11-01 is Sunday -> WEEK_OFF
        self._create_completed_shift(date(2026, 11, 2), (10, 0), (15, 0), break_mins=0)
        # 2026-11-03 left empty -> ABSENT
        self._create_completed_shift(date(2026, 11, 4), (9, 0), (17, 30), break_mins=0)

        # Active shift today (2026-11-05 10:00 IST)
        today_start = datetime(2026, 11, 5, 10, 0, tzinfo=self.tz)
        ws_today = WorkSession.objects.create(
            user=self.emp,
            date=date(2026, 11, 5),
            status=WorkSession.Status.ACTIVE,
            started_at=today_start,
        )
        WorkActivitySegment.objects.create(session=ws_today, category=self.cat, started_at=today_start)

        fake_now = datetime(2026, 11, 5, 15, 0, tzinfo=self.tz)
        with patch("django.utils.timezone.now", return_value=fake_now):
            self.api.force_authenticate(self.emp)
            res = self.api.get("/api/v1/mobile/employee/")
            self.assertEqual(res.status_code, 200)

            overview = res.data["attendance_overview"]
            self.assertEqual(overview["effective_start_date"], "2026-10-28")
            self.assertEqual(overview["all_months"]["months_count"], 2)
            self.assertEqual(overview["all_months"]["present"], 4)
            self.assertEqual(overview["all_months"]["full_shift"], 2)
            self.assertEqual(overview["all_months"]["short_shift"], 1)
            self.assertEqual(overview["all_months"]["active_shift"], 1)
            self.assertEqual(overview["all_months"]["absent"], 2)
            self.assertEqual(overview["all_months"]["holiday"], 1)
            self.assertEqual(overview["all_months"]["sunday"], 1)
            self.assertEqual(overview["all_months"]["leave"], 1)

            months_by_key = {m["month"]: m for m in overview["months"]}
            oct_m = months_by_key["2026-10"]
            self.assertEqual(oct_m["present"], 1)
            self.assertEqual(oct_m["absent"], 1)  # Days before Oct 28 join_date NOT marked absent!
            self.assertEqual(oct_m["holiday"], 1)
            self.assertEqual(oct_m["leave"], 1)
            self.assertEqual(oct_m["total_shift_formatted"], "8h 30m")
            self.assertEqual(oct_m["total_net_formatted"], "8h 0m")

            nov_m = months_by_key["2026-11"]
            self.assertEqual(nov_m["present"], 3)
            self.assertEqual(nov_m["absent"], 1)
            self.assertEqual(nov_m["sunday"], 1)

            # Verify Oct 28 record IN TIME = 09:30 AM, OUT TIME = 06:00 PM, TOTAL HOURS = 8h 30m (shift span)
            oct28 = next(r for r in overview["daily_records"] if r["date"] == "2026-10-28")
            self.assertEqual(oct28["in_time_formatted"], "09:30 AM")
            self.assertEqual(oct28["out_time_formatted"], "06:00 PM")
            self.assertEqual(oct28["total_hours_formatted"], "8h 30m")
            self.assertEqual(oct28["net_work_formatted"], "8h 0m")
            self.assertEqual(oct28["break_formatted"], "0h 30m")

            # Verify Today's active shift record
            today_shift = overview["today_shift"]
            self.assertEqual(today_shift["in_time_formatted"], "10:00 AM")
            self.assertEqual(today_shift["out_time_formatted"], "Active")
            self.assertEqual(today_shift["total_hours_formatted"], "5h 0m")

            # Dedicated month query endpoint works cleanly
            oct_only = self.api.get("/api/v1/mobile/employee/attendance-overview/?month=2026-10")
            self.assertEqual(oct_only.status_code, 200)
            self.assertTrue(all(r["month"] == "2026-10" for r in oct_only.data["daily_records"]))


class Feature3WorkReportReminderTests(TestCase):
    def setUp(self):
        self.tz = zoneinfo.ZoneInfo("Asia/Kolkata")
        self.expo_mock = patch("apps.notifications.team_events.expo_request").start()
        self.addCleanup(patch.stopall)
        self.expo_mock.return_value = {"data": []}

        self.api = APIClient()
        self.target_date = date(2026, 11, 4)  # Wednesday
        self.emp = User.objects.create_user(
            username="remind_745_emp",
            first_name="Ananya",
            last_name="Dev",
            role=User.Role.IT,
            is_active=True,
        )
        self.caller = User.objects.create_user(
            username="remind_745_caller",
            first_name="Rohan",
            last_name="Caller",
            role=User.Role.CALLER,
            is_active=True,
        )
        self.cat = ActivityCategory.objects.create(
            name="Development",
            slug="dev-test-745",
            is_active=True,
            is_default_general=True,
        )
        self.break_reason = BreakReasonConfig.objects.create(
            name="Lunch",
            code="LUNCH_745",
            warning_threshold_minutes=30,
            is_active=True,
        )

    def test_reminder_fires_at_7h45m_net_work_and_is_strictly_idempotent(self):
        self.assertEqual(WORK_REPORT_REMINDER_NET_SECONDS, 27900)  # 7 * 3600 + 45 * 60

        # Shift starts at 09:30 AM with a 30-minute lunch break (13:00 - 13:30)
        s_start = datetime(2026, 11, 4, 9, 30, tzinfo=self.tz)
        b_start = datetime(2026, 11, 4, 13, 0, tzinfo=self.tz)
        b_end = datetime(2026, 11, 4, 13, 30, tzinfo=self.tz)

        ws = WorkSession.objects.create(
            user=self.emp,
            date=self.target_date,
            status=WorkSession.Status.ACTIVE,
            started_at=s_start,
        )
        WorkActivitySegment.objects.create(session=ws, category=self.cat, started_at=s_start, ended_at=b_start)
        ActivityBreak.objects.create(
            session=ws,
            reason_config=self.break_reason,
            started_at=b_start,
            ended_at=b_end,
            closure_reason=ActivityBreak.ClosureReason.RESUMED,
            warning_threshold_minutes_snapshot=30,
        )
        WorkActivitySegment.objects.create(session=ws, category=self.cat, started_at=b_end)

        # 1. At 17:44 IST -> Gross = 8h 14m, Break = 30m -> Net = 7h 44m (27,840s) -> NO reminder
        at_7h44m = datetime(2026, 11, 4, 17, 44, tzinfo=self.tz)
        sent_early = check_and_send_work_report_reminder_for_user(self.emp, now=at_7h44m)
        self.assertFalse(sent_early)
        self.assertEqual(Notification.objects.filter(recipient=self.emp).count(), 0)

        # 2. At 17:45 IST -> Gross = 8h 15m, Break = 30m -> Net = 7h 45m (27,900s) -> SENDS reminder!
        at_7h45m = datetime(2026, 11, 4, 17, 45, tzinfo=self.tz)
        sent_exact = check_and_send_work_report_reminder_for_user(self.emp, now=at_7h45m)
        self.assertTrue(sent_exact)

        notifs = list(Notification.objects.filter(recipient=self.emp, type=Notification.Type.WORK_REPORT_REMINDER))
        self.assertEqual(len(notifs), 1)
        self.assertEqual(notifs[0].title, "Work Report Reminder")
        self.assertIn("7h 45m", notifs[0].body)
        self.assertEqual(notifs[0].data["reminder_type"], "WORK_REPORT")
        self.assertEqual(notifs[0].data["target_date"], "2026-11-04")
        self.assertEqual(notifs[0].data["net_work_seconds"], 27900)

        # 3. Repeated calls via helper, CurrentActivityView, and CLI command do NOT duplicate
        self.assertFalse(
            check_and_send_work_report_reminder_for_user(self.emp, now=at_7h45m + timedelta(minutes=5))
        )
        batch_summary = send_attendance_reminders(
            reminder_type=REMINDER_WORK_REPORT,
            now=at_7h45m + timedelta(minutes=10),
        )
        self.assertEqual(batch_summary["sent_count"], 0)
        self.assertEqual(batch_summary["skipped_already_notified"], 1)
        self.assertEqual(Notification.objects.filter(recipient=self.emp).count(), 1)

    def test_skips_employee_who_already_submitted_work_report_or_ended_shift(self):
        s_start = datetime(2026, 11, 4, 9, 30, tzinfo=self.tz)
        at_1730 = datetime(2026, 11, 4, 17, 30, tzinfo=self.tz)  # 8h 0m net

        # emp has active shift >= 7h45m BUT already submitted today's WorkReport
        ws1 = WorkSession.objects.create(
            user=self.emp,
            date=self.target_date,
            status=WorkSession.Status.ACTIVE,
            started_at=s_start,
        )
        WorkActivitySegment.objects.create(session=ws1, category=self.cat, started_at=s_start)
        WorkReport.objects.create(
            employee=self.emp,
            date=self.target_date,
            notes="Completed backend optimization tasks.",
        )

        # caller already ended their shift at 17:20
        ws2 = WorkSession.objects.create(
            user=self.caller,
            date=self.target_date,
            status=WorkSession.Status.COMPLETED,
            closure_reason=WorkSession.ClosureReason.MANUAL,
            started_at=s_start,
            ended_at=datetime(2026, 11, 4, 17, 20, tzinfo=self.tz),
        )
        WorkActivitySegment.objects.create(
            session=ws2,
            category=self.cat,
            started_at=s_start,
            ended_at=datetime(2026, 11, 4, 17, 20, tzinfo=self.tz),
        )

        summary = send_attendance_reminders(reminder_type=REMINDER_WORK_REPORT, now=at_1730)
        self.assertEqual(summary["sent_count"], 0)
        self.assertEqual(summary["skipped_report_submitted"], 1)
        self.assertEqual(summary["skipped_already_ended"], 1)
        self.assertEqual(Notification.objects.count(), 0)

        # Verify management command CLI supports --type WORK_REPORT
        call_command(
            "send_attendance_reminders",
            type="WORK_REPORT",
            dry_run=True,
            now_override="2026-11-04T17:30:00+05:30",
        )


class Feature4ResponsiveNoticesWebTests(TestCase):
    def test_notices_page_and_css_support_long_titles_and_multi_role_badges_on_mobile(self):
        admin = User.objects.create_user(
            username="notice_admin",
            role=User.Role.ADMIN,
            is_active=True,
        )
        emp = User.objects.create_user(
            username="notice_emp",
            role=User.Role.CALLER,
            is_active=True,
        )
        long_title = (
            "Urgent Updated Admission Verification & Holiday Shift Schedule Guidelines "
            "for All Calling, Counselling & Operations Teams — November 2026"
        )
        notice = Notice.objects.create(
            title=long_title,
            body="Please review the attached schedule before tomorrow morning.",
            roles=[
                User.Role.CALLER,
                User.Role.COUNSELOR,
                User.Role.IT,
                User.Role.VIDEO_EDITOR,
                User.Role.EMPLOYEE,
            ],
            created_by=admin,
        )

        from django.utils.html import escape

        client = Client()
        client.force_login(emp)
        res = client.get("/notices/")
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, escape(long_title))
        self.assertContains(res, notice.audience_label)

        css_path = Path(settings.BASE_DIR) / "apps" / "web" / "static" / "web" / "crm.css"
        css_text = css_path.read_text(encoding="utf-8")
        self.assertIn(".notice-title-group", css_text)
        self.assertIn("overflow-wrap: anywhere;", css_text)
        # Ensure .notice-audience-badge no longer forces white-space: nowrap overflow on mobile
        badge_block = css_text.split(".notice-audience-badge {")[1].split("}")[0]
        self.assertIn("white-space: normal;", badge_block)
        self.assertNotIn("white-space: nowrap;", badge_block)
