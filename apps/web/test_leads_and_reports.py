import json
from datetime import timedelta

from django.core.files.base import ContentFile
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.activity.models import ActivityCategory, BreakReasonConfig, WorkActivitySegment, WorkSession, ActivityBreak
from apps.calls.models import Call, CallRecording
from apps.followups.models import FollowUp
from apps.leads.models import Counselling, Lead
from apps.leads.scoring import refresh_lead_score
from apps.performance.models import PeerAppreciation, PointsEntry
from apps.web.models import Attendance, Project, ProjectWorkReport, WorkReport


class UpgradedLeadsDirectoryTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            username='admin_leads_test',
            password='Password123!',
            role=User.Role.ADMIN,
            first_name='Admin',
            last_name='Boss',
        )
        self.caller_a = User.objects.create_user(
            username='caller_a_test',
            password='Password123!',
            role=User.Role.CALLER,
            first_name='Priya',
            last_name='Verma',
        )
        self.caller_b = User.objects.create_user(
            username='caller_b_test',
            password='Password123!',
            role=User.Role.CALLER,
            first_name='Aman',
            last_name='Khan',
        )

        # Hot lead assigned to caller_a
        self.hot_lead = Lead.objects.create(
            name='Hot Candidate',
            phone='9999000001',
            status=Lead.Status.INTERESTED,
            assigned_caller=self.caller_a,
            preferred_course='MBBS',
            location='Delhi',
            neet_status='560 marks',
            preferred_intake='2026',
        )
        c1 = Call.objects.create(
            lead=self.hot_lead,
            caller=self.caller_a,
            phone_number=self.hot_lead.phone,
            direction=Call.Direction.INBOUND,
            started_at=timezone.now() - timedelta(hours=1),
            duration_seconds=110,
            outcome=Call.Outcome.INTERESTED,
        )
        CallRecording.objects.create(
            call=c1,
            file=ContentFile(b'RIFF0000WAVEfmt ', name='hot.wav'),
            duration_seconds=110,
            file_size=22000,
            sha256='1' * 64,
            transcript_status=CallRecording.TranscriptStatus.COMPLETED,
            ai_summary='Candidate asked about MBBS fees and disclosed 25 lakh budget for 2026.',
            ai_key_points=['Asked about fees', 'Budget 25 lakh'],
            ai_objections=[{'category': 'HOSTEL', 'label': 'Hostel query', 'detail': 'Asked about veg mess'}],
            ai_sentiment=CallRecording.Sentiment.POSITIVE,
        )
        Counselling.objects.create(lead=self.hot_lead, caller=self.caller_a, course='MBBS')
        refresh_lead_score(self.hot_lead, save=True)

        # Inactive lead assigned to caller_b
        self.cold_lead = Lead.objects.create(
            name='Inactive Candidate',
            phone='9999000002',
            status=Lead.Status.PENDING,
            assigned_caller=self.caller_b,
        )
        refresh_lead_score(self.cold_lead, save=True)

    def test_leads_page_displays_scores_tiers_filters_and_sorting(self):
        self.client.force_login(self.admin)
        resp = self.client.get(reverse('web:leads'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'How the 100-point score builds')
        self.assertContains(resp, 'Hot Candidate')
        self.assertContains(resp, 'Inactive Candidate')
        self.assertContains(resp, 'lead-scores-data')

        # Filter by HOT temperature
        resp_hot = self.client.get(reverse('web:leads') + '?temperature=HOT')
        self.assertEqual(resp_hot.status_code, 200)
        self.assertContains(resp_hot, 'Hot Candidate')
        self.assertNotContains(resp_hot, 'Inactive Candidate')

        # Filter by analyzed recordings & objections
        resp_obj = self.client.get(reverse('web:leads') + '?has_recording=objections')
        self.assertEqual(resp_obj.status_code, 200)
        self.assertContains(resp_obj, 'Hot Candidate')
        self.assertNotContains(resp_obj, 'Inactive Candidate')

        # Sort by AI score descending
        resp_sort = self.client.get(reverse('web:leads') + '?sort=score_desc')
        self.assertEqual(resp_sort.status_code, 200)
        records = list(resp_sort.context['records'])
        self.assertEqual(records[0].pk, self.hot_lead.pk)

    def test_caller_isolation_and_recalculate_endpoints(self):
        self.client.force_login(self.caller_a)
        resp = self.client.get(reverse('web:leads'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Hot Candidate')
        self.assertNotContains(resp, 'Inactive Candidate')

        # Own lead recalculate returns JSON 200
        res_own = self.client.post(reverse('web:lead-recalculate-score', args=[self.hot_lead.pk]))
        self.assertEqual(res_own.status_code, 200)
        payload = res_own.json()
        self.assertGreaterEqual(payload['score'], 80)
        self.assertEqual(payload['temperature'], Lead.Temperature.HOT)

        # Other caller's lead returns 404
        res_other = self.client.post(reverse('web:lead-recalculate-score', args=[self.cold_lead.pk]))
        self.assertEqual(res_other.status_code, 404)

        # Recalculate all is management only
        res_all_denied = self.client.post(reverse('web:leads-recalculate-all'))
        self.assertEqual(res_all_denied.status_code, 403)

    def test_lead_detail_shows_score_card_and_exports_include_score(self):
        self.client.force_login(self.admin)
        detail = self.client.get(reverse('web:lead-detail', args=[self.hot_lead.pk]))
        self.assertEqual(detail.status_code, 200)
        self.assertContains(detail, 'How the 100-point score builds')
        self.assertContains(detail, 'Asked about fees')
        # Top executive snapshot fields
        self.assertContains(detail, 'AI Call Brief')
        self.assertContains(detail, 'Key Points &amp; Objections')
        self.assertContains(detail, 'Candidate asked about MBBS fees')
        self.assertContains(detail, 'Hostel query')
        self.assertContains(detail, 'ld-tablist')
        self.assertEqual(detail.context['initial_tab'], 'overview')
        self.assertEqual(detail.context['lead_snapshot']['total_calls'], 1)
        self.assertEqual(detail.context['lead_snapshot']['inbound_calls'], 1)
        self.assertEqual(detail.context['lead_snapshot']['objections_count'], 1)

        # Category filter activates timeline tab directly
        detail_cat = self.client.get(reverse('web:lead-detail', args=[self.hot_lead.pk]) + '?category=CALL')
        self.assertEqual(detail_cat.status_code, 200)
        self.assertEqual(detail_cat.context['initial_tab'], 'timeline')

        csv_resp = self.client.get(reverse('web:leads') + '?export=csv')
        self.assertEqual(csv_resp.status_code, 200)
        csv_text = b''.join(csv_resp.streaming_content).decode('utf-8-sig')
        self.assertIn('AI Score (0-100)', csv_text)
        self.assertIn('Lead Temperature', csv_text)
        self.assertIn('Hot', csv_text)


class VisualEmployeeReportsTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            username='admin_rep_test',
            password='Password123!',
            role=User.Role.ADMIN,
            first_name='Super',
            last_name='Admin',
        )
        self.caller = User.objects.create_user(
            username='caller_rep_test',
            password='Password123!',
            role=User.Role.CALLER,
            first_name='Neha',
            last_name='Singh',
        )
        self.staff = User.objects.create_user(
            username='staff_rep_test',
            password='Password123!',
            role=User.Role.IT,
            first_name='Rohan',
            last_name='Dev',
        )

        lead = Lead.objects.create(
            name='Report Lead',
            phone='9888000011',
            status=Lead.Status.INTERESTED,
            assigned_caller=self.caller,
        )
        refresh_lead_score(lead, save=True)
        Call.objects.create(
            lead=lead,
            caller=self.caller,
            phone_number=lead.phone,
            started_at=timezone.now() - timedelta(hours=2),
            duration_seconds=65,
            outcome=Call.Outcome.INTERESTED,
        )
        FollowUp.objects.create(
            lead=lead,
            caller=self.caller,
            scheduled_at=timezone.now() + timedelta(hours=3),
            status=FollowUp.Status.COMPLETED,
        )
        Attendance.objects.create(
            employee=self.caller,
            date=timezone.localdate(),
            status='PRESENT',
            checked_in=timezone.now() - timedelta(hours=8),
            checked_out=timezone.now(),
        )
        WorkReport.objects.create(employee=self.staff, date=timezone.localdate(), notes='Completed UI sprint')
        Project.objects.create(title='CRM Upgrade', employee=self.staff, status=Project.Status.COMPLETED)

    def test_management_sees_all_employees_and_visual_payload(self):
        self.client.force_login(self.admin)
        resp = self.client.get(reverse('web:employee-reports'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Employee Performance Reports')
        self.assertContains(resp, 'chart-team-temps')
        self.assertContains(resp, 'chart-daily-trend')
        self.assertContains(resp, 'er-employee-grid')
        self.assertContains(resp, reverse('web:employee-reports'))

        payload = resp.context['report_payload']
        emp_usernames = {e['username'] for e in payload['employees']}
        self.assertIn('caller_rep_test', emp_usernames)
        self.assertIn('staff_rep_test', emp_usernames)

        caller_row = next(e for e in payload['employees'] if e['username'] == 'caller_rep_test')
        self.assertEqual(caller_row['calls']['total'], 1)
        self.assertEqual(caller_row['followups']['completed'], 1)
        self.assertGreater(caller_row['attendance_pct'], 0)

        # Role filtering works
        resp_callers = self.client.get(reverse('web:employee-reports') + '?role=callers&range=7d')
        self.assertEqual(resp_callers.status_code, 200)
        filtered_usernames = {e['username'] for e in resp_callers.context['report_payload']['employees']}
        self.assertEqual(filtered_usernames, {'caller_rep_test'})

    def test_selected_employee_360_caller_and_live_calls_filter_api(self):
        # Add a yesterday call and a points entry for the caller
        lead = Lead.objects.filter(assigned_caller=self.caller).first()
        Call.objects.create(
            lead=lead,
            caller=self.caller,
            phone_number=lead.phone,
            direction=Call.Direction.INBOUND,
            started_at=timezone.now() - timedelta(days=1),
            duration_seconds=45,
            outcome=Call.Outcome.CALL_BACK,
        )
        PointsEntry.objects.create(
            caller=self.caller,
            lead=lead,
            event=PointsEntry.Event.INTERESTED_LEAD,
            points=10,
            reason='Interested MBBS lead verified',
            event_key='test-pts-caller-1',
        )

        self.client.force_login(self.admin)
        resp = self.client.get(reverse('web:employee-reports') + f'?employee={self.caller.pk}&range=7d')
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'er-detail-tablist')
        self.assertContains(resp, 'er-calls-toolbar')
        self.assertContains(resp, 'Interested MBBS lead verified')
        detail = resp.context['employee_detail']
        self.assertEqual(detail['employee']['default_tab'], 'calls')
        self.assertTrue(detail['employee']['sections']['show_calls'])
        self.assertTrue(detail['employee']['sections']['show_leads'])
        self.assertFalse(detail['employee']['sections']['show_projects'])
        self.assertEqual(detail['employee']['glance_tile_count'], 5)
        self.assertContains(resp, 'data-cols="5"')
        self.assertNotContains(resp, 'id="er-tab-projects"')
        self.assertEqual(detail['calls']['summary']['total'], 2)
        expected_pts = sum(PointsEntry.objects.filter(caller=self.caller).values_list('points', flat=True))
        self.assertGreaterEqual(expected_pts, 10)
        self.assertEqual(detail['points']['total'], expected_pts)

        # Live Calls AJAX API: today vs yesterday vs 7d vs direction filter
        api_url = reverse('web:employee-reports-calls-api', args=[self.caller.pk])
        res_today = self.client.get(api_url + '?range=today')
        self.assertEqual(res_today.status_code, 200)
        d_today = res_today.json()
        self.assertTrue(d_today['single_day'])
        self.assertEqual(len(d_today['trend']['labels']), 24)
        self.assertEqual(d_today['summary']['total'], 1)

        res_yday = self.client.get(api_url + '?range=yesterday')
        self.assertEqual(res_yday.status_code, 200)
        d_yday = res_yday.json()
        self.assertEqual(d_yday['summary']['total'], 1)
        self.assertEqual(d_yday['summary']['inbound'], 1)

        res_in = self.client.get(api_url + '?range=7d&direction=INBOUND')
        self.assertEqual(res_in.status_code, 200)
        self.assertEqual(res_in.json()['summary']['total'], 1)

    def test_selected_employee_360_it_projects_work_reports_and_workforce(self):
        video_editor = User.objects.create_user(
            username='veditor_rep_test',
            password='Password123!',
            role=User.Role.VIDEO_EDITOR,
            first_name='Kabir',
            last_name='Edit',
        )
        proj = Project.objects.create(title='MBBS Promo Reel', employee=video_editor, status=Project.Status.IN_PROGRESS)
        wr = WorkReport.objects.create(employee=video_editor, date=timezone.localdate(), notes='Edited reel v2')
        ProjectWorkReport.objects.create(
            project=proj,
            employee=video_editor,
            daily_report=wr,
            work_date=timezone.localdate(),
            duration_minutes=120,
            description='Color grading and captions',
        )
        cat = ActivityCategory.objects.create(name='Video Editing', slug='video-editing', color_hex='#8b5cf6')
        brk_cfg = BreakReasonConfig.objects.create(name='Lunch', code='LUNCH', warning_threshold_minutes=45)
        ws = WorkSession.objects.create(
            user=video_editor,
            date=timezone.localdate(),
            status=WorkSession.Status.COMPLETED,
            started_at=timezone.now() - timedelta(hours=8),
            ended_at=timezone.now(),
            closure_reason=WorkSession.ClosureReason.MANUAL,
        )
        WorkActivitySegment.objects.create(
            session=ws,
            category=cat,
            started_at=timezone.now() - timedelta(hours=7),
            ended_at=timezone.now() - timedelta(hours=1),
        )
        ActivityBreak.objects.create(
            session=ws,
            reason_config=brk_cfg,
            started_at=timezone.now() - timedelta(hours=4),
            ended_at=timezone.now() - timedelta(hours=3, minutes=30),
            warning_threshold_minutes_snapshot=45,
            closure_reason=ActivityBreak.ClosureReason.RESUMED,
        )

        self.client.force_login(self.admin)
        resp = self.client.get(reverse('web:employee-reports') + f'?employee={video_editor.pk}&range=7d')
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'MBBS Promo Reel')
        self.assertContains(resp, 'Color grading and captions')
        self.assertContains(resp, 'Video Editing')
        self.assertContains(resp, 'data-cols="4"')
        # Strict role fairness: Video Editor / IT must NOT see Calls, Leads, or Sales Outcomes!
        self.assertNotContains(resp, 'id="er-tab-calls"')
        self.assertNotContains(resp, 'er-calls-toolbar')
        self.assertNotContains(resp, 'Calls &amp; Connect')
        self.assertNotContains(resp, 'Leads &amp; Follow-ups')
        self.assertNotContains(resp, 'Assigned Lead Temperatures')
        self.assertNotContains(resp, 'id="er-tab-points"')
        detail = resp.context['employee_detail']
        self.assertEqual(detail['employee']['default_tab'], 'projects')
        self.assertFalse(detail['employee']['sections']['show_calls'])
        self.assertFalse(detail['employee']['sections']['show_leads'])
        self.assertFalse(detail['employee']['sections']['show_sales_outcomes'])
        self.assertTrue(detail['employee']['sections']['show_projects'])
        self.assertEqual(detail['employee']['glance_tile_count'], 4)
        self.assertEqual(detail['projects']['total'], 1)
        self.assertEqual(detail['projects']['work_reports_count'], 1)
        self.assertEqual(len(detail['attendance']['activity_categories']), 1)

        # IT employee with PeerAppreciation gets clean "Points & Recognition" tab (still NO Leads/Calls)
        PeerAppreciation.objects.create(
            reviewer=self.admin,
            employee=self.staff,
            month=timezone.localdate().strftime('%Y-%m'),
            score=9,
        )
        resp_it = self.client.get(reverse('web:employee-reports') + f'?employee={self.staff.pk}&range=7d')
        self.assertEqual(resp_it.status_code, 200)
        self.assertContains(resp_it, 'Points &amp; Recognition')
        self.assertContains(resp_it, 'Peer Appreciation Reviews')
        self.assertNotContains(resp_it, 'id="er-tab-calls"')
        self.assertNotContains(resp_it, 'Assigned Lead Temperatures')
        self.assertNotContains(resp_it, 'Calls &amp; Connect')
        self.assertNotContains(resp_it, 'Leads &amp; Follow-ups')

    def test_non_management_employee_sees_only_self_and_cannot_query_others_calls(self):
        self.client.force_login(self.caller)
        resp = self.client.get(reverse('web:employee-reports'))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context['selected_employee'].pk, self.caller.pk)
        payload = resp.context['report_payload']
        emp_usernames = {e['username'] for e in payload['employees']}
        self.assertEqual(emp_usernames, {'caller_rep_test'})

        # Own calls API works
        res_own = self.client.get(reverse('web:employee-reports-calls-api', args=[self.caller.pk]) + '?range=today')
        self.assertEqual(res_own.status_code, 200)

        # Another employee's calls API is blocked (403)
        res_other = self.client.get(reverse('web:employee-reports-calls-api', args=[self.staff.pk]) + '?range=today')
        self.assertEqual(res_other.status_code, 403)
