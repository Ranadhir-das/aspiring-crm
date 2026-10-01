from io import BytesIO
from unittest.mock import patch
from uuid import uuid4

from django.contrib.admin.sites import AdminSite
from django.core.cache.backends.locmem import LocMemCache
from django.core.files.base import ContentFile
from django.test import TestCase, override_settings
from django.utils import timezone
from openpyxl import Workbook
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.leads.admin import LeadAdmin, WebsiteLeadSubmissionAdmin, WebsiteSourceAdmin
from apps.leads.api.public_views import PublicLeadBurstThrottle, PublicLeadDailyThrottle
from apps.leads.claiming import claim_website_lead
from apps.leads.models import (
    Lead,
    LeadAvailability,
    LeadImportBatch,
    Service,
    WebsiteLeadSubmission,
    WebsiteSource,
)
from apps.leads.services import bulk_assign_leads, commit_import


class WebsiteIntegrationTests(TestCase):
    url = '/api/v1/public/leads/'
    available_url = '/api/v1/mobile/leads/available/'

    def setUp(self):
        self.api = APIClient()
        cache = LocMemCache(f'website-integration-{uuid4()}', {})
        self.addCleanup(cache.clear)
        for cls in (PublicLeadBurstThrottle, PublicLeadDailyThrottle):
            patched = patch.object(cls, 'cache', cache)
            patched.start()
            self.addCleanup(patched.stop)

        # Base services
        self.service_mbbs, _ = Service.objects.get_or_create(
            code='MBBS', defaults={'name': 'Bachelor of Medicine'}
        )
        self.service_bds, _ = Service.objects.get_or_create(
            code='BDS', defaults={'name': 'Dental Surgery'}
        )
        self.service_other, _ = Service.objects.get_or_create(
            code='OTHER', defaults={'name': 'Other'}
        )

        # Configured website source
        self.source = WebsiteSource.objects.create(
            name='Official Portal',
            code='official_web',
            api_key='ws_test_secret_key_123',
            is_active=True,
        )
        self.source.default_service = self.service_mbbs
        self.source.save(update_fields=['default_service'])
        self.source.allowed_services.set(Service.objects.all())

    def submit_with_key(self, data, key=None, header=True, auth_header=False):
        key = key if key is not None else self.source.api_key
        headers = {}
        payload = dict(data)
        if key:
            if header:
                headers['HTTP_X_API_KEY'] = key
            elif auth_header:
                headers['HTTP_AUTHORIZATION'] = f'Api-Key {key}'
            else:
                payload['api_key'] = key
        return self.api.post(self.url, payload, format='json', **headers)

    def test_website_source_auto_generates_api_key_and_cleans_code(self):
        ws = WebsiteSource.objects.create(name='New Portal', code=' NEW_PORTAL ')
        ws.full_clean()
        self.assertEqual(ws.code, 'NEW_PORTAL')
        self.assertTrue(ws.api_key.startswith('ws_'))
        self.assertGreaterEqual(len(ws.api_key), 20)

    def test_authenticated_submission_creates_lead_and_submission_record(self):
        payload = {
            'name': 'Priya Sharma',
            'phone': '+91 98765 11111',
            'email': 'priya@example.com',
            'service': 'MBBS',
            'campaign': 'autumn-campaign',
            'location': 'Delhi',
            'notes': 'Interested in admissions guidance',
        }
        response = self.submit_with_key(payload)
        self.assertEqual(response.status_code, 202, response.data)
        self.assertFalse(response.data['is_duplicate'])
        lead_id = response.data['lead_id']
        self.assertIsNotNone(lead_id)

        lead = Lead.objects.get(pk=lead_id)
        self.assertEqual(lead.name, 'Priya Sharma')
        self.assertEqual(lead.phone, '919876511111')
        self.assertEqual(lead.source, 'official_web')
        self.assertEqual(lead.campaign, 'autumn-campaign')
        self.assertEqual(lead.service_type, self.service_mbbs)

        # Audit submission record
        submission = WebsiteLeadSubmission.objects.get(lead=lead)
        self.assertFalse(submission.is_duplicate)
        self.assertEqual(submission.website_source, self.source)
        self.assertEqual(submission.campaign, 'autumn-campaign')
        self.assertEqual(submission.service_type, self.service_mbbs)

    def test_submission_with_authorization_header_and_body_key(self):
        # Test Authorization: Api-Key <key>
        resp1 = self.submit_with_key(
            {'name': 'Auth Header User', 'phone': '9876522221', 'service': 'MBBS'},
            auth_header=True, header=False,
        )
        self.assertEqual(resp1.status_code, 202)
        self.assertFalse(resp1.data['is_duplicate'])

        # Test body api_key
        resp2 = self.submit_with_key(
            {'name': 'Body Key User', 'phone': '9876522222', 'service': 'MBBS'},
            header=False, auth_header=False,
        )
        self.assertEqual(resp2.status_code, 202)
        self.assertFalse(resp2.data['is_duplicate'])

    def test_invalid_and_inactive_api_keys(self):
        # Invalid key -> 401
        resp = self.submit_with_key(
            {'name': 'Hacker', 'phone': '9876533331'}, key='ws_wrong_key'
        )
        self.assertEqual(resp.status_code, 401)
        self.assertIn('Invalid API key', str(resp.data))

        # Inactive source -> 403
        self.source.is_active = False
        self.source.save()
        resp2 = self.submit_with_key(
            {'name': 'Inactive User', 'phone': '9876533332'}
        )
        self.assertEqual(resp2.status_code, 403)
        self.assertIn('inactive', str(resp2.data))

    def test_allowed_services_restriction(self):
        # Restrict source to MBBS only
        self.source.allowed_services.set([self.service_mbbs])

        # Allowed service succeeds
        ok_resp = self.submit_with_key(
            {'name': 'MBBS Student', 'phone': '9876544441', 'service': 'MBBS'}
        )
        self.assertEqual(ok_resp.status_code, 202)

        # Disallowed service rejected with 400
        bad_resp = self.submit_with_key(
            {'name': 'BDS Student', 'phone': '9876544442', 'service': 'BDS'}
        )
        self.assertEqual(bad_resp.status_code, 400)
        self.assertIn('not permitted', str(bad_resp.data))

    def test_duplicate_lead_does_not_create_lead_and_records_submission(self):
        # First submission
        resp1 = self.submit_with_key({
            'name': 'Original Enquiry',
            'phone': '+91-98765-55555',
            'service': 'MBBS',
            'campaign': 'campaign-1',
        })
        self.assertEqual(resp1.status_code, 202)
        self.assertFalse(resp1.data['is_duplicate'])
        lead_id = resp1.data['lead_id']
        self.assertEqual(Lead.objects.count(), 1)

        # Duplicate submission with different campaign and notes
        resp2 = self.submit_with_key({
            'name': 'Original Enquiry Repeat',
            'phone': '91 98765 55555',
            'service': 'MBBS',
            'campaign': 'campaign-2',
            'notes': 'Follow up again please',
        })
        self.assertEqual(resp2.status_code, 202)
        self.assertTrue(resp2.data['is_duplicate'])
        self.assertEqual(resp2.data['lead_id'], lead_id)
        self.assertEqual(resp2.data['detail'], 'Enquiry received for existing lead.')

        # Ensure no second Lead was created
        self.assertEqual(Lead.objects.count(), 1)

        # Both submissions recorded
        submissions = WebsiteLeadSubmission.objects.filter(lead_id=lead_id).order_by('submitted_at')
        self.assertEqual(submissions.count(), 2)
        self.assertFalse(submissions[0].is_duplicate)
        self.assertEqual(submissions[0].campaign, 'campaign-1')
        self.assertTrue(submissions[1].is_duplicate)
        self.assertEqual(submissions[1].campaign, 'campaign-2')
        self.assertEqual(submissions[1].notes, 'Follow up again please')

    def test_website_submission_automatically_enters_service_caller_queue(self):
        caller_mbbs = User.objects.create_user('caller-mbbs', role='CALLER')
        caller_bds = User.objects.create_user('caller-bds', role='CALLER')
        caller_mbbs.services.add(self.service_mbbs)
        caller_bds.services.add(self.service_bds)

        # Submit an MBBS lead
        resp = self.submit_with_key({
            'name': 'MBBS Aspirant',
            'phone': '9876566661',
            'service': 'MBBS',
        })
        self.assertEqual(resp.status_code, 202)
        lead_id = resp.data['lead_id']

        # Caller mapped to MBBS sees the lead in fresh queue
        self.api.force_authenticate(caller_mbbs)
        available = self.api.get(self.available_url).data
        self.assertIn(lead_id, [item['id'] for item in available])

        # Caller mapped to BDS does NOT see the lead
        self.api.force_authenticate(caller_bds)
        available_bds = self.api.get(self.available_url).data
        self.assertNotIn(lead_id, [item['id'] for item in available_bds])

        # Caller mapped to MBBS can claim it
        self.api.force_authenticate(caller_mbbs)
        claim_resp = self.api.post(f'/api/v1/mobile/leads/{lead_id}/claim/')
        self.assertEqual(claim_resp.status_code, 200)

        # After claim, queue is empty
        self.assertEqual(self.api.get(self.available_url).data, [])

    def test_spoofing_configured_source_without_key_is_rejected(self):
        # Attempt to claim source 'official_web' without supplying the API key
        resp = self.api.post(
            self.url,
            {'name': 'Spoofer', 'phone': '9876577771', 'source': 'official_web', 'service': 'MBBS'},
            format='json',
        )
        self.assertEqual(resp.status_code, 401)
        self.assertIn('API key is required', str(resp.data))

    @override_settings(WEBSITE_LEAD_REQUIRE_API_KEY=True)
    def test_global_require_api_key_setting_enforcement(self):
        # Missing key returns 401 when setting is True
        resp = self.api.post(
            self.url,
            {'name': 'Anonymous', 'phone': '9876588881', 'service': 'MBBS'},
            format='json',
        )
        self.assertEqual(resp.status_code, 401)

        # Supplying valid key succeeds
        ok_resp = self.submit_with_key({
            'name': 'Valid User', 'phone': '9876588881', 'service': 'MBBS'
        })
        self.assertEqual(ok_resp.status_code, 202)

    def test_honeypot_silently_drops_spam_without_storing(self):
        resp = self.submit_with_key({
            'name': 'Bot',
            'phone': '9876599991',
            'website': 'https://spam-link.example',
        })
        self.assertEqual(resp.status_code, 202)
        self.assertFalse(Lead.objects.exists())
        self.assertFalse(WebsiteLeadSubmission.objects.exists())

    def test_admin_visibility_and_routing_status_methods(self):
        site = AdminSite()
        lead_admin = LeadAdmin(Lead, site)
        source_admin = WebsiteSourceAdmin(WebsiteSource, site)
        sub_admin = WebsiteLeadSubmissionAdmin(WebsiteLeadSubmission, site)

        # Masked key
        self.assertEqual(source_admin.api_key_masked(self.source), 'ws_tes..._123')
        self.assertIn('MBBS', source_admin.allowed_services_display(self.source))

        caller = User.objects.create_user('routing-caller', role='CALLER')
        caller.services.add(self.service_mbbs)

        # Submit lead
        resp = self.submit_with_key({
            'name': 'Routing Test', 'phone': '9876500001', 'service': 'MBBS', 'campaign': 'autumn'
        })
        lead = Lead.objects.get(pk=resp.data['lead_id'])
        self.assertEqual(lead.routing_status, 'Available (MBBS)')
        self.assertEqual(lead_admin.routing_status_display(lead), 'Available (MBBS)')

        submission = WebsiteLeadSubmission.objects.get(lead=lead)
        self.assertEqual(sub_admin.source_name(submission), 'Official Portal')
        self.assertEqual(sub_admin.service_code(submission), 'MBBS')
        self.assertEqual(sub_admin.routing_status(submission), 'Available (MBBS)')
        self.assertIn('Routing Test', sub_admin.lead_link(submission))

        # Claim lead -> updates routing status
        claim_website_lead(lead.pk, caller)
        lead.refresh_from_db()
        self.assertIn('Claimed by', lead.routing_status)

    def test_csv_xlsx_and_manual_assignment_regression(self):
        admin = User.objects.create_user('admin-p5-regression', role='ADMIN')
        caller = User.objects.create_user('caller-p5-regression', role='CALLER')

        # CSV upload
        csv = ContentFile(b'name,phone\nBatch Lead,9876500099\n', name='leads.csv')
        res = commit_import(csv, admin)
        self.assertEqual(res['created_count'], 1)
        lead = Lead.objects.get(import_batch_id=res['batch_id'])
        self.assertEqual(lead.routing_status, 'Manual / Batch')

        # XLSX upload
        wb = Workbook()
        wb.active.append(['name', 'phone'])
        wb.active.append(['XLSX Lead', '9876500098'])
        stream = BytesIO()
        wb.save(stream)
        res_xlsx = commit_import(ContentFile(stream.getvalue(), name='leads.xlsx'), admin)
        self.assertEqual(res_xlsx['created_count'], 1)

        # Manual bulk assign
        assign_res = bulk_assign_leads([lead.pk], caller, admin)
        self.assertEqual(assign_res['assigned_count'], 1)
        lead.refresh_from_db()
        self.assertIn('Assigned to', lead.routing_status)
