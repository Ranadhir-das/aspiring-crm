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

        self.counselor2 = User.objects.create_user(
            username='counselor_amit',
            password='Password123!',
            first_name='Amit',
            last_name='Gupta',
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

    def test_counselor_lists_only_own_created_leads(self):
        # Counselor 1 creates a lead
        self.client.force_authenticate(user=self.counselor)
        r1 = self.client.post('/api/v1/mobile/counselor/leads/create/', data={
            'name': 'Priya Student 1',
            'phone': '9876500011',
            'assigned_caller_id': self.caller1.pk,
        }, format='json')
        self.assertEqual(r1.status_code, status.HTTP_201_CREATED)
        lead1_id = r1.data['lead']['id']

        # Counselor 2 creates a lead
        self.client.force_authenticate(user=self.counselor2)
        r2 = self.client.post('/api/v1/mobile/counselor/leads/create/', data={
            'name': 'Amit Student 1',
            'phone': '9876500022',
            'assigned_caller_id': self.caller1.pk,
        }, format='json')
        self.assertEqual(r2.status_code, status.HTTP_201_CREATED)
        lead2_id = r2.data['lead']['id']

        # Counselor 1 lists created leads -> sees only lead1
        self.client.force_authenticate(user=self.counselor)
        list_res = self.client.get('/api/v1/mobile/counselor/created-leads/')
        self.assertEqual(list_res.status_code, status.HTTP_200_OK)
        lead_ids = [l['id'] for l in list_res.data['leads']]
        self.assertIn(lead1_id, lead_ids)
        self.assertNotIn(lead2_id, lead_ids)

        # Search filter
        search_res = self.client.get('/api/v1/mobile/counselor/created-leads/?search=Priya')
        self.assertEqual(search_res.status_code, status.HTTP_200_OK)
        self.assertEqual(len(search_res.data['leads']), 1)
        self.assertEqual(search_res.data['leads'][0]['id'], lead1_id)

    def test_counselor_created_lead_detail_and_isolation(self):
        # Counselor 1 creates lead
        self.client.force_authenticate(user=self.counselor)
        r = self.client.post('/api/v1/mobile/counselor/leads/create/', data={
            'name': 'Kavita Singh',
            'phone': '9876500033',
            'email': 'kavita@example.com',
            'location': 'Pune',
            'college': 'Fergusson',
            'neet_status': 'Qualified',
            'pcb_percentage': '88.5',
            'preferred_course': 'MBBS',
            'assigned_caller_id': self.caller1.pk,
        }, format='json')
        self.assertEqual(r.status_code, status.HTTP_201_CREATED)
        lead_id = r.data['lead']['id']

        # Counselor 1 fetches detail
        detail_res = self.client.get(f'/api/v1/mobile/counselor/created-leads/{lead_id}/')
        self.assertEqual(detail_res.status_code, status.HTTP_200_OK)
        lead_data = detail_res.data['lead']
        self.assertEqual(lead_data['name'], 'Kavita Singh')
        self.assertEqual(lead_data['phone'], '9876500033')
        self.assertEqual(lead_data['email'], 'kavita@example.com')
        self.assertEqual(lead_data['location'], 'Pune')
        self.assertEqual(lead_data['college'], 'Fergusson')
        self.assertEqual(lead_data['neet_status'], 'Qualified')
        self.assertEqual(lead_data['assigned_caller_id'], self.caller1.pk)
        self.assertEqual(len(lead_data['assignment_history']), 1)
        self.assertEqual(lead_data['assignment_history'][0]['new_caller_id'], self.caller1.pk)
        self.assertEqual(lead_data['assignment_history'][0]['reason'], 'Created and assigned by counselor')

        # Counselor 2 tries to fetch Counselor 1's created lead -> 404
        self.client.force_authenticate(user=self.counselor2)
        c2_res = self.client.get(f'/api/v1/mobile/counselor/created-leads/{lead_id}/')
        self.assertEqual(c2_res.status_code, status.HTTP_404_NOT_FOUND)

    def test_counselor_reassign_created_lead_success(self):
        # Counselor 1 creates lead assigned to caller1
        self.client.force_authenticate(user=self.counselor)
        r = self.client.post('/api/v1/mobile/counselor/leads/create/', data={
            'name': 'Deepak Verma',
            'phone': '9876500044',
            'assigned_caller_id': self.caller1.pk,
        }, format='json')
        self.assertEqual(r.status_code, status.HTTP_201_CREATED)
        lead_id = r.data['lead']['id']

        # Reassign to caller2
        reassign_res = self.client.post(f'/api/v1/mobile/counselor/created-leads/{lead_id}/reassign/', data={
            'caller_id': self.caller2.pk,
        }, format='json')
        self.assertEqual(reassign_res.status_code, status.HTTP_200_OK)
        self.assertTrue(reassign_res.data['success'])
        self.assertEqual(reassign_res.data['lead']['assigned_caller_id'], self.caller2.pk)

        # Verify DB state
        lead = Lead.objects.get(pk=lead_id)
        self.assertEqual(lead.assigned_caller, self.caller2)

        # Verify audit trail
        history = list(LeadAssignmentHistory.objects.filter(lead=lead).order_by('assigned_at'))
        self.assertEqual(len(history), 2)
        # 1st: created and assigned to caller1
        self.assertIsNone(history[0].previous_caller)
        self.assertEqual(history[0].new_caller, self.caller1)
        self.assertEqual(history[0].reason, 'Created and assigned by counselor')
        # 2nd: reassigned to caller2
        self.assertEqual(history[1].previous_caller, self.caller1)
        self.assertEqual(history[1].new_caller, self.caller2)
        self.assertEqual(history[1].assigned_by, self.counselor)
        self.assertEqual(history[1].reason, 'Reassigned by counselor')

        # Verify caller2 now sees it, and caller1 does not
        self.client.force_authenticate(user=self.caller2)
        c2_list = self.client.get('/api/v1/mobile/leads/')
        self.assertIn(lead_id, [l['id'] for l in c2_list.data])

        self.client.force_authenticate(user=self.caller1)
        c1_list = self.client.get('/api/v1/mobile/leads/')
        self.assertNotIn(lead_id, [l['id'] for l in c1_list.data])

    def test_counselor_reassign_validation_rules(self):
        # Counselor 1 creates lead assigned to caller1
        self.client.force_authenticate(user=self.counselor)
        r = self.client.post('/api/v1/mobile/counselor/leads/create/', data={
            'name': 'Validation Lead',
            'phone': '9876500055',
            'assigned_caller_id': self.caller1.pk,
        }, format='json')
        lead_id = r.data['lead']['id']

        # Reassign to same caller -> 400
        res = self.client.post(f'/api/v1/mobile/counselor/created-leads/{lead_id}/reassign/', data={
            'caller_id': self.caller1.pk,
        }, format='json')
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('already assigned', str(res.data))

        # Reassign to inactive caller -> 400
        res = self.client.post(f'/api/v1/mobile/counselor/created-leads/{lead_id}/reassign/', data={
            'caller_id': self.inactive_caller.pk,
        }, format='json')
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

        # Reassign to non-caller (admin) -> 400
        res = self.client.post(f'/api/v1/mobile/counselor/created-leads/{lead_id}/reassign/', data={
            'caller_id': self.admin.pk,
        }, format='json')
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

        # Counselor 2 cannot reassign Counselor 1's lead -> 404
        self.client.force_authenticate(user=self.counselor2)
        res = self.client.post(f'/api/v1/mobile/counselor/created-leads/{lead_id}/reassign/', data={
            'caller_id': self.caller2.pk,
        }, format='json')
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)

    def test_caller_role_cannot_access_created_leads(self):
        self.client.force_authenticate(user=self.caller1)
        res = self.client.get('/api/v1/mobile/counselor/created-leads/')
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)
