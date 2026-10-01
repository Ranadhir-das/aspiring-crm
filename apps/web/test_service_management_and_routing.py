from datetime import timedelta
from django.test import TestCase, Client
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.calls.models import Call
from apps.followups.models import FollowUp
from apps.leads.models import Lead, Service, LeadAvailability
from apps.leads.public_intake import create_website_lead


class ServiceManagementAndRoutingWebTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.manager = User.objects.create_user(
            username='mgr_routing',
            password='password123',
            role=User.Role.MANAGER,
            is_active=True,
        )
        self.caller1 = User.objects.create_user(
            username='caller_one',
            password='password123',
            role=User.Role.CALLER,
            designation='Counselor Level 1',
            is_active=True,
        )
        self.caller2 = User.objects.create_user(
            username='caller_two',
            password='password123',
            role=User.Role.CALLER,
            designation='Counselor Level 2',
            is_active=True,
        )

        self.service_mbbs = Service.objects.get(code='MBBS')
        self.service_mba = Service.objects.get(code='MBA')
        self.service_apostille = Service.objects.get(code='APOSTILLE')
        self.service_other = Service.objects.get(code='OTHER')

        self.caller1.services.add(self.service_mbbs)
        self.caller2.services.add(self.service_mbbs, self.service_mba)

    def test_caller_cannot_access_management_views(self):
        self.client.force_login(self.caller1)
        for url_name in ['web:services', 'web:service-create', 'web:caller-services', 'web:lead-routing']:
            response = self.client.get(reverse(url_name))
            self.assertEqual(response.status_code, 403)

    def test_services_list_and_create_and_edit_and_toggle(self):
        self.client.force_login(self.manager)

        # 1. Services list view
        resp = self.client.get(reverse('web:services'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'MBBS')
        self.assertContains(resp, 'MBA')
        self.assertContains(resp, 'APOSTILLE')
        self.assertContains(resp, 'OTHER')

        # 2. Create service
        resp = self.client.post(
            reverse('web:service-create'),
            {
                'name': 'Ayush & Dental',
                'code': 'AYUSH',
                'description': 'Ayush and dental admissions',
                'is_active': 'on',
            },
        )
        self.assertEqual(resp.status_code, 302)
        new_svc = Service.objects.get(code='AYUSH')
        self.assertEqual(new_svc.name, 'Ayush & Dental')
        self.assertTrue(new_svc.is_active)

        # 3. Edit service
        resp = self.client.post(
            reverse('web:service-edit', args=[new_svc.pk]),
            {
                'name': 'Ayush & Dental Studies',
                'code': 'AYUSH',
                'description': 'Updated description',
                'is_active': 'on',
            },
        )
        self.assertEqual(resp.status_code, 302)
        new_svc.refresh_from_db()
        self.assertEqual(new_svc.name, 'Ayush & Dental Studies')

        # 4. Toggle active status
        resp = self.client.post(reverse('web:service-toggle-active', args=[new_svc.pk]))
        self.assertEqual(resp.status_code, 302)
        new_svc.refresh_from_db()
        self.assertFalse(new_svc.is_active)

    def test_caller_service_assignment_and_eligibility(self):
        self.client.force_login(self.manager)

        # 1. Caller list view
        resp = self.client.get(reverse('web:caller-services'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'caller_one')
        self.assertContains(resp, 'Counselor Level 1')
        self.assertContains(resp, 'MBBS')

        # 2. Edit caller services and designation
        resp = self.client.post(
            reverse('web:caller-service-edit', args=[self.caller1.pk]),
            {
                'designation': 'Senior Medical Counselor',
                'is_active': 'on',
                'services': [self.service_mbbs.pk, self.service_apostille.pk],
            },
        )
        self.assertEqual(resp.status_code, 302)
        self.caller1.refresh_from_db()
        self.assertEqual(self.caller1.designation, 'Senior Medical Counselor')
        self.assertEqual(
            set(self.caller1.services.values_list('code', flat=True)),
            {'MBBS', 'APOSTILLE'},
        )

        # 3. Toggle eligibility
        resp = self.client.post(
            reverse('web:caller-eligibility-toggle', args=[self.caller1.pk])
        )
        self.assertEqual(resp.status_code, 302)
        self.caller1.refresh_from_db()
        self.assertFalse(self.caller1.is_active)

    def test_lead_routing_view_and_availability_diagnostics(self):
        self.client.force_login(self.manager)

        # Create website leads in different states:
        # A) Available lead (MBBS with active caller2)
        lead_available, _, _ = create_website_lead(
            {'name': 'Available Lead', 'phone': '9876543201', 'service': 'MBBS'}
        )

        # B) Claimed lead
        lead_claimed, _, _ = create_website_lead(
            {'name': 'Claimed Lead', 'phone': '9876543202', 'service': 'MBBS'}
        )
        from apps.leads.claiming import claim_website_lead

        claim_website_lead(lead_claimed.pk, self.caller2)

        # C) Completed lead (outcome submitted)
        lead_completed, _, _ = create_website_lead(
            {'name': 'Completed Lead', 'phone': '9876543203', 'service': 'MBA'}
        )
        claim_website_lead(lead_completed.pk, self.caller2)
        lead_completed.status = Lead.Status.INTERESTED
        lead_completed.save()

        # D) Follow-up pending
        lead_callback, _, _ = create_website_lead(
            {'name': 'Callback Lead', 'phone': '9876543204', 'service': 'MBA'}
        )
        claim_website_lead(lead_callback.pk, self.caller2)
        lead_callback.status = Lead.Status.CALL_BACK
        lead_callback.save()
        FollowUp.objects.create(
            lead=lead_callback,
            caller=self.caller2,
            scheduled_at=timezone.now() + timedelta(days=1),
            status='PENDING',
        )

        # E) Inactive service
        svc_inactive = Service.objects.create(
            name='Inactive Cert', code='INACT', is_active=False
        )
        lead_inact = Lead.objects.create(
            name='Inactive Svc Lead',
            phone='9876543205',
            service_type=svc_inactive,
            source='website',
        )
        LeadAvailability.objects.create(lead=lead_inact)

        # F) No eligible caller (APOSTILLE has no active callers currently)
        self.caller1.services.clear()
        lead_no_caller, _, _ = create_website_lead(
            {'name': 'No Caller Lead', 'phone': '9876543206', 'service': 'APOSTILLE'}
        )

        # Test Main Routing View
        resp = self.client.get(reverse('web:lead-routing'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Available Lead')
        self.assertContains(resp, 'Claimed Lead')
        self.assertContains(resp, 'Completed Lead')
        self.assertContains(resp, 'Callback Lead')
        self.assertContains(resp, 'No Caller Lead')

        # Test No Eligible Caller Tab
        resp = self.client.get(reverse('web:lead-routing') + '?tab=no_caller')
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'No Caller Lead')
        self.assertNotContains(resp, 'Available Lead')

        # Test Workload Tab
        resp = self.client.get(reverse('web:lead-routing') + '?tab=workload')
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'caller_two')
        self.assertContains(resp, 'Caller workload &amp; queue capacity')

        # Test Filters
        resp = self.client.get(reverse('web:lead-routing') + '?service=APOSTILLE')
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'No Caller Lead')
        self.assertNotContains(resp, 'Available Lead')

    def test_admin_service_and_routing_apis(self):
        from rest_framework.test import APIClient
        api = APIClient()
        api.force_authenticate(self.manager)

        # 1. GET /api/v1/leads/services/
        resp = api.get('/api/v1/leads/services/')
        self.assertEqual(resp.status_code, 200)
        codes = [item['code'] for item in resp.data]
        self.assertIn('MBBS', codes)

        # 2. POST /api/v1/leads/services/
        resp = api.post(
            '/api/v1/leads/services/',
            {'name': 'Nursing', 'code': 'NURSING', 'description': 'B.Sc Nursing'},
            format='json',
        )
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.data['code'], 'NURSING')

        # 3. GET /api/v1/leads/callers/services/
        resp = api.get('/api/v1/leads/callers/services/')
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(any(c['username'] == 'caller_two' for c in resp.data))

        # 4. POST /api/v1/leads/callers/<id>/services/
        resp = api.post(
            f'/api/v1/leads/callers/{self.caller2.pk}/services/',
            {
                'designation': 'Senior Lead Specialist',
                'is_active': True,
                'services': ['MBBS', 'NURSING'],
            },
            format='json',
        )
        self.assertEqual(resp.status_code, 200)
        self.caller2.refresh_from_db()
        self.assertEqual(self.caller2.designation, 'Senior Lead Specialist')
        self.assertEqual(
            set(self.caller2.services.values_list('code', flat=True)),
            {'MBBS', 'NURSING'},
        )

        # 5. GET /api/v1/leads/routing/
        resp = api.get('/api/v1/leads/routing/')
        self.assertEqual(resp.status_code, 200)
        self.assertIn('results', resp.data)
