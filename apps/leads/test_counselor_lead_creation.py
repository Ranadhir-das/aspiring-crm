"""Comprehensive test suite for Counselor Lead Creation and Caller Assignment."""
from decimal import Decimal
from django.test import TestCase
from rest_framework.test import APIClient
from rest_framework import status

from apps.accounts.models import User
from apps.leads.models import Lead, LeadAssignmentHistory
from apps.leads.courses import Course


class CounselorLeadCreationTests(TestCase):
    def setUp(self):
        self.client = APIClient()

        # Counselor user
        self.counselor = User.objects.create_user(
            username='counselor_priya',
            password='Password123!',
            first_name='Priya',
            last_name='Sharma',
            role=User.Role.COUNSELOR,
            is_active=True,
            registration_pending=False,
        )

        # Callers
        self.caller1 = User.objects.create_user(
            username='caller_rahul',
            password='Password123!',
            first_name='Rahul',
            last_name='Verma',
            role=User.Role.CALLER,
            is_active=True,
            registration_pending=False,
            phone='9876500001',
        )

        self.caller2 = User.objects.create_user(
            username='caller_anita',
            password='Password123!',
            first_name='Anita',
            last_name='Patel',
            role=User.Role.CALLER,
            is_active=True,
            registration_pending=False,
            phone='9876500002',
        )

        # Inactive / Pending callers (should NOT be eligible)
        self.inactive_caller = User.objects.create_user(
            username='caller_inactive',
            password='Password123!',
            first_name='Inactive',
            last_name='User',
            role=User.Role.CALLER,
            is_active=False,
            registration_pending=False,
        )

        self.pending_caller = User.objects.create_user(
            username='caller_pending',
            password='Password123!',
            first_name='Pending',
            last_name='User',
            role=User.Role.CALLER,
            is_active=True,
            registration_pending=True,
        )

        # Admin user
        self.admin = User.objects.create_user(
            username='admin_boss',
            password='Password123!',
            first_name='Admin',
            last_name='Boss',
            role=User.Role.ADMIN,
            is_active=True,
            registration_pending=False,
        )

    def test_callers_list_as_counselor(self):
        self.client.force_authenticate(user=self.counselor)
        response = self.client.get('/api/v1/mobile/counselor/callers/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        callers = response.data['callers']

        caller_ids = [c['id'] for c in callers]
        self.assertIn(self.caller1.pk, caller_ids)
        self.assertIn(self.caller2.pk, caller_ids)
        self.assertNotIn(self.inactive_caller.pk, caller_ids)
        self.assertNotIn(self.pending_caller.pk, caller_ids)
        self.assertNotIn(self.counselor.pk, caller_ids)
        self.assertNotIn(self.admin.pk, caller_ids)

    def test_callers_list_forbidden_for_caller_role(self):
        self.client.force_authenticate(user=self.caller1)
        response = self.client.get('/api/v1/mobile/counselor/callers/')
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_callers_list_unauthenticated_rejected(self):
        response = self.client.get('/api/v1/mobile/counselor/callers/')
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_create_lead_with_minimal_fields(self):
        self.client.force_authenticate(user=self.counselor)
        payload = {
            'phone': '98765 43210',
            'assigned_caller_id': self.caller1.pk,
            # Name left blank: should default to "Student"
            'name': '',
        }
        response = self.client.post('/api/v1/mobile/counselor/leads/create/', data=payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertTrue(response.data['success'])

        lead_id = response.data['lead']['id']
        lead = Lead.objects.get(pk=lead_id)
        self.assertEqual(lead.name, 'Student')
        self.assertEqual(lead.phone, '9876543210')
        self.assertEqual(lead.assigned_caller, self.caller1)
        self.assertIsNotNone(lead.assigned_at)
        self.assertEqual(lead.status, Lead.Status.PENDING)

        # Check assignment history
        history = LeadAssignmentHistory.objects.filter(lead=lead).first()
        self.assertIsNotNone(history)
        self.assertIsNone(history.previous_caller)
        self.assertEqual(history.new_caller, self.caller1)
        self.assertEqual(history.assigned_by, self.counselor)

    def test_create_lead_with_full_crm_details(self):
        self.client.force_authenticate(user=self.counselor)
        payload = {
            'name': 'Aarav Gupta',
            'phone': '+91 9988776655',
            'email': 'aarav@example.com',
            'location': 'Mumbai',
            'college': 'St. Xavier',
            'neet_status': 'Qualified',
            'pcb_percentage': '88.5',
            'preferred_intake': '2027',
            'preferred_course': Course.MBBS,
            'source': 'Counselor Referral',
            'campaign': 'Spot Admission 2026',
            'notes': 'Interested in MBBS abroad. Parents keen on Russia or Uzbekistan.',
            'assigned_caller_id': self.caller2.pk,
        }
        response = self.client.post('/api/v1/mobile/counselor/leads/create/', data=payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        lead = Lead.objects.get(pk=response.data['lead']['id'])
        self.assertEqual(lead.name, 'Aarav Gupta')
        self.assertEqual(lead.phone, '919988776655')
        self.assertEqual(lead.email, 'aarav@example.com')
        self.assertEqual(lead.location, 'Mumbai')
        self.assertEqual(lead.college, 'St. Xavier')
        self.assertEqual(lead.neet_status, 'Qualified')
        self.assertEqual(lead.pcb_percentage, Decimal('88.50'))
        self.assertEqual(lead.preferred_intake, '2027')
        self.assertEqual(lead.preferred_course, Course.MBBS)
        self.assertEqual(lead.source, 'Counselor Referral')
        self.assertEqual(lead.campaign, 'Spot Admission 2026')
        self.assertEqual(lead.notes, 'Interested in MBBS abroad. Parents keen on Russia or Uzbekistan.')
        self.assertEqual(lead.assigned_caller, self.caller2)

    def test_duplicate_phone_rejected(self):
        # Create an existing lead
        Lead.objects.create(name='Original Student', phone='9876512345', assigned_caller=self.caller1)

        self.client.force_authenticate(user=self.counselor)
        payload = {
            'name': 'Duplicate Attempt',
            'phone': '+91-98765-12345',
            'assigned_caller_id': self.caller2.pk,
        }
        response = self.client.post('/api/v1/mobile/counselor/leads/create/', data=payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('phone', response.data)
        self.assertIn('already exists', str(response.data['phone']))

    def test_invalid_phone_rejected(self):
        self.client.force_authenticate(user=self.counselor)
        payload = {
            'name': 'Short Phone',
            'phone': '12345',
            'assigned_caller_id': self.caller1.pk,
        }
        response = self.client.post('/api/v1/mobile/counselor/leads/create/', data=payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('phone', response.data)

    def test_invalid_caller_rejected(self):
        self.client.force_authenticate(user=self.counselor)
        # Attempt to assign to non-existent ID
        response = self.client.post('/api/v1/mobile/counselor/leads/create/', data={
            'phone': '9876543210',
            'assigned_caller_id': 99999,
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

        # Attempt to assign to inactive caller
        response = self.client.post('/api/v1/mobile/counselor/leads/create/', data={
            'phone': '9876543210',
            'assigned_caller_id': self.inactive_caller.pk,
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

        # Attempt to assign to pending caller
        response = self.client.post('/api/v1/mobile/counselor/leads/create/', data={
            'phone': '9876543210',
            'assigned_caller_id': self.pending_caller.pk,
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

        # Attempt to assign to non-caller role (admin)
        response = self.client.post('/api/v1/mobile/counselor/leads/create/', data={
            'phone': '9876543210',
            'assigned_caller_id': self.admin.pk,
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_caller_isolation_enforced(self):
        # Counselor creates a lead assigned to caller1
        self.client.force_authenticate(user=self.counselor)
        response = self.client.post('/api/v1/mobile/counselor/leads/create/', data={
            'name': 'Rohan Shah',
            'phone': '9876599999',
            'assigned_caller_id': self.caller1.pk,
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        lead_id = response.data['lead']['id']

        # Caller 1 logs in and checks their lead list
        self.client.force_authenticate(user=self.caller1)
        list_response = self.client.get('/api/v1/mobile/leads/')
        self.assertEqual(list_response.status_code, status.HTTP_200_OK)
        assigned_ids = [l['id'] for l in list_response.data]
        self.assertIn(lead_id, assigned_ids)

        # Caller 1 views lead details
        detail_response = self.client.get(f'/api/v1/mobile/leads/{lead_id}/')
        self.assertEqual(detail_response.status_code, status.HTTP_200_OK)
        self.assertEqual(detail_response.data['name'], 'Rohan Shah')

        # Caller 2 logs in and checks their lead list
        self.client.force_authenticate(user=self.caller2)
        caller2_list = self.client.get('/api/v1/mobile/leads/')
        self.assertEqual(caller2_list.status_code, status.HTTP_200_OK)
        caller2_assigned_ids = [l['id'] for l in caller2_list.data]
        self.assertNotIn(lead_id, caller2_assigned_ids)

        # Caller 2 attempts to directly access Caller 1's lead details -> MUST be 404
        forbidden_detail = self.client.get(f'/api/v1/mobile/leads/{lead_id}/')
        self.assertEqual(forbidden_detail.status_code, status.HTTP_404_NOT_FOUND)
