from datetime import timedelta
from django.test import Client, TestCase
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.utils import timezone
from apps.accounts.models import User
from apps.calls.models import Call
from apps.followups.models import FollowUp
from apps.leads.models import Lead, LeadAssignmentHistory


class WorkspaceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.manager = User.objects.create_user('manager', password='test-pass', role='MANAGER')
        cls.caller = User.objects.create_user('caller', password='test-pass', role='CALLER')
        cls.other = User.objects.create_user('other', password='test-pass', role='CALLER')
        cls.lead = Lead.objects.create(name='Visible lead', phone='9876543210', assigned_caller=cls.caller)
        cls.hidden = Lead.objects.create(name='Private lead', phone='9876543211', assigned_caller=cls.other)
        cls.followup = FollowUp.objects.create(lead=cls.hidden, caller=cls.other, scheduled_at=timezone.now())
        Call.objects.create(lead=cls.lead, caller=cls.caller, started_at=timezone.now(), outcome='INTERESTED')

    def test_login_required_and_role_access(self):
        self.assertRedirects(self.client.get('/'), '/login/?next=/', fetch_redirect_response=False)
        denied = User.objects.create_user('editor', password='test-pass', role='VIDEO_EDITOR')
        self.client.force_login(denied)
        self.assertRedirects(self.client.get('/'), '/employee/', fetch_redirect_response=False)
        self.assertEqual(self.client.get('/leads/').status_code, 403)

    def test_pages_render_for_management(self):
        self.client.force_login(self.manager)
        for path in ['/', '/?days=30', '/leads/', '/leads/new/', '/leads/import/', '/calls/', '/follow-ups/', '/team/', f'/leads/{self.lead.pk}/']:
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 200)

    def test_caller_isolation_in_stats_lists_and_direct_actions(self):
        self.client.force_login(self.caller)
        response = self.client.get('/')
        self.assertEqual(response.context['total'], 1)
        self.assertEqual(response.context['today_calls'], 1)
        self.assertNotContains(response, 'Private lead')
        self.assertNotContains(self.client.get('/leads/'), 'Private lead')
        self.assertEqual(self.client.get(f'/leads/{self.hidden.pk}/').status_code, 404)
        self.assertEqual(self.client.post(f'/leads/{self.hidden.pk}/', {'action': 'save'}).status_code, 404)
        self.assertEqual(self.client.post(f'/follow-ups/{self.followup.pk}/complete/').status_code, 404)
        for path in ['/team/', '/leads/import/', '/leads/new/']:
            self.assertEqual(self.client.get(path).status_code, 403)
        self.assertEqual(self.client.post('/leads/assign/', {'lead_ids': [self.hidden.pk], 'caller_id': self.caller.pk}).status_code, 403)

    def test_assignment_keeps_history_and_skips_existing_owner(self):
        self.client.force_login(self.manager)
        unassigned = Lead.objects.create(name='New lead', phone='9876543212')
        self.client.post('/leads/assign/', {'lead_ids': [unassigned.pk, self.hidden.pk], 'caller_id': self.caller.pk})
        unassigned.refresh_from_db()
        self.hidden.refresh_from_db()
        self.assertEqual(unassigned.assigned_caller_id, self.caller.pk)
        self.assertEqual(self.hidden.assigned_caller_id, self.other.pk)
        self.assertEqual(LeadAssignmentHistory.objects.filter(lead=unassigned).count(), 1)

    def test_followup_schedule_and_complete(self):
        self.client.force_login(self.caller)
        when = timezone.now() + timedelta(days=1)
        response = self.client.post(f'/leads/{self.lead.pk}/', {'action': 'followup', 'scheduled_at': when.isoformat(), 'notes': 'Call tomorrow'})
        self.assertEqual(response.status_code, 302)
        followup = FollowUp.objects.get(lead=self.lead)
        self.assertEqual(followup.caller_id, self.caller.pk)
        self.lead.refresh_from_db()
        self.assertEqual(self.lead.status, 'CALL_BACK')
        self.client.post(f'/follow-ups/{followup.pk}/complete/')
        followup.refresh_from_db()
        self.assertEqual(followup.status, 'COMPLETED')

    def test_import_preview_does_not_write_and_commit_deduplicates(self):
        self.client.force_login(self.manager)
        content = b'name,phone\nNew student,9876543299\nDuplicate,9876543210\n'
        before = Lead.objects.count()
        response = self.client.post('/leads/import/', {'action': 'preview', 'file': SimpleUploadedFile('leads.csv', content)})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Lead.objects.count(), before)
        self.client.post('/leads/import/', {'action': 'import', 'file': SimpleUploadedFile('leads.csv', content)})
        self.assertEqual(Lead.objects.count(), before + 1)

    def test_csrf_is_required_for_writes(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.manager)
        self.assertEqual(client.post('/leads/assign/', {'lead_ids': [self.lead.pk], 'caller_id': self.other.pk}).status_code, 403)

    def test_chart_handles_empty_data_and_invalid_period(self):
        empty = User.objects.create_user('empty', role='CALLER')
        self.client.force_login(empty)
        response = self.client.get('/?days=not-a-number')
        self.assertEqual(response.context['total'], 0)
        self.assertEqual(response.context['interest_rate'], 0)
        self.assertEqual(len(response.context['chart_data']['trend']), 7)

    def test_filter_pagination_and_escaping(self):
        self.client.force_login(self.manager)
        Lead.objects.create(name='<script>alert(1)</script>', phone='9876543298')
        response = self.client.get('/leads/?q=script')
        self.assertContains(response, '&lt;script&gt;')
        self.assertNotContains(response, '<script>alert(1)</script>')
        response = self.client.get('/leads/?q=Visible&page=invalid')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Visible lead')
        self.assertNotContains(response, 'Private lead')
