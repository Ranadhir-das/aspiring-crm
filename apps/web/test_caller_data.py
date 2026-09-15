from django.test import TestCase
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone
from rest_framework.test import APIClient
from apps.accounts.models import User
from apps.calls.models import Call
from apps.leads.models import Lead, LeadAssignmentHistory

class CallerDataTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user('admin', role='ADMIN')
        self.a = User.objects.create_user('a', role='CALLER')
        self.b = User.objects.create_user('b', role='CALLER')
        self.lead = Lead.objects.create(name='Existing', phone='9876543210', assigned_caller=self.b)
        self.ca = Call.objects.create(lead=self.lead, caller=self.a, started_at=timezone.now(), outcome='INTERESTED')
        self.cb = Call.objects.create(lead=self.lead, caller=self.b, started_at=timezone.now(), outcome='BUSY')
        self.api = APIClient()

    def upload(self):
        return SimpleUploadedFile('leads.csv', b'name,phone\nExisting,9876543210\nNew,9876543211\n')

    def test_history_is_private_even_after_reassignment(self):
        self.api.force_authenticate(self.a)
        response = self.api.get('/api/v1/calls/mine/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual([r['id'] for r in response.data], [self.ca.pk])
        self.api.force_authenticate(self.b)
        self.assertEqual([r['id'] for r in self.api.get(f'/api/v1/calls/lead/{self.lead.pk}/').data], [self.cb.pk])
        new = User.objects.create_user('new', role='CALLER')
        self.api.force_authenticate(new)
        self.assertEqual(self.api.get('/api/v1/calls/mine/').data, [])
        self.api.force_authenticate(None)
        self.assertEqual(self.api.get('/api/v1/calls/mine/').status_code, 401)

    def test_import_assigns_new_only_with_audit_and_mobile_visibility(self):
        self.client.force_login(self.admin)
        response = self.client.post('/leads/import/', {'file': self.upload(), 'action': 'import', 'caller': self.a.pk})
        self.assertEqual(response.status_code, 302)
        new = Lead.objects.get(phone='9876543211')
        self.assertEqual(new.assigned_caller_id, self.a.pk)
        self.assertIsNotNone(new.assigned_at)
        self.assertTrue(LeadAssignmentHistory.objects.filter(lead=new, new_caller=self.a, assigned_by=self.admin).exists())
        self.lead.refresh_from_db()
        self.assertEqual(self.lead.assigned_caller_id, self.b.pk)
        self.api.force_authenticate(self.a)
        self.assertEqual([r['id'] for r in self.api.get('/api/v1/mobile/leads/').data], [new.pk])

    def test_invalid_assignees_preview_and_permissions(self):
        self.client.force_login(self.admin)
        self.b.is_active = False
        self.b.save()
        for assignee in [self.b, self.admin]:
            response = self.client.post('/leads/import/', {'file': self.upload(), 'action': 'import', 'caller': assignee.pk})
            self.assertIn('caller', response.context['form'].errors)
        self.client.post('/leads/import/', {'file': self.upload(), 'action': 'preview', 'caller': self.a.pk})
        self.assertFalse(Lead.objects.filter(phone='9876543211').exists())
        self.client.force_login(self.a)
        self.assertEqual(self.client.post('/leads/import/', {'file': self.upload(), 'action': 'import', 'caller': self.a.pk}).status_code, 403)
