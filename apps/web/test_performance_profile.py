from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import CallerSession, User
from apps.calls.models import Call
from apps.leads.models import Admission, Lead, LeadImportBatch


class PerformanceProfileTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user('profile-admin', role='ADMIN')
        self.caller = User.objects.create_user('profile-caller', role='CALLER')
        self.other = User.objects.create_user('profile-other', role='CALLER')
        self.batch = LeadImportBatch.objects.create(filename='September.csv')
        self.lead = Lead.objects.create(name='Batch lead', phone='123', import_batch=self.batch,
                                        assigned_caller=self.caller)
        self.client.force_login(self.admin)

    def test_profile_totals_and_batches_keep_call_ownership(self):
        now = timezone.now()
        Call.objects.create(caller=self.caller, lead=self.lead, started_at=now, duration_seconds=125)
        Call.objects.create(caller=self.other, lead=self.lead, started_at=now, duration_seconds=900)
        session = CallerSession.objects.create(caller=self.caller, active_seconds=60)
        CallerSession.objects.filter(pk=session.pk).update(logged_in_at=now - timedelta(minutes=5), last_seen=now)
        response = self.client.get(reverse('web:caller-detail', args=[self.caller.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['total_duration'], 125)
        self.assertEqual(response.context['login_seconds'], 300)
        self.assertEqual(response.context['app_active_seconds'], 60)
        self.assertEqual(sum(p['count'] for p in response.context['profile_chart']['trend']), 1)
        self.assertContains(response, 'September.csv')
        self.assertContains(response, 'Batch lead')
        self.assertEqual(response.context['stats']['total_points'], 0)
        self.assertContains(response, 'Points ledger')
        self.assertContains(response, '<h2>Activity</h2>', html=True)

    def test_all_statuses_and_empty_profile(self):
        response = self.client.get(reverse('web:performance'))
        self.assertEqual(len(response.context['status_bars']), len(Lead.Status.choices))
        self.assertEqual(sum(row['value'] for row in response.context['status_bars']), 1)
        self.assertContains(response, 'Leads by status')
        response = self.client.get(reverse('web:caller-detail', args=[self.other.pk]))
        self.assertContains(response, 'No leads assigned.')
        self.assertEqual(response.context['total_duration'], 0)

    def test_batch_filter_is_scoped_to_visible_leads(self):
        private = LeadImportBatch.objects.create(filename='Private.csv')
        Lead.objects.create(name='Private lead', phone='456', import_batch=private, assigned_caller=self.other)
        self.client.force_login(self.caller)
        response = self.client.get(reverse('web:leads'), {'batch': self.batch.pk})
        self.assertContains(response, 'Batch lead')
        self.assertNotContains(response, 'Private.csv')
        response = self.client.get(reverse('web:leads'), {'batch': private.pk})
        self.assertNotContains(response, 'Private lead')
        self.assertEqual(self.client.get(reverse('web:caller-detail', args=[self.other.pk])).status_code, 403)

    def test_batches_remain_grouped_across_pages(self):
        Lead.objects.bulk_create([Lead(name=f'Lead {i:02}', phone=str(i), import_batch=self.batch) for i in range(25)])
        for page in (1, 2):
            response = self.client.get(reverse('web:leads'), {'batch': self.batch.pk, 'page': page})
            self.assertContains(response, 'batch-heading')
            self.assertTrue(all(lead.import_batch_id == self.batch.pk for lead in response.context['records']))

    def test_leaderboard_columns_and_caller_profile_metrics(self):
        # Caller has 1 pending lead (left to call)
        response = self.client.get(reverse('web:performance'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Left to call')
        self.assertNotContains(response, '<th>Today / week / month</th>')
        self.assertNotContains(response, '<th>Counselling</th>')
        self.assertNotContains(response, '<th>Applications</th>')
        self.assertNotContains(response, '<th>Admissions</th>')
        # Check caller row has left_to_call = 1
        caller_row = next(r for r in response.context['rows'] if r['caller'].pk == self.caller.pk)
        self.assertEqual(caller_row['left_to_call'], 1)
        self.assertEqual(caller_row['leads_assigned'], 1)

        # Check caller detail profile
        profile_res = self.client.get(reverse('web:caller-detail', args=[self.caller.pk]))
        self.assertEqual(profile_res.status_code, 200)
        self.assertEqual(profile_res.context['left_to_call'], 1)
        self.assertContains(profile_res, 'Counselling Sessions')
        self.assertContains(profile_res, 'Student Applications')
        self.assertContains(profile_res, 'Admissions Completed')
        self.assertContains(profile_res, "Today's Points")
        self.assertContains(profile_res, "This Week's Points")
        self.assertContains(profile_res, "This Month's Points")

    def test_clickable_status_links_and_exact_lead_counts(self):
        # Create additional leads with different statuses
        Lead.objects.create(name='Interested Lead', phone='991', status=Lead.Status.INTERESTED, assigned_caller=self.caller)
        Lead.objects.create(name='Called Lead', phone='992', status=Lead.Status.CALLED, assigned_caller=self.caller)
        Lead.objects.create(name='Other Pending Lead', phone='993', status=Lead.Status.PENDING, assigned_caller=self.other)

        # 1. Caller profile links to leads filtered by status and caller
        profile_url = reverse('web:caller-detail', args=[self.caller.pk])
        res = self.client.get(profile_url)
        self.assertEqual(res.status_code, 200)
        # Check that status chips are clickable links with status and owner parameters
        expected_pending_link = f"{reverse('web:leads')}?status=PENDING&owner={self.caller.pk}"
        expected_interested_link = f"{reverse('web:leads')}?status=INTERESTED&owner={self.caller.pk}"
        self.assertContains(res, expected_pending_link)
        self.assertContains(res, expected_interested_link)

        # 2. Performance overview status chips link to leads filtered by status
        perf_res = self.client.get(reverse('web:performance'))
        self.assertEqual(perf_res.status_code, 200)
        self.assertContains(perf_res, f"{reverse('web:leads')}?status=PENDING")
        self.assertContains(perf_res, f"{reverse('web:leads')}?status=INTERESTED")

        # 3. Leads view shows exact count and status pills with accurate counts
        leads_res = self.client.get(reverse('web:leads'), {'status': 'PENDING', 'owner': self.caller.pk})
        self.assertEqual(leads_res.status_code, 200)
        self.assertEqual(leads_res.context['records'].paginator.count, 1)
        self.assertEqual(leads_res.context['total_matching_leads'], 3)
        self.assertEqual(leads_res.context['selected_status'], 'PENDING')
        self.assertContains(leads_res, '<strong>1</strong> lead found')
        self.assertContains(leads_res, 'Batch lead')
        self.assertNotContains(leads_res, 'Other Pending Lead')
        self.assertNotContains(leads_res, 'Interested Lead')

        # Check total leads without owner filter
        all_pending_res = self.client.get(reverse('web:leads'), {'status': 'PENDING'})
        self.assertEqual(all_pending_res.status_code, 200)
        self.assertEqual(all_pending_res.context['records'].paginator.count, 2)
        self.assertContains(all_pending_res, '<strong>2</strong> leads found')

    def test_call_outcomes_and_conversions_metrics(self):
        now = timezone.now()
        # Create calls with distinct outcomes
        Call.objects.create(caller=self.caller, lead=self.lead, started_at=now, duration_seconds=60, outcome=Call.Outcome.INTERESTED)
        Call.objects.create(caller=self.caller, lead=self.lead, started_at=now, duration_seconds=45, outcome=Call.Outcome.NOT_INTERESTED)
        Call.objects.create(caller=self.other, lead=self.lead, started_at=now, duration_seconds=30, outcome=Call.Outcome.INTERESTED)

        # Create an admission
        Admission.objects.create(lead=self.lead, caller=self.caller, college='Harvard Medical School')

        # Check performance page shows accurate admissions and outcomes
        perf_res = self.client.get(reverse('web:performance'))
        self.assertEqual(perf_res.status_code, 200)
        self.assertEqual(perf_res.context['team_stats']['conversions'], 1)
        self.assertEqual(perf_res.context['team_stats']['interested'], 2)
        self.assertEqual(perf_res.context['team_stats']['not_interested'], 1)
        self.assertContains(perf_res, '1 admissions')

        # Check caller detail profile links call outcomes to /calls/
        profile_res = self.client.get(reverse('web:caller-detail', args=[self.caller.pk]))
        self.assertEqual(profile_res.status_code, 200)
        expected_call_link = f"{reverse('web:calls')}?outcome=INTERESTED&caller={self.caller.pk}"
        self.assertContains(profile_res, expected_call_link)

        # Check calls page outcome filtering and counts
        calls_res = self.client.get(reverse('web:calls'), {'outcome': 'INTERESTED', 'caller': self.caller.pk})
        self.assertEqual(calls_res.status_code, 200)
        self.assertEqual(calls_res.context['records'].paginator.count, 1)
        self.assertContains(calls_res, '<strong>1</strong> call found')

    def test_admission_updates_lead_status(self):
        new_lead = Lead.objects.create(name='Admission Candidate', phone='88888', status=Lead.Status.INTERESTED, assigned_caller=self.caller)
        res = self.client.post(reverse('web:admission-new'), {
            'candidate_type': Admission.CandidateType.LEAD,
            'lead': new_lead.pk,
            'caller': self.caller.pk,
            'college': 'Oxford University',
            'course': 'Biochemistry',
            'admission_date': timezone.localdate().isoformat(),
        })
        self.assertEqual(res.status_code, 302)
        new_lead.refresh_from_db()
        self.assertEqual(new_lead.status, Lead.Status.ADMISSION_DONE)



