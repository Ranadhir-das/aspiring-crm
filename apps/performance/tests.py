import uuid
from datetime import datetime, timedelta
from io import StringIO
from django.contrib.admin.sites import AdminSite
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.db.models import Sum
from django.test import RequestFactory, TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient
from apps.accounts.models import CallerSession, User
from apps.calls.models import Call
from apps.followups.models import FollowUp
from apps.leads.models import Lead, LeadImportBatch
from apps.web.caller_profile import profile_data, status_data
from apps.web.models import AuditEvent
from .admin import PointsEntryAdmin
from .models import LeadMilestone, PointsAdjustment, PointsEntry
from .reporting import Window, metrics, report_window, trend
from .services import adjust_points, record_milestone, score_call, score_followup


class PointsTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user('points-admin', role='ADMIN', is_staff=True)
        self.caller = User.objects.create_user('points-caller', role='CALLER')
        self.other = User.objects.create_user('points-other', role='CALLER')
        self.manager = User.objects.create_user('points-manager', role='MANAGER')
        self.lead = Lead.objects.create(name='A lead', phone='123456', assigned_caller=self.caller)
        self.api = APIClient()

    def call(self, duration=0, outcome='', **kwargs):
        return Call.objects.create(caller=self.caller, lead=self.lead, started_at=kwargs.pop('started_at', timezone.now()), duration_seconds=duration, outcome=outcome, **kwargs)

    def total(self, **filters):
        return PointsEntry.objects.filter(**filters).aggregate(n=Sum('points'))['n'] or 0

    def test_duration_thresholds_use_only_highest_tier(self):
        for seconds, expected in [(0, 1), (29, 3), (30, 4), (119, 4), (120, 5), (299, 5), (300, 6), (1000, 6)]:
            with self.subTest(seconds=seconds):
                call = self.call(seconds)
                self.assertEqual(self.total(call=call), expected)
                self.assertLessEqual(PointsEntry.objects.filter(call=call, event='DURATION').count(), 1)

    def test_connected_outcomes_and_invalid_number(self):
        self.assertEqual(self.total(call=self.call(0, 'NOT_INTERESTED')), 3)
        self.assertEqual(self.total(call=self.call(0, 'NO_ANSWER')), 1)
        self.assertEqual(self.total(call=self.call(300, 'BUSY')), 1)
        call = self.call(300, 'WRONG_NUMBER')
        self.assertEqual(self.total(call=call), 0)
        self.assertTrue(PointsEntry.objects.filter(call=call, event='INVALID', points=0).exists())

    def test_call_edits_reconcile_without_stacking_or_deleting(self):
        call = self.call(30)
        count = PointsEntry.objects.filter(call=call).count()
        call.save()
        score_call(call.pk)
        self.assertEqual(PointsEntry.objects.filter(call=call).count(), count)
        call.duration_seconds = 300
        call.save()
        self.assertEqual(self.total(call=call), 6)
        self.assertEqual(self.total(call=call, event='DURATION'), 3)
        call.outcome = 'WRONG_NUMBER'
        call.save()
        self.assertEqual(self.total(call=call), 0)
        self.assertGreater(PointsEntry.objects.filter(call=call, points__lt=0).count(), 0)

    def test_interest_once_across_calls_status_toggles_and_reassignment(self):
        self.call(10, 'INTERESTED')
        self.lead.status = 'INTERESTED'
        self.lead.save()
        self.lead.status = 'PENDING'
        self.lead.save()
        self.lead.status = 'INTERESTED'
        self.lead.assigned_caller = self.other
        self.lead.save()
        self.assertEqual(PointsEntry.objects.filter(lead=self.lead, event='INTERESTED').count(), 1)
        self.assertEqual(self.total(event='INTERESTED', caller=self.caller), 5)

    def test_completed_and_missed_followups_awarded_once(self):
        item = FollowUp.objects.create(caller=self.caller, lead=self.lead, scheduled_at=timezone.now()-timedelta(hours=1))
        self.assertEqual(self.total(followup=item), -3)
        item.status = 'COMPLETED'
        item.save()
        item.save()
        score_followup(item.pk)
        self.assertEqual(self.total(followup=item), 0)
        self.assertEqual(PointsEntry.objects.filter(followup=item).count(), 2)
        on_time = FollowUp.objects.create(caller=self.caller, lead=self.lead, scheduled_at=timezone.now()+timedelta(hours=1), status='COMPLETED')
        self.assertEqual(self.total(followup=on_time), 3)

    def test_cancelled_followup_is_not_penalized(self):
        item = FollowUp.objects.create(caller=self.caller, lead=self.lead, scheduled_at=timezone.now()-timedelta(hours=1), status='CANCELLED')
        self.assertFalse(PointsEntry.objects.filter(followup=item).exists())

    def test_scheduler_and_backfill_are_idempotent(self):
        item = FollowUp.objects.create(caller=self.caller, lead=self.lead, scheduled_at=timezone.now()+timedelta(hours=1))
        FollowUp.objects.filter(pk=item.pk).update(scheduled_at=timezone.now()-timedelta(hours=1))
        Call.objects.bulk_create([Call(caller=self.caller, lead=self.lead, started_at=timezone.now(), duration_seconds=300)])
        for _ in range(2):
            call_command('reconcile_points', backfill=True, stdout=StringIO())
        self.assertEqual(self.total(caller=self.caller), 3)  # six for call, minus three missed

    def test_every_milestone_has_default_weight_and_evidence(self):
        for event, expected in [('COUNSELLING', 8), ('APPLICATION', 10), ('ADMISSION', 20), ('FALSE_STATUS', -5)]:
            item = record_milestone(actor=self.admin, caller=self.caller, lead=self.lead, event=event, reason='Verified reference 123')
            again = record_milestone(actor=self.admin, caller=self.caller, lead=self.lead, event=event, reason='Retry')
            self.assertEqual(item.pk, again.pk)
            self.assertEqual(self.total(event=event), expected)
        self.assertEqual(AuditEvent.objects.filter(category='POINTS').count(), 4)

    def test_milestone_requires_admin_correct_owner_and_reason(self):
        for actor in [self.caller, self.manager]:
            with self.assertRaises(PermissionDenied):
                record_milestone(actor=actor, caller=self.caller, lead=self.lead, event='ADMISSION', reason='Claim')
        with self.assertRaises(ValidationError):
            record_milestone(actor=self.admin, caller=self.other, lead=self.lead, event='ADMISSION', reason='Claim')
        with self.assertRaises(ValidationError):
            record_milestone(actor=self.admin, caller=self.caller, lead=self.lead, event='ADMISSION', reason='  ')

    def test_manual_adjustment_is_reasoned_audited_and_retry_safe(self):
        key = uuid.uuid4()
        data = dict(actor=self.admin, caller=self.caller, points=-7, reason='Duplicate call correction', key=key)
        adjust_points(**data)
        adjust_points(**data)
        self.assertEqual(self.total(caller=self.caller), -7)
        self.assertEqual(AuditEvent.objects.filter(category='POINTS').count(), 1)
        with self.assertRaises(ValidationError):
            adjust_points(**{**data, 'points': 5})
        with self.assertRaises(ValidationError):
            adjust_points(**{**data, 'key': uuid.uuid4(), 'reason': ' '})
        with self.assertRaises(PermissionDenied):
            adjust_points(**{**data, 'actor': self.caller})

    def test_ledger_is_immutable_and_event_keys_are_unique(self):
        self.call()
        entry = PointsEntry.objects.first()
        with self.assertRaises(ValidationError):
            entry.save()
        with self.assertRaises(ValidationError):
            entry.delete()
        with self.assertRaises(IntegrityError), transaction.atomic():
            PointsEntry.objects.create(caller=self.caller, event='DIALED', points=1, reason='Duplicate', event_key=entry.event_key)

    def test_admin_ledger_is_read_only_and_not_visible_to_caller(self):
        model_admin = PointsEntryAdmin(PointsEntry, AdminSite())
        request = RequestFactory().get('/admin/')
        request.user = self.admin
        self.assertTrue(model_admin.has_view_permission(request))
        self.assertFalse(model_admin.has_add_permission(request))
        self.assertFalse(model_admin.has_change_permission(request))
        self.assertFalse(model_admin.has_delete_permission(request))
        request.user = self.caller
        self.assertFalse(model_admin.has_view_permission(request))

    def test_mobile_lifetime_points_include_history_and_exclude_other_callers(self):
        self.call(30, started_at=timezone.now() - timedelta(days=60))
        self.call(0, 'NO_ANSWER')
        Call.objects.create(caller=self.other, lead=self.lead, started_at=timezone.now(), duration_seconds=300)
        self.api.force_authenticate(self.caller)
        response = self.api.get('/api/v1/points/me/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['summary']['lifetime_points'], 5)
        self.assertEqual(response.data['summary']['total_points'], 1)
        self.assertTrue(all(item['caller'] == self.caller.pk for item in response.data['results']))
        self.api.force_authenticate(self.manager)
        response = self.api.get(f'/api/v1/points/callers/{self.caller.pk}/')
        self.assertEqual(response.data['summary']['lifetime_points'], 5)

    def test_points_api_own_scope_and_management_access(self):
        self.call(30)
        self.api.force_authenticate(self.caller)
        response = self.api.get('/api/v1/points/me/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['summary']['total_points'], 4)
        self.assertEqual(self.api.get(f'/api/v1/points/callers/{self.other.pk}/').status_code, 403)
        self.assertEqual(self.api.post('/api/v1/points/adjustments/', {}).status_code, 403)
        self.api.force_authenticate(self.admin)
        self.assertEqual(self.api.get(f'/api/v1/points/callers/{self.caller.pk}/').status_code, 200)
        self.api.force_authenticate(None)
        self.assertEqual(self.api.get('/api/v1/points/me/').status_code, 401)

    def test_api_invalid_filters_and_adjustments(self):
        self.api.force_authenticate(self.admin)
        self.assertEqual(self.api.get(f'/api/v1/points/callers/{self.caller.pk}/', {'start_date':'bad'}).status_code, 400)
        payload = dict(request_id=str(uuid.uuid4()), caller=self.caller.pk, points=9, reason='Quality review')
        self.assertEqual(self.api.post('/api/v1/points/adjustments/', payload).status_code, 201)
        self.assertEqual(self.api.post('/api/v1/points/adjustments/', payload).status_code, 201)
        self.assertEqual(self.total(caller=self.caller), 9)
        self.assertEqual(self.api.post('/api/v1/points/adjustments/', {**payload, 'reason':' '}).status_code, 400)

    def test_call_api_idempotency_key_prevents_duplicate_points(self):
        self.api.force_authenticate(self.caller)
        data = {'lead': self.lead.pk, 'client_event_id': str(uuid.uuid4()), 'started_at': timezone.now().isoformat(), 'duration_seconds': 35, 'outcome':'NOT_INTERESTED'}
        url = '/api/v1/calls/'
        first = self.api.post(url, data)
        self.assertEqual(first.status_code, 201, first.data)
        second = self.api.post(url, data)
        self.assertEqual(second.status_code, 200, second.data)
        self.assertEqual(Call.objects.count(), 1)
        self.assertEqual(self.total(caller=self.caller), 4)
        self.assertEqual(self.api.post(url, {**data, 'duration_seconds': 100}).status_code, 409)

    def test_hourly_filters_and_pie_are_consistent(self):
        start = timezone.make_aware(datetime(2026, 9, 10, 9))
        self.call(30, 'NOT_INTERESTED', started_at=start)
        self.call(45, 'NOT_INTERESTED', started_at=start+timedelta(minutes=20))
        self.call(30, 'BUSY', started_at=start+timedelta(hours=1))
        window = Window(start, start+timedelta(hours=1), 'hour')
        context = profile_data(self.caller, window)
        self.assertEqual(context['stats']['calls'], 2)
        self.assertEqual(context['pie_total'], 2)
        self.assertEqual(sum(p['value'] for p in context['pipeline']), 2)
        self.assertEqual(context['profile_chart']['trend'][0]['count'], 2)
        self.assertEqual(context['profile_chart']['trend'][0]['points'], 8)

    def test_pie_status_aggregation_ignores_lead_ordering(self):
        batch = LeadImportBatch.objects.create(filename='same.csv')
        for i in range(5):
            Lead.objects.create(name=f'Person {i}', phone=str(i), assigned_caller=self.caller, import_batch=batch)
        data = status_data(self.caller.assigned_leads.order_by('import_batch_id', 'name', 'pk'))
        self.assertEqual(sum(row['value'] for row in data), 6)
        self.assertEqual(data[0]['value'], 6)

    def test_calendar_totals_and_negative_points(self):
        adjust_points(actor=self.admin, caller=self.caller, points=-8, reason='Review', key=uuid.uuid4())
        self.call(30)
        _, window, _ = report_window({})
        data = metrics(self.caller, window)
        self.assertEqual((data['total_points'], data['positive_points'], data['negative_points']), (-4, 4, -8))
        self.assertEqual(data['daily_points'], -4)
        self.assertEqual(data['weekly_points'], -4)
        self.assertEqual(data['monthly_points'], -4)

    def test_filters_validate_order_range_hours_and_zero_hour(self):
        for params in [{'start_date':'2026-09-15','end_date':'2026-09-10'}, {'start_hour':24}, {'start_date':'2020-01-01','end_date':'2026-01-01'}, {'start_date':'2026-01-01','end_date':'2026-02-10','interval':'hour'}]:
            self.assertFalse(report_window(params)[2])
        _, window, valid = report_window({'start_date':'2026-09-01','end_date':'2026-09-01','end_hour':'0','interval':'hour'})
        self.assertTrue(valid)
        self.assertEqual(window.end-window.start, timedelta(hours=1))
        self.assertEqual(len(trend(self.caller, window)), 1)

    def test_web_own_profile_and_admin_forms(self):
        self.client.force_login(self.caller)
        url = reverse('web:caller-detail', args=[self.caller.pk])
        self.assertEqual(self.client.get(url).status_code, 200)
        self.assertEqual(self.client.post(url, {'action':'adjust','points':20,'reason':'Cheat'}).status_code, 403)
        self.assertEqual(self.client.get(reverse('web:caller-detail', args=[self.other.pk])).status_code, 403)
        self.client.force_login(self.admin)
        response = self.client.post(url, {'action':'adjust','points':-2,'reason':'Review','request_id':str(uuid.uuid4())})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.total(caller=self.caller), -2)
        response = self.client.get(reverse('web:performance'), {'compare':self.caller.pk,'interval':'hour','start_date':timezone.localdate(),'end_date':timezone.localdate()})
        self.assertContains(response, 'Caller progress')
        self.assertContains(response, 'negative-bar')
        self.assertEqual(len(response.context['chart_data']['trend']), 24)

    def test_session_overlap_is_clipped_and_marked_estimated(self):
        start = timezone.make_aware(datetime(2026,9,10,9))
        session = CallerSession.objects.create(caller=self.caller, active_seconds=1200)
        CallerSession.objects.filter(pk=session.pk).update(logged_in_at=start, last_seen=start+timedelta(hours=2))
        data = profile_data(self.caller, Window(start+timedelta(hours=1), start+timedelta(hours=2), 'hour'))
        self.assertEqual(data['login_seconds'], 3600)
        self.assertEqual(data['app_active_seconds'], 600)
        self.assertTrue(data['active_estimated'])

    def test_later_note_edit_cannot_turn_timely_completion_into_missed(self):
        now = timezone.now()
        item = FollowUp.objects.create(caller=self.caller, lead=self.lead, scheduled_at=now+timedelta(hours=1), status='COMPLETED')
        original = item.completed_at
        score_followup(item.pk, now=now+timedelta(days=1))
        item.notes = 'Additional context'
        item.save()
        self.assertEqual(item.completed_at, original)
        self.assertEqual(self.total(followup=item), 3)
