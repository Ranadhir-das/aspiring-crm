from datetime import timedelta
from io import BytesIO
from uuid import uuid4
from unittest.mock import patch

from django.core.files.base import ContentFile
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient
from openpyxl import Workbook

from apps.accounts.models import User
from apps.calls.models import Call
from apps.followups.models import FollowUp
from apps.leads.models import Lead, Counselling, Admission, WhatsAppTemplate, WhatsAppActivity
from apps.leads.api.serializers import LeadImportSerializer
from apps.leads.services import commit_import
from apps.leads.whatsapp_service import render_template
from apps.performance.models import PointsEntry
from apps.performance.services import adjust_points, reconcile_daily_calls, score_call, score_followup


class CourseOutcomeTests(TestCase):
    def setUp(self):
        self.caller = User.objects.create_user('course-caller', role='CALLER')
        self.other = User.objects.create_user('course-other', role='CALLER')
        self.admin = User.objects.create_user('course-admin', role='ADMIN')
        self.lead = Lead.objects.create(name='Student', phone='9876543210', assigned_caller=self.caller,
                                        preferred_course='BTECH')
        self.api = APIClient()
        self.api.force_authenticate(self.caller)

    def payload(self, **changes):
        now = timezone.now()
        data = dict(lead=self.lead.pk, client_event_id=str(uuid4()), started_at=now.isoformat(),
                    ended_at=now.isoformat(), outcome='INTERESTED', selected_course='BTECH',
                    expected_admission_year=2027)
        data.update(changes)
        if data['outcome'] != 'INTERESTED':
            data.pop('selected_course', None)
            data.pop('expected_admission_year', None)
        return data

    def post(self, data):
        return self.api.post('/api/v1/calls/', data, format='json')

    def test_upload_requires_course(self):
        serializer = LeadImportSerializer(data={'file': ContentFile(b'name,phone\nA,9876543211', name='a.csv')})
        self.assertFalse(serializer.is_valid())
        self.assertIn('preferred_course', serializer.errors)
        self.client.force_login(self.admin)
        response = self.client.post(reverse('web:lead-import'), {'action': 'import', 'file': ContentFile(b'name,phone\nA,9876543211', name='a.csv')})
        self.assertEqual(response.status_code, 200)
        self.assertIn('preferred_course', response.context['form'].errors)
        self.assertEqual(Lead.objects.count(), 1)

    def test_import_others_requires_custom_name(self):
        for custom in ['', '   ']:
            serializer = LeadImportSerializer(data={'file': ContentFile(b'name,phone', name='a.csv'), 'preferred_course': 'OTHERS', 'preferred_course_custom': custom})
            self.assertFalse(serializer.is_valid())
        with self.assertRaises(ValueError):
            commit_import(ContentFile(b'name,phone', name='a.csv'), self.admin, preferred_course='OTHERS')

    def test_csv_course_applied_and_duplicate_preference_unchanged(self):
        file = ContentFile(b'name,phone\nDuplicate,9876543210\nNew,9876543211\nNew2,9876543212', name='a.csv')
        result = commit_import(file, self.admin, preferred_course='MBBS')
        self.assertEqual(result['created_count'], 2)
        self.assertEqual(result['duplicate_count'], 1)
        self.assertEqual(set(Lead.objects.exclude(pk=self.lead.pk).values_list('preferred_course', flat=True)), {'MBBS'})
        self.lead.refresh_from_db()
        self.assertEqual(self.lead.preferred_course, 'BTECH')

    def test_xlsx_custom_course_and_preview(self):
        book = Workbook(); book.active.append(['name', 'phone']); book.active.append(['New', '9876543213'])
        stream = BytesIO(); book.save(stream)
        result = commit_import(ContentFile(stream.getvalue(), name='a.xlsx'), self.admin,
                               preferred_course='OTHERS', preferred_course_custom='  Data   Science ')
        lead = Lead.objects.exclude(pk=self.lead.pk).get()
        self.assertEqual((lead.preferred_course, lead.preferred_course_custom), ('OTHERS', 'Data Science'))
        self.client.force_login(self.admin)
        preview = self.client.post(reverse('web:lead-import'), {'action': 'preview', 'preferred_course': 'BTECH', 'file': ContentFile(b'name,phone\nA,9876543214', name='b.csv')})
        self.assertContains(preview, 'Preferred course: BTECH')
        self.assertEqual(Lead.objects.count(), 2)

    def test_historical_null_preference_stays_valid_without_points(self):
        self.lead.preferred_course = None; self.lead.save()
        self.lead.full_clean()
        response = self.post(self.payload())
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data['course_classification'], 'UNKNOWN')
        self.assertEqual(response.data['outcome_points'], 0)
        self.lead.refresh_from_db(); self.assertIsNone(self.lead.preferred_course)
        self.lead.preferred_course = 'BTECH'; self.lead.save()
        score_call(response.data['id'])
        self.assertFalse(PointsEntry.objects.exists())

    def test_interested_requires_course_and_year(self):
        for missing in ['selected_course', 'expected_admission_year']:
            data = self.payload(); data.pop(missing)
            response = self.post(data)
            self.assertEqual(response.status_code, 400)
            self.assertIn(missing, response.data)
        self.assertFalse(Call.objects.exists())

    def test_custom_course_validation(self):
        self.assertEqual(self.post(self.payload(selected_course='OTHERS')).status_code, 400)
        self.assertEqual(self.post(self.payload(selected_course='FAKE')).status_code, 400)
        self.assertEqual(self.post(self.payload(selected_course_custom='unexpected')).status_code, 400)
        response = self.post(self.payload(selected_course='OTHERS', selected_course_custom='  Data   Science '))
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data['selected_course_custom'], 'Data Science')
        self.assertEqual(response.data['outcome_points'], 30)

    def test_this_next_and_arbitrary_custom_year(self):
        for year in [timezone.localdate().year, timezone.localdate().year+1, 2039, 1900, 9999, 1]:
            with self.subTest(year=year):
                response = self.post(self.payload(expected_admission_year=year))
                self.assertEqual(response.status_code, 201, response.data)
                self.assertEqual(response.data['expected_admission_year'], year)
        for year in [0, -1, 10000, '2026x', 2027.5]:
            self.assertEqual(self.post(self.payload(expected_admission_year=year)).status_code, 400)

    def test_own_course_points_server_owned_idempotent(self):
        payload = self.payload(points=999, caller=self.other.pk, course_classification='OTHER')
        first = self.post(payload); second = self.post(payload)
        self.assertEqual((first.status_code, second.status_code), (201, 200))
        self.assertEqual(first.data['id'], second.data['id'])
        self.assertEqual(first.data['course_classification'], 'OWN')
        self.assertEqual(first.data['outcome_points'], 15)
        self.assertEqual(PointsEntry.objects.filter(lead=self.lead, event='INTERESTED_LEAD').count(), 1)
        call = Call.objects.get(); self.assertEqual(call.caller_id, self.caller.pk)
        self.assertEqual(self.post(dict(payload, selected_course='MBBS')).status_code, 409)
        self.assertEqual(self.post(dict(payload, expected_admission_year=2038)).status_code, 409)
        score_call(call.pk)
        self.assertEqual(PointsEntry.objects.get(lead=self.lead).points, 15)

    def test_other_course_awards_once_across_distinct_calls(self):
        first = self.post(self.payload(selected_course='MBA'))
        second = self.post(self.payload(selected_course='MBA'))
        self.assertEqual(first.data['outcome_points'], 30)
        self.assertEqual(second.data['outcome_points'], 0)
        self.assertEqual(PointsEntry.objects.filter(lead=self.lead).count(), 1)

    def test_custom_course_comparison_normalizes_case_and_spaces(self):
        self.lead.preferred_course = 'OTHERS'; self.lead.preferred_course_custom = 'Data Science'; self.lead.save()
        response = self.post(self.payload(selected_course='OTHERS', selected_course_custom='data   SCIENCE'))
        self.assertEqual(response.data['course_classification'], 'OWN')
        self.assertEqual(response.data['outcome_points'], 15)

    def test_prohibited_outcomes_reject_followup_and_whatsapp(self):
        for outcome in ['NOT_INTERESTED', 'NO_CANDIDATE', 'WRONG_NUMBER']:
            with self.subTest(outcome=outcome):
                self.assertEqual(self.post(self.payload(outcome=outcome, callback_at=timezone.now().isoformat())).status_code, 400)
                self.assertEqual(self.post(self.payload(outcome=outcome, whatsapp_message='Hello')).status_code, 400)
                saved = self.post(self.payload(outcome=outcome))
                self.assertEqual(saved.status_code, 201, saved.data)
                self.assertEqual(self.api.post(f"/api/v1/calls/{saved.data['id']}/whatsapp/initiate/", {}).status_code, 400)
        self.assertFalse(FollowUp.objects.exists())
        self.assertFalse(WhatsAppActivity.objects.exists())

    def test_allowed_outcomes_followup_whatsapp_and_retry(self):
        for outcome in ['INTERESTED', 'NO_ANSWER', 'BUSY', 'CALL_BACK', 'FORWARDED_CALLS', 'DISCONNECTED', 'ALL_WAITING', 'NOT_REACHABLE', 'RINGING']:
            with self.subTest(outcome=outcome):
                payload = self.payload(outcome=outcome, callback_at=(timezone.now()+timedelta(days=1)).isoformat(), whatsapp_message='Hello student')
                first = self.post(payload); replay = self.post(payload)
                self.assertEqual((first.status_code, replay.status_code), (201, 200))
                self.assertEqual(FollowUp.objects.filter(call_id=first.data['id']).count(), 1)
                url = f"/api/v1/calls/{first.data['id']}/whatsapp/initiate/"
                self.assertEqual(self.api.post(url, {}).status_code, 200)
                self.assertEqual(self.api.post(url, {}).status_code, 200)
                self.assertEqual(WhatsAppActivity.objects.filter(call_id=first.data['id']).count(), 1)

    def test_outcome_and_handoff_caller_isolation(self):
        saved = self.post(self.payload(whatsapp_message='Hello'))
        self.api.force_authenticate(self.other)
        self.assertEqual(self.post(self.payload()).status_code, 404)
        self.assertEqual(self.api.post(f"/api/v1/calls/{saved.data['id']}/whatsapp/initiate/", {}).status_code, 404)

    def test_private_template_crud_and_ownership(self):
        url = '/api/v1/mobile/whatsapp/templates/'
        shared = WhatsAppTemplate.objects.create(title='Shared', message='Legacy {name}')
        created = self.api.post(url, {'title': 'Mine', 'message': 'Hello {{student_name}}', 'owner': self.other.pk}, format='json')
        self.assertEqual(created.status_code, 201, created.data)
        pk = created.data['id']; detail = f'{url}{pk}/'
        self.assertEqual(created.data['owner'], self.caller.pk)
        self.assertEqual(self.api.patch(detail, {'title': 'Updated'}, format='json').status_code, 200)
        self.api.force_authenticate(self.other)
        self.assertNotIn(pk, [item['id'] for item in self.api.get(url).data])
        self.assertIn(shared.pk, [item['id'] for item in self.api.get(url).data])
        self.assertEqual(self.api.patch(detail, {'message': 'Attack'}, format='json').status_code, 404)
        self.assertEqual(self.api.delete(detail).status_code, 404)
        self.api.force_authenticate(self.caller)
        self.assertEqual(self.api.delete(f'{url}{shared.pk}/').status_code, 404)
        self.assertEqual(self.api.delete(detail).status_code, 204)

    def test_private_template_cannot_be_used_by_other_caller(self):
        template = WhatsAppTemplate.objects.create(owner=self.other, title='Private', message='Secret')
        self.assertEqual(self.post(self.payload(whatsapp_template=template.pk)).status_code, 400)
        self.assertEqual(self.api.post(f'/api/v1/mobile/leads/{self.lead.pk}/whatsapp/initiate/', {'template_id': template.pk}).status_code, 400)

    def test_placeholder_rendering_is_literal_and_supports_legacy(self):
        rendered = render_template('Hi {{student_name}} {name}: {{course}} / {{year}} from {{caller_name}} {{unknown}}', student_name='A', course='MBA', year=2050, caller_name='B')
        self.assertEqual(rendered, 'Hi A A: MBA / 2050 from B {{unknown}}')

    def test_course_details_appear_in_history_mobile_lead_and_crm(self):
        saved = self.post(self.payload())
        history = self.api.get('/api/v1/calls/mine/').data[0]
        self.assertEqual(history['selected_course'], 'BTECH')
        details = self.api.get(f'/api/v1/mobile/leads/{self.lead.pk}/').data
        self.assertEqual(details['preferred_course'], 'BTECH')
        self.assertEqual(details['interested_details']['expected_admission_year'], 2027)
        self.client.force_login(self.admin)
        self.assertContains(self.client.get(reverse('web:lead-detail', args=[self.lead.pk])), 'Own course')
        self.assertContains(self.client.get(reverse('web:calls')), '15 outcome points')

    def test_transaction_failure_does_not_leave_partial_outcome_points_or_followup(self):
        with patch('apps.calls.api.views.FollowUp.objects.create', side_effect=RuntimeError('DB failure')):
            with self.assertRaises(RuntimeError):
                self.post(self.payload(callback_at=(timezone.now()+timedelta(days=1)).isoformat()))
        self.assertFalse(Call.objects.exists())
        self.assertFalse(PointsEntry.objects.exists())
        self.lead.refresh_from_db(); self.assertEqual(self.lead.status, 'PENDING')

    def test_old_saved_interested_request_replays_without_new_required_fields(self):
        event = uuid4(); now = timezone.now()
        call = Call.objects.create(caller=self.caller, lead=self.lead, client_event_id=event,
                                   started_at=now, outcome='INTERESTED')
        payload = dict(client_event_id=str(event), lead=self.lead.pk, started_at=now.isoformat(), outcome='INTERESTED')
        response = self.post(payload)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['id'], call.pk)
        self.assertFalse(PointsEntry.objects.exists())
        self.assertEqual(self.post(dict(payload, client_event_id=str(uuid4()))).status_code, 400)

    def test_website_optional_followup_retains_claim(self):
        from apps.leads.models import Service, LeadAvailability
        from apps.leads.public_intake import create_website_lead
        from apps.leads.claiming import claim_website_lead
        self.caller.services.add(Service.objects.get(code='MBBS'))
        create_website_lead({'name': 'Website', 'phone': '9876543299', 'service': 'MBBS'})
        lead = Lead.objects.get(phone='9876543299')
        claim_website_lead(lead.pk, self.caller)
        response = self.post(self.payload(lead=lead.pk, outcome='BUSY', callback_at=(timezone.now()+timedelta(days=1)).isoformat()))
        self.assertEqual(response.status_code, 201, response.data)
        lead.refresh_from_db()
        self.assertEqual(lead.assigned_caller_id, self.caller.pk)
        self.assertEqual(LeadAvailability.objects.get(lead=lead).claimed_by_id, self.caller.pk)
        self.assertEqual(FollowUp.objects.filter(lead=lead).count(), 1)

    def test_website_handoff_during_retry_cooldown_but_not_after_new_claim(self):
        from apps.leads.models import Service, LeadAvailability
        from apps.leads.public_intake import create_website_lead
        from apps.leads.claiming import claim_website_lead
        service = Service.objects.get(code='MBBS')
        self.caller.services.add(service); self.other.services.add(service)
        create_website_lead({'name': 'Website', 'phone': '9876543299', 'service': 'MBBS'})
        lead = Lead.objects.get(phone='9876543299'); claim_website_lead(lead.pk, self.caller)
        response = self.post(self.payload(lead=lead.pk, outcome='BUSY', whatsapp_message='Hello'))
        self.assertEqual(response.status_code, 201, response.data)
        url = f"/api/v1/calls/{response.data['id']}/whatsapp/initiate/"
        self.assertEqual(self.api.post(url, {}).status_code, 200)
        LeadAvailability.objects.filter(lead=lead).update(available_at=timezone.now()-timedelta(seconds=1))
        claim_website_lead(lead.pk, self.other)
        self.assertEqual(self.api.post(url, {}).status_code, 400)

    def test_no_event_id_retry_still_deduplicates_structured_outcome(self):
        data = self.payload(); data.pop('client_event_id')
        self.assertEqual(self.post(data).status_code, 201)
        self.assertEqual(self.post(data).status_code, 200)
        self.assertEqual(Call.objects.count(), 1)
        self.assertEqual(PointsEntry.objects.count(), 1)


class UpdatedPointsTests(TestCase):
    def setUp(self):
        self.caller = User.objects.create_user('rule-caller', role='CALLER')
        self.admin = User.objects.create_user('rule-admin', role='ADMIN')
        self.lead = Lead.objects.create(name='Student', phone='9876543210', assigned_caller=self.caller)

    def test_daily_volume_thresholds_and_cap_are_not_cumulative(self):
        now = timezone.now()
        previous = 0
        for count, expected in [(49, 0), (50, 25), (99, 25), (100, 50), (150, 75), (200, 100), (250, 100)]:
            Call.objects.bulk_create([Call(caller=self.caller, lead=self.lead, started_at=now) for _ in range(count-previous)])
            previous = count
            entry = reconcile_daily_calls(self.caller.pk)
            self.assertEqual(entry.points if entry else 0, expected)
            reconcile_daily_calls(self.caller.pk)
            self.assertEqual(PointsEntry.objects.count(), int(expected > 0))

    def test_historical_daily_ledger_not_recalculated(self):
        today = timezone.localdate()
        old = PointsEntry.objects.create(caller=self.caller, event='CALL_DAILY_BONUS', points=7, reason='Legacy award', event_key=f'daily_calls:{self.caller.pk}:{today}')
        Call.objects.bulk_create([Call(caller=self.caller, lead=self.lead, started_at=timezone.now()) for _ in range(200)])
        reconcile_daily_calls(self.caller.pk)
        old.refresh_from_db(); self.assertEqual((old.points, old.reason, old.rules_version), (7, 'Legacy award', None))

    def test_ontime_followup_awards_two_once(self):
        item = FollowUp.objects.create(caller=self.caller, lead=self.lead, scheduled_at=timezone.now()+timedelta(hours=1), status='COMPLETED')
        for _ in range(2): score_followup(item.pk)
        self.assertEqual(PointsEntry.objects.get(followup=item).points, 2)

    def test_pending_overdue_strictly_24_hours_once_per_lead(self):
        now = timezone.now()
        item = FollowUp.objects.create(caller=self.caller, lead=self.lead, scheduled_at=now)
        score_followup(item.pk, now=now+timedelta(hours=24))
        self.assertFalse(PointsEntry.objects.exists())
        score_followup(item.pk, now=now+timedelta(hours=24, seconds=1))
        other = FollowUp.objects.create(caller=self.caller, lead=self.lead, scheduled_at=now-timedelta(days=2))
        score_followup(other.pk)
        self.assertEqual(PointsEntry.objects.count(), 1)
        self.assertEqual(PointsEntry.objects.get().points, -10)

    def test_counselling_and_admission_values(self):
        for kind in ['WALK_IN', 'GOOGLE_MEET']:
            row = Counselling.objects.create(caller=self.caller, lead=self.lead, counselling_type=kind, conducted_at=timezone.now())
            self.assertEqual(PointsEntry.objects.get(counselling=row).points, 75)
        row = Admission.objects.create(caller=self.caller, lead=self.lead, created_by=self.admin)
        self.assertEqual(PointsEntry.objects.get(admission=row).points, 500)

    def test_legacy_phone_counselling_is_not_assumed_to_be_video(self):
        row = Counselling.objects.create(caller=self.caller, lead=self.lead, counselling_type='ONLINE')
        self.assertFalse(PointsEntry.objects.filter(counselling=row).exists())

    def test_adjustment_values_and_retry(self):
        for reason, units, expected in [('UNPLANNED_LEAVE', 1, -100), ('UNPLANNED_LEAVE', 2, -200), ('CONSECUTIVE_UNAPPROVED_LEAVE', 3, -150), ('INDISCIPLINE_WORKPLACE_CONDUCT', 1, -50)]:
            key = uuid4()
            args = dict(actor=self.admin, caller=self.caller, reason_type=reason, units=units, key=key, reason='Reviewed')
            row = adjust_points(**args)
            self.assertEqual(row.points, expected)
            self.assertEqual(adjust_points(**args).pk, row.pk)
        for units in [1, 2]:
            with self.assertRaises(ValidationError):
                adjust_points(actor=self.admin, caller=self.caller, reason_type='CONSECUTIVE_UNAPPROVED_LEAVE', units=units)

    def test_management_bonus_range_and_permissions(self):
        for points in [50, 125, 200]:
            row = adjust_points(actor=self.admin, caller=self.caller, reason_type='MANAGEMENT_BONUS', points=points, reason='Expo performance')
            self.assertEqual(row.points, points)
        for points in [49, 201, -50, 0]:
            with self.assertRaises(ValidationError):
                adjust_points(actor=self.admin, caller=self.caller, reason_type='MANAGEMENT_BONUS', points=points, reason='Invalid')


from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest import skipUnless
from django.db import connection, connections
from django.test import TransactionTestCase


@skipUnless(connection.vendor == 'postgresql', 'Requires PostgreSQL row locking')
class ConcurrentCourseOutcomeTests(TransactionTestCase):
    def test_concurrent_double_save_creates_one_call_followup_and_award(self):
        caller = User.objects.create_user('concurrent-course', role='CALLER')
        lead = Lead.objects.create(name='Student', phone='9876543210', preferred_course='MBA', assigned_caller=caller)
        now = timezone.now()
        payload = dict(lead=lead.pk, client_event_id=str(uuid4()), started_at=now.isoformat(), outcome='INTERESTED',
                       selected_course='MBA', expected_admission_year=2030, callback_at=(now+timedelta(days=1)).isoformat())
        barrier = Barrier(2)
        def submit():
            try:
                api = APIClient(); api.force_authenticate(caller)
                barrier.wait(timeout=10)
                return api.post('/api/v1/calls/', payload, format='json').status_code
            finally:
                connections.close_all()
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(submit) for _ in range(2)]
            results = [future.result(timeout=20) for future in futures]
        self.assertEqual(sorted(results), [200, 201])
        self.assertEqual(Call.objects.count(), 1)
        self.assertEqual(FollowUp.objects.count(), 1)
        self.assertEqual(PointsEntry.objects.get().points, 15)
