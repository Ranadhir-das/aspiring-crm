import json
from datetime import timedelta

from django.core.files.base import ContentFile
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.calls.models import Call, CallRecording
from apps.followups.models import FollowUp
from apps.leads.models import Counselling, Lead
from apps.leads.scoring import refresh_lead_score
from apps.web.models import Attendance, Project, WorkReport


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
        self.assertContains(resp, 'emp-360-dialog')
        # Sidebar link appears under Overview
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

    def test_non_management_employee_sees_only_self(self):
        self.client.force_login(self.caller)
        resp = self.client.get(reverse('web:employee-reports'))
        self.assertEqual(resp.status_code, 200)
        payload = resp.context['report_payload']
        emp_usernames = {e['username'] for e in payload['employees']}
        self.assertEqual(emp_usernames, {'caller_rep_test'})
