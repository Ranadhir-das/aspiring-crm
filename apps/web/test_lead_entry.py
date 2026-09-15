from django.test import TestCase
from django.core.files.base import ContentFile
from apps.accounts.models import User
from apps.leads.models import Lead, LeadImportBatch
from apps.leads.services import commit_import, preview_import

class LeadEntryTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user('admin', role='ADMIN')
        self.a = User.objects.create_user('a', role='CALLER')
        self.b = User.objects.create_user('b', role='CALLER')
        self.client.force_login(self.admin)

    def test_calley_headers_batch_and_duplicates(self):
        data = b'Name,Mobile\nAlice,09876543210\nBob,9876543211\nDuplicate,09876543210\n'
        preview = preview_import(ContentFile(data, name='calley.csv'))
        self.assertTrue(preview['success'])
        self.assertEqual(preview['duplicate_count'], 1)
        result = commit_import(ContentFile(data, name='calley.csv'), self.admin)
        self.assertEqual(result['created_count'], 2)
        self.assertEqual(Lead.objects.filter(import_batch_id=result['batch_id']).count(), 2)
        self.assertTrue(Lead.objects.filter(phone='09876543210').exists())

    def test_quick_download_import_validation_and_access(self):
        data = {'form-TOTAL_FORMS': '1', 'form-INITIAL_FORMS': '0', 'form-0-name': 'Alice', 'form-0-phone': '09876543210', 'action': 'download'}
        response = self.client.post('/leads/quick-entry/', data)
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'Alice,09876543210', response.content)
        self.assertFalse(Lead.objects.exists())
        data['action'] = 'import'
        response = self.client.post('/leads/quick-entry/', data)
        self.assertEqual(response.status_code, 302)
        self.assertIn('/leads/distribute/?batch=', response.url)
        self.assertEqual(Lead.objects.count(), 1)
        data['form-0-phone'] = 'bad'
        self.assertContains(self.client.post('/leads/quick-entry/', data), 'valid phone number')
        self.client.force_login(self.a)
        self.assertEqual(self.client.get('/leads/quick-entry/').status_code, 403)

    def test_quantities_apply_only_to_selected_batch(self):
        batch = LeadImportBatch.objects.create(filename='batch.csv', imported_by=self.admin)
        other = Lead.objects.create(name='Other batch', phone='9876543218')
        for n in range(3):
            Lead.objects.create(name=f'Lead {n}', phone=f'987654321{n}', import_batch=batch)
        data = {'batch': batch.pk, f'caller_{self.a.pk}': 2, f'caller_{self.b.pk}': 1, 'action': 'preview'}
        self.assertContains(self.client.post('/leads/distribute/', data), 'Allocation preview')
        self.assertFalse(Lead.objects.filter(assigned_caller__isnull=False).exists())
        data['action'] = 'assign'
        self.assertEqual(self.client.post('/leads/distribute/', data).status_code, 302)
        self.assertEqual(Lead.objects.filter(assigned_caller=self.a).count(), 2)
        self.assertEqual(Lead.objects.filter(assigned_caller=self.b).count(), 1)
        other.refresh_from_db()
        self.assertIsNone(other.assigned_caller)

    def test_remaining_counts_and_partial_assignment_return_to_batch(self):
        batch = LeadImportBatch.objects.create(filename='Partial.csv', imported_by=self.admin)
        for n in range(5):
            Lead.objects.create(name=f'Person {n}', phone=f'887654321{n}', import_batch=batch)
        response = self.client.get(f'/leads/distribute/?batch={batch.pk}')
        self.assertEqual(response.context['total_leads'], 5)
        self.assertEqual(response.context['unassigned'], 5)
        data = {'batch': batch.pk, f'caller_{self.a.pk}': 2, f'caller_{self.b.pk}': 0, 'action': 'assign'}
        response = self.client.post('/leads/distribute/', data, follow=True)
        self.assertEqual(response.context['assigned'], 2)
        self.assertEqual(response.context['unassigned'], 3)
        self.assertEqual(response.context['available'], 3)
        data[f'caller_{self.b.pk}'] = 2
        response = self.client.post('/leads/distribute/', data)
        self.assertContains(response, 'Only 3 matching leads')
        self.assertEqual(Lead.objects.filter(import_batch=batch, assigned_caller__isnull=True).count(), 3)
        overview = self.client.get('/leads/import/')
        row = next(b for b in overview.context['batches'] if b.pk == batch.pk)
        self.assertEqual(row.remaining, 3)
        self.assertEqual(row.allocated, 2)
