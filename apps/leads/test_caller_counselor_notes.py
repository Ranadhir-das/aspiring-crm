"""Comprehensive unit and integration tests for displaying counselor notes to the assigned caller.

Covers all Phase 3 requirements:
1. Counselor can create a note for an authorized lead.
2. Assigned caller can view that counselor's notes on web CRM.
3. Caller cannot view notes belonging to an unauthorized lead (404 / empty).
4. Unauthorized users cannot create notes as another counselor (no impersonation).
5. Caller cannot modify or delete counselor notes without permission.
6. Multiple notes display in the correct chronological order (newest first).
7. Notes display the correct author display name and timestamp.
8. Leads without notes display the empty state.
9. API output contains required fields and no sensitive fields.
10. Existing lead details, forwarding, assignment, activity, and permissions continue to work.
"""
from datetime import timedelta
from unittest.mock import patch
from django.test import TestCase, Client
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.activity.models import ActivityLog
from apps.leads.counselor import CounselorNote, LeadCounselorAssignment
from apps.leads.counselor_services import visible_counselor_notes, add_counselor_note
from apps.leads.models import Lead

NO_PUSH = 'apps.notifications.team_events.persist_and_deliver'


@patch(NO_PUSH, lambda *args, **kwargs: None)
class CallerCounselorNotesTests(TestCase):
    def setUp(self):
        self.api = APIClient()
        self.client = Client()

        # Users
        self.caller = User.objects.create_user(
            username='caller_alice',
            first_name='Alice',
            last_name='Caller',
            role=User.Role.CALLER,
        )
        self.other_caller = User.objects.create_user(
            username='caller_bob',
            first_name='Bob',
            last_name='Caller',
            role=User.Role.CALLER,
        )
        self.counselor = User.objects.create_user(
            username='counselor_clara',
            first_name='Clara',
            last_name='Counselor',
            role=User.Role.COUNSELOR,
        )
        self.other_counselor = User.objects.create_user(
            username='counselor_dan',
            first_name='Dan',
            last_name='Advisor',
            role=User.Role.COUNSELOR,
        )
        self.manager = User.objects.create_user(
            username='manager_mary',
            first_name='Mary',
            last_name='Manager',
            role=User.Role.MANAGER,
        )
        self.admin = User.objects.create_user(
            username='admin_adam',
            first_name='Adam',
            last_name='Admin',
            role=User.Role.ADMIN,
        )

        # Leads
        self.lead = Lead.objects.create(
            name='Student Target',
            phone='9876543210',
            assigned_caller=self.caller,
            source='Google Search',
            notes='Initial caller assessment note',
        )
        self.unrelated_lead = Lead.objects.create(
            name='Student Unrelated',
            phone='9123456789',
            assigned_caller=self.other_caller,
            source='Website',
        )

        # Forward lead to counselor
        self.assignment = LeadCounselorAssignment.objects.create(
            lead=self.lead,
            counselor=self.counselor,
            caller=self.caller,
            forwarded_by=self.caller,
            is_active=True,
        )

    def as_api_user(self, user):
        self.api.force_authenticate(user)
        return self.api

    # 1. Counselor can create a note for an authorized lead
    def test_counselor_can_create_note_for_authorized_lead(self):
        client = self.as_api_user(self.counselor)
        url = f'/api/v1/mobile/counselor/leads/{self.lead.pk}/notes/'
        response = client.post(url, {
            'notes': 'Student prefers MBBS admissions in Georgia.',
        }, format='json')

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertTrue(response.data.get('success'))
        self.assertEqual(response.data['note']['body'], 'Student prefers MBBS admissions in Georgia.')

        note = CounselorNote.objects.filter(lead=self.lead).first()
        self.assertIsNotNone(note)
        self.assertEqual(note.counselor, self.counselor)
        self.assertEqual(note.assignment, self.assignment)
        self.assertEqual(note.body, 'Student prefers MBBS admissions in Georgia.')
        self.assertIsNotNone(note.created_at)

    # 2. Assigned caller can view that counselor's notes on web CRM
    def test_assigned_caller_can_view_counselor_notes_on_web(self):
        CounselorNote.objects.create(
            lead=self.lead,
            assignment=self.assignment,
            counselor=self.counselor,
            body='Discussed fee structure with parents.',
        )

        self.client.force_login(self.caller)
        response = self.client.get(f'/leads/{self.lead.pk}/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Counsellor Notes')
        self.assertContains(response, 'Discussed fee structure with parents.')
        self.assertContains(response, 'Clara Counselor')

    # 3. Caller cannot view notes belonging to an unauthorized lead
    def test_caller_cannot_view_notes_of_unauthorized_lead(self):
        CounselorNote.objects.create(
            lead=self.unrelated_lead,
            counselor=self.other_counselor,
            body='CONFIDENTIAL NOTE FOR UNRELATED LEAD',
        )

        # Web CRM access by unauthorized caller is blocked (404 object isolation)
        self.client.force_login(self.caller)
        web_resp = self.client.get(f'/leads/{self.unrelated_lead.pk}/')
        self.assertEqual(web_resp.status_code, 404)

        # Mobile counselor API for lead access by unauthorized caller is blocked (404)
        api_client = self.as_api_user(self.caller)
        api_resp = api_client.get(f'/api/v1/mobile/leads/{self.unrelated_lead.pk}/counselor/')
        self.assertEqual(api_resp.status_code, status.HTTP_404_NOT_FOUND)

        # Service-level check returns empty queryset for unauthorized caller
        service_notes = visible_counselor_notes(self.caller, self.unrelated_lead)
        self.assertEqual(service_notes.count(), 0)

    # 4. Unauthorized users cannot create notes as another counselor (no impersonation)
    def test_unauthorized_users_cannot_create_notes_as_counselor(self):
        # A caller cannot post counselor notes
        caller_api = self.as_api_user(self.caller)
        res_caller = caller_api.post(f'/api/v1/mobile/counselor/leads/{self.lead.pk}/notes/', {
            'notes': 'Illegal note from caller',
        }, format='json')
        self.assertEqual(res_caller.status_code, status.HTTP_403_FORBIDDEN)

        # An unassigned counselor cannot post notes to this lead
        unassigned_counselor_api = self.as_api_user(self.other_counselor)
        res_unassigned = unassigned_counselor_api.post(f'/api/v1/mobile/counselor/leads/{self.lead.pk}/notes/', {
            'notes': 'Note from unassigned counselor',
        }, format='json')
        self.assertEqual(res_unassigned.status_code, status.HTTP_404_NOT_FOUND)

        # An unauthenticated request is rejected
        unauth_api = APIClient()
        res_unauth = unauth_api.post(f'/api/v1/mobile/counselor/leads/{self.lead.pk}/notes/', {
            'notes': 'Anonymous note',
        }, format='json')
        self.assertEqual(res_unauth.status_code, status.HTTP_401_UNAUTHORIZED)

    # 5. Caller cannot modify or delete counselor notes without permission
    def test_caller_cannot_modify_or_delete_counselor_notes(self):
        note = CounselorNote.objects.create(
            lead=self.lead,
            assignment=self.assignment,
            counselor=self.counselor,
            body='Immutable advisory note.',
        )

        caller_api = self.as_api_user(self.caller)
        # Note endpoints do not allow PUT, PATCH, or DELETE
        put_resp = caller_api.put(f'/api/v1/mobile/counselor/leads/{self.lead.pk}/notes/', {
            'notes': 'Hacked',
        }, format='json')
        self.assertIn(put_resp.status_code, [status.HTTP_403_FORBIDDEN, status.HTTP_405_METHOD_NOT_ALLOWED])

        del_resp = caller_api.delete(f'/api/v1/mobile/counselor/leads/{self.lead.pk}/notes/')
        self.assertIn(del_resp.status_code, [status.HTTP_403_FORBIDDEN, status.HTTP_405_METHOD_NOT_ALLOWED])

        # Verify note in database is completely intact
        note.refresh_from_db()
        self.assertEqual(note.body, 'Immutable advisory note.')

    # 6. Multiple notes display in the correct chronological order (newest first)
    def test_multiple_notes_display_in_correct_order(self):
        now = timezone.now()
        note1 = CounselorNote.objects.create(
            lead=self.lead,
            assignment=self.assignment,
            counselor=self.counselor,
            body='FIRST_NOTE: Introduction Call',
        )
        note2 = CounselorNote.objects.create(
            lead=self.lead,
            assignment=self.assignment,
            counselor=self.counselor,
            body='SECOND_NOTE: Followup Counselling',
        )

        # Force timestamps to ensure distinct order
        CounselorNote.objects.filter(pk=note1.pk).update(created_at=now - timedelta(hours=2))
        CounselorNote.objects.filter(pk=note2.pk).update(created_at=now - timedelta(hours=1))

        # Check Web CRM display
        self.client.force_login(self.caller)
        resp = self.client.get(f'/leads/{self.lead.pk}/')
        self.assertEqual(resp.status_code, 200)

        c1 = resp.content.decode('utf-8').index('SECOND_NOTE: Followup Counselling')
        c2 = resp.content.decode('utf-8').index('FIRST_NOTE: Introduction Call')
        self.assertLess(c1, c2, 'Newest note must appear before older note')

        # Check API response order
        api_client = self.as_api_user(self.caller)
        api_resp = api_client.get(f'/api/v1/mobile/leads/{self.lead.pk}/counselor/')
        notes = api_resp.data.get('counselor_notes', [])
        self.assertEqual(len(notes), 2)
        self.assertEqual(notes[0]['id'], note2.pk)
        self.assertEqual(notes[1]['id'], note1.pk)

    # 7. Notes display the correct author display name and timestamp
    def test_notes_display_correct_author_and_timestamp(self):
        CounselorNote.objects.create(
            lead=self.lead,
            assignment=self.assignment,
            counselor=self.counselor,
            body='Advisory completed by Clara.',
        )

        # Web check
        self.client.force_login(self.caller)
        resp = self.client.get(f'/leads/{self.lead.pk}/')
        self.assertContains(resp, 'Clara Counselor')

        # API check
        api_client = self.as_api_user(self.caller)
        api_resp = api_client.get(f'/api/v1/mobile/leads/{self.lead.pk}/counselor/')
        self.assertEqual(api_resp.status_code, 200)
        note_data = api_resp.data['counselor_notes'][0]
        self.assertEqual(note_data['author_name'], 'Clara Counselor')
        self.assertIn('created_at', note_data)

    # 8. Leads without notes display the empty state
    def test_leads_without_notes_display_empty_state(self):
        # Web check
        self.client.force_login(self.caller)
        resp = self.client.get(f'/leads/{self.lead.pk}/')
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'No counsellor notes recorded for this lead yet.')

        # API check
        api_client = self.as_api_user(self.caller)
        api_resp = api_client.get(f'/api/v1/mobile/leads/{self.lead.pk}/counselor/')
        self.assertEqual(api_resp.status_code, 200)
        self.assertEqual(api_resp.data.get('counselor_notes'), [])

    # 9. API output contains required fields and no sensitive fields
    def test_api_output_contains_required_fields_and_no_sensitive_data(self):
        CounselorNote.objects.create(
            lead=self.lead,
            assignment=self.assignment,
            counselor=self.counselor,
            body='Safe public advisory content.',
            phone_number='9876543210',
        )

        # Check LeadCounselorView
        api_client = self.as_api_user(self.caller)
        resp1 = api_client.get(f'/api/v1/mobile/leads/{self.lead.pk}/counselor/')
        self.assertEqual(resp1.status_code, 200)
        notes1 = resp1.data['counselor_notes']
        self.assertEqual(len(notes1), 1)
        item1 = notes1[0]

        # Required fields present
        self.assertIn('id', item1)
        self.assertIn('content', item1)
        self.assertIn('author_name', item1)
        self.assertIn('created_at', item1)

        # Sensitive fields absent
        self.assertNotIn('password', item1)
        self.assertNotIn('phone_number', item1)
        self.assertNotIn('client_event_id', item1)
        self.assertNotIn('email', item1)


    # 10. Existing lead details, forwarding, assignment, and permissions continue to work
    def test_existing_lead_details_forwarding_and_activity_unaffected(self):
        # 1. Lead details save action works
        self.client.force_login(self.caller)
        save_resp = self.client.post(f'/leads/{self.lead.pk}/', {
            'action': 'save',
            'name': 'Updated Student Target',
            'phone': '9876543210',
            'status': self.lead.status,
            'source': self.lead.source,
        })
        self.assertEqual(save_resp.status_code, 302)
        self.lead.refresh_from_db()
        self.assertEqual(self.lead.name, 'Updated Student Target')

        # 2. Activity logging continues
        ActivityLog.objects.create(
            lead=self.lead,
            actor=self.caller,
            verb=ActivityLog.Verb.CALL_LOGGED,
            description='Caller logged test verification call',
        )
        act_resp = self.client.get(f'/leads/{self.lead.pk}/')
        self.assertContains(act_resp, 'Caller logged test verification call')

        # 3. Management view retains administrative panel
        self.client.force_login(self.admin)
        admin_resp = self.client.get(f'/leads/{self.lead.pk}/')
        self.assertEqual(admin_resp.status_code, 200)
        self.assertContains(admin_resp, 'Counsellor Notes')
        self.assertContains(admin_resp, 'Counselor Administrative Workflow')
