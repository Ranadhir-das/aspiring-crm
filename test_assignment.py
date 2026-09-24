from django.test import TestCase
from apps.accounts.models import User
from apps.leads.models import Lead, LeadAssignmentHistory
from apps.leads.services import bulk_assign_leads


class AssignmentTests(TestCase):
    def test_assigns_generated_unassigned_leads(self):
        admin = User.objects.create_user('assignment-admin', role='ADMIN')
        caller = User.objects.create_user('assignment-caller', role='CALLER')
        leads = [Lead.objects.create(name=f'Lead {n}', phone=f'987654320{n}') for n in range(3)]
        result = bulk_assign_leads([lead.pk for lead in leads], caller, admin, reason='Initial assignment')
        self.assertTrue(result['success'])
        self.assertEqual(result['assigned_count'], 3)
        self.assertEqual(Lead.objects.filter(assigned_caller=caller).count(), 3)
        self.assertEqual(LeadAssignmentHistory.objects.filter(assigned_by=admin, new_caller=caller).count(), 3)
