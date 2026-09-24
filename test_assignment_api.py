from django.test import TestCase
from rest_framework.test import APIClient
from apps.accounts.models import User
from apps.leads.models import Lead


class AssignmentAPITests(TestCase):
    def test_assigns_generated_leads_through_api(self):
        admin = User.objects.create_user('assignment-admin', role='ADMIN')
        caller = User.objects.create_user('assignment-caller', role='CALLER')
        leads = [Lead.objects.create(name=f'Lead {n}', phone=f'987654321{n}') for n in range(3)]
        api = APIClient()
        api.force_authenticate(admin)
        response = api.post('/api/v1/leads/bulk-assign/', {
            'lead_ids': [lead.pk for lead in leads], 'caller_id': caller.pk,
            'reassign': False, 'reason': 'API assignment test',
        }, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(Lead.objects.filter(assigned_caller=caller).count(), 3)
