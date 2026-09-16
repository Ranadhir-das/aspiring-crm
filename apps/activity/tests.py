from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import User, CallerSession
from apps.calls.models import Call
from apps.followups.models import FollowUp
from apps.leads.models import Lead
from apps.leads.services import bulk_assign_leads

from .models import ActivityLog


class ActivityLogTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user('admin', role='ADMIN')
        self.caller = User.objects.create_user('caller', role='CALLER')
        self.other_caller = User.objects.create_user('other', role='CALLER')
        self.api = APIClient()

    def test_lead_created_via_crm_is_logged_with_actor(self):
        self.client.force_login(self.admin)
        response = self.client.post('/leads/new/', {
            'name': 'New Lead', 'phone': '9876500001', 'email': '', 'location': '',
            'college': '', 'neet_status': '', 'pcb_percentage': '', 'preferred_intake': '',
            'source': 'Website', 'campaign': '', 'status': 'PENDING', 'notes': '',
        })
        self.assertEqual(response.status_code, 302)
        lead = Lead.objects.get(phone='9876500001')
        entry = ActivityLog.objects.get(lead=lead, verb=ActivityLog.Verb.LEAD_CREATED)
        self.assertEqual(entry.actor, self.admin)
        self.assertIn('Website', entry.description)

    def test_status_change_via_crm_is_logged_with_actor_and_transition(self):
        lead = Lead.objects.create(name='Edit me', phone='9876500002', assigned_caller=self.caller)
        ActivityLog.objects.filter(lead=lead).delete()
        self.client.force_login(self.admin)
        response = self.client.post(f'/leads/{lead.pk}/', {
            'action': 'save', 'name': lead.name, 'phone': lead.phone, 'email': '', 'location': '',
            'college': '', 'neet_status': '', 'pcb_percentage': '', 'preferred_intake': '',
            'source': '', 'campaign': '', 'status': 'INTERESTED', 'notes': '',
        })
        self.assertEqual(response.status_code, 302)
        entry = ActivityLog.objects.get(lead=lead, verb=ActivityLog.Verb.STATUS_CHANGED)
        self.assertEqual(entry.actor, self.admin)
        self.assertIn('Pending', entry.description)
        self.assertIn('Interested', entry.description)

    def test_bulk_assignment_is_logged_with_assigner_as_actor(self):
        lead = Lead.objects.create(name='Assign me', phone='9876500003')
        bulk_assign_leads([lead.pk], self.caller, self.admin, reason='Test')
        entry = ActivityLog.objects.get(lead=lead, verb=ActivityLog.Verb.ASSIGNED)
        self.assertEqual(entry.actor, self.admin)
        self.assertIn('caller', entry.description.lower())

        bulk_assign_leads([lead.pk], self.other_caller, self.admin, reassign=True, reason='Reassign')
        entry = ActivityLog.objects.get(lead=lead, verb=ActivityLog.Verb.REASSIGNED)
        self.assertEqual(entry.actor, self.admin)

    def test_call_creation_logs_call_and_resulting_status_change_with_caller_as_actor(self):
        lead = Lead.objects.create(name='Call me', phone='9876500004', assigned_caller=self.caller)
        ActivityLog.objects.filter(lead=lead).delete()
        self.api.force_authenticate(self.caller)
        response = self.api.post('/api/v1/calls/', {
            'lead': lead.pk, 'started_at': timezone.now().isoformat(), 'outcome': 'INTERESTED', 'notes': 'Good chat',
        })
        self.assertEqual(response.status_code, 201, response.data)
        call_entry = ActivityLog.objects.get(lead=lead, verb=ActivityLog.Verb.CALL_LOGGED)
        self.assertEqual(call_entry.actor, self.caller)
        status_entry = ActivityLog.objects.get(lead=lead, verb=ActivityLog.Verb.STATUS_CHANGED)
        self.assertEqual(status_entry.actor, self.caller)
        self.assertIn('Interested', status_entry.description)

    def test_followup_completion_via_crm_is_logged_with_completer_as_actor(self):
        lead = Lead.objects.create(name='Followup lead', phone='9876500005', assigned_caller=self.caller)
        followup = FollowUp.objects.create(lead=lead, caller=self.caller, scheduled_at=timezone.now())
        self.client.force_login(self.admin)
        response = self.client.post(f'/follow-ups/{followup.pk}/complete/')
        self.assertEqual(response.status_code, 302)
        entry = ActivityLog.objects.get(lead=lead, verb=ActivityLog.Verb.FOLLOWUP_COMPLETED)
        self.assertEqual(entry.actor, self.admin)

    def test_login_and_logout_sessions_are_logged(self):
        from apps.accounts.api.session_service import close_session
        session = CallerSession.objects.create(caller=self.caller)
        self.assertTrue(ActivityLog.objects.filter(actor=self.caller, verb=ActivityLog.Verb.LOGGED_IN).exists())
        close_session(session, timezone.now())
        self.assertTrue(ActivityLog.objects.filter(actor=self.caller, verb=ActivityLog.Verb.LOGGED_OUT).exists())

    def test_caller_detail_page_shows_only_that_caller_and_requires_management(self):
        lead = Lead.objects.create(name='Scoped', phone='9876500006', assigned_caller=self.caller)
        self.api.force_authenticate(self.caller)
        self.api.post('/api/v1/calls/', {
            'lead': lead.pk, 'started_at': timezone.now().isoformat(), 'outcome': 'BUSY',
        })
        self.api.force_authenticate(self.other_caller)
        other_lead = Lead.objects.create(name='Other', phone='9876500007', assigned_caller=self.other_caller)
        self.api.post('/api/v1/calls/', {
            'lead': other_lead.pk, 'started_at': timezone.now().isoformat(), 'outcome': 'BUSY',
        })

        self.client.force_login(self.admin)
        response = self.client.get(f'/team/{self.caller.pk}/')
        self.assertEqual(response.status_code, 200)
        actors = {item.actor_id for item in response.context['records']}
        self.assertEqual(actors, {self.caller.pk})

        self.client.force_login(self.caller)
        self.assertEqual(self.client.get(f'/team/{self.caller.pk}/').status_code, 403)

    def test_lead_detail_page_renders_activity_timeline(self):
        lead = Lead.objects.create(name='Timeline', phone='9876500008', assigned_caller=self.caller)
        self.client.force_login(self.admin)
        response = self.client.get(f'/leads/{lead.pk}/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Lead created')
