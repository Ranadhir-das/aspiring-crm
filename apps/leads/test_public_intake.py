from io import BytesIO
from unittest.mock import patch
from uuid import uuid4
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest import skipUnless

from django.core.cache.backends.locmem import LocMemCache
from django.core.files.base import ContentFile
from django.db import connection, connections
from django.test import TestCase, TransactionTestCase, override_settings
from openpyxl import Workbook
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.leads.api.public_views import PublicLeadBurstThrottle, PublicLeadDailyThrottle
from apps.leads.models import Lead, LeadAssignmentHistory, LeadImportBatch, Service, WebsiteSource
from apps.leads.services import bulk_assign_leads, commit_import
from apps.leads.public_intake import create_website_lead


class PublicLeadTests(TestCase):
    url = '/api/v1/public/leads/'

    def setUp(self):
        self.api = APIClient()
        self.source = WebsiteSource.objects.create(name='Landing', code='landing-page', default_service=Service.objects.get(code='MBBS'))
        self.source.allowed_services.set(Service.objects.all())
        self.api.credentials(HTTP_X_API_KEY=self.source.api_key)
        cache = LocMemCache(f'public-leads-{uuid4()}', {})
        self.addCleanup(cache.clear)
        for cls in (PublicLeadBurstThrottle, PublicLeadDailyThrottle):
            patched = patch.object(cls, 'cache', cache)
            patched.start()
            self.addCleanup(patched.stop)
        self.data = {'name': ' Website visitor ', 'phone': '+91 (98765) 43210',
                     'email': 'visitor@example.com', 'service': 'MBBS', 'source': 'landing-page',
                     'campaign': 'September', 'location': 'Kolkata', 'notes': 'Please contact me.',
                     'pcb_percentage': '85.50'}

    def submit(self, **changes):
        return self.api.post(self.url, {**self.data, **changes}, format='json')

    def test_create_normalized_unassigned_website_lead(self):
        response = self.submit()
        self.assertEqual(response.status_code, 202, response.data)
        lead = Lead.objects.get()
        self.assertEqual(lead.name, 'Website visitor')
        self.assertEqual(lead.phone, '919876543210')
        for field in ['email', 'service', 'source', 'campaign', 'location', 'notes']:
            self.assertEqual(getattr(lead, field), self.data[field])
        self.assertEqual(str(lead.pcb_percentage), '85.50')
        self.assertEqual(lead.status, Lead.Status.PENDING)
        self.assertEqual(lead.service_type.code, 'MBBS')
        self.assertIsNone(lead.availability.claimed_at)
        self.assertIsNone(lead.assigned_caller)
        self.assertIsNone(lead.assigned_at)
        self.assertIsNone(lead.import_batch)
        self.assertFalse(LeadAssignmentHistory.objects.exists())
        self.assertFalse(LeadImportBatch.objects.exists())
        self.assertEqual(set(response.data), {'detail', 'lead_id', 'is_duplicate'})

    def test_minimal_fields_and_default_source(self):
        response = self.api.post(self.url, {'name': 'Visitor', 'phone': '9876543210'}, format='json')
        self.assertEqual(response.status_code, 202)
        lead = Lead.objects.get()
        self.assertEqual(lead.source, 'landing-page')
        self.assertEqual(lead.service, 'MBBS')
        self.assertEqual(lead.service_type.code, 'MBBS')

    def test_duplicate_does_not_disclose_or_overwrite_existing_lead(self):
        original = Lead.objects.create(name='Private name', phone='+91-98765-43210',
                                       notes='Private note', status=Lead.Status.INTERESTED, service='Existing')
        response = self.submit()
        self.assertEqual(response.status_code, 202)
        self.assertEqual(Lead.objects.count(), 1)
        original.refresh_from_db()
        self.assertEqual(original.notes, 'Private note')
        self.assertEqual(original.service, 'Existing')
        fresh = self.submit(phone='9123456789')
        self.assertTrue(response.data['is_duplicate'])
        self.assertFalse(fresh.data['is_duplicate'])
        self.assertNotIn('notes', response.data)

    def test_repeated_submission_creates_only_one_lead(self):
        self.submit()
        self.submit(phone='91 98765 43210')
        self.assertEqual(Lead.objects.count(), 1)

    def test_invalid_phones(self):
        for phone in ['', '123', '1234567890123456', 'abc9876543210', '1111111111']:
            with self.subTest(phone=phone):
                self.assertEqual(self.submit(phone=phone).status_code, 400)
        self.assertFalse(Lead.objects.exists())

    def test_invalid_fields(self):
        for changes in [{'name': ' '}, {'email': 'bad'}, {'pcb_percentage': '101'},
                        {'notes': 'x' * 2001}, {'service': 'x' * 101}]:
            with self.subTest(changes=changes):
                self.assertEqual(self.submit(**changes).status_code, 400)
        self.assertFalse(Lead.objects.exists())

    def test_internal_fields_cannot_be_written(self):
        for field in ['assigned_caller', 'status', 'import_batch', 'id', 'created_at']:
            with self.subTest(field=field):
                self.assertEqual(self.submit(**{field: 1}).status_code, 400)
        self.assertFalse(Lead.objects.exists())

    def test_honeypot_does_not_store_spam(self):
        response = self.submit(website='https://spam.example')
        self.assertEqual(response.status_code, 202)
        self.assertFalse(Lead.objects.exists())

    def test_json_only_size_limit_and_malformed_json(self):
        self.assertEqual(self.api.post(self.url, 'x' * 16385, content_type='application/json').status_code, 413)
        self.assertEqual(self.api.post(self.url, '{', content_type='application/json').status_code, 400)
        self.assertEqual(self.api.post(self.url, [], format='json').status_code, 400)
        self.assertEqual(self.api.post(self.url, self.data, format='multipart').status_code, 415)
        self.assertFalse(Lead.objects.exists())

    def test_no_read_update_delete_or_crm_access(self):
        for method in ['get', 'patch', 'put', 'delete']:
            self.assertEqual(getattr(self.api, method)(self.url).status_code, 405)
        self.assertIn(self.api.get('/api/v1/leads/').status_code, [401, 403])
        self.assertIn(self.api.post('/api/v1/leads/bulk-assign/', {}, format='json').status_code, [401, 403])

    @override_settings(PUBLIC_LEAD_BURST_RATE='2/min')
    def test_throttle_cannot_be_bypassed_with_forwarded_header(self):
        for ip in ['1.1.1.1', '2.2.2.2']:
            self.assertEqual(self.api.post(self.url, self.data, format='json', HTTP_X_FORWARDED_FOR=ip).status_code, 202)
        response = self.api.post(self.url, self.data, format='json', HTTP_X_FORWARDED_FOR='3.3.3.3')
        self.assertEqual(response.status_code, 429)
        self.assertIn('Retry-After', response)

    @override_settings(PUBLIC_LEAD_DAILY_RATE='1/day')
    def test_daily_limit(self):
        self.assertEqual(self.submit().status_code, 202)
        self.assertEqual(self.submit().status_code, 429)

    def test_csv_xlsx_import_and_manual_assignment_still_work(self):
        admin = User.objects.create_user('website-regression-admin', role='ADMIN')
        caller = User.objects.create_user('website-regression-caller', role='CALLER')
        self.submit()
        csv = ContentFile(b'name,phone\nDuplicate,+91-98765-43210\nBatch person,09876543211\n', name='leads.csv')
        result = commit_import(csv, admin)
        self.assertEqual(result['created_count'], 1)
        self.assertEqual(result['duplicate_count'], 1)
        batch_lead = Lead.objects.get(import_batch_id=result['batch_id'])
        self.assertEqual(batch_lead.phone, '09876543211')
        self.assertEqual(batch_lead.service, '')
        workbook = Workbook()
        workbook.active.append(['name', 'phone'])
        workbook.active.append(['XLSX person', '09876543212'])
        stream = BytesIO()
        workbook.save(stream)
        imported = commit_import(ContentFile(stream.getvalue(), name='leads.xlsx'), admin)
        self.assertEqual(imported['created_count'], 1)
        result = bulk_assign_leads([batch_lead.pk], caller, admin)
        self.assertEqual(result['assigned_count'], 1)
        self.assertIsNone(Lead.objects.get(phone='919876543210').assigned_caller)


@skipUnless(connection.vendor == 'postgresql', 'Production website locking uses PostgreSQL')
class ConcurrentPublicLeadTests(TransactionTestCase):
    def setUp(self):
        Service.objects.get_or_create(code='OTHER', defaults={'name': 'OTHER'})

    def test_simultaneous_same_phone_submissions_create_one_lead(self):
        barrier = Barrier(2)

        def submit():
            try:
                barrier.wait(timeout=10)
                create_website_lead({'name': 'Concurrent visitor', 'phone': '9876543210'})
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(submit) for _ in range(2)]
            for future in futures:
                future.result(timeout=20)
        self.assertEqual(Lead.objects.count(), 1)
