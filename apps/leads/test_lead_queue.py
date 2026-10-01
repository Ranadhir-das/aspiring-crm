from datetime import timedelta

from django.core.paginator import Paginator
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.calls.models import Call
from apps.followups.models import FollowUp
from apps.leads.models import Lead, LeadAvailability, Service
from apps.leads.public_intake import create_website_lead
from apps.leads.queue import caller_lead_queue


class CallerLeadQueueTests(TestCase):
    available_url = '/api/v1/mobile/leads/available/?category=ALL'

    def setUp(self):
        self.api = APIClient()
        self.caller = User.objects.create_user('queue-caller', role='CALLER')
        self.other_caller = User.objects.create_user('queue-other', role='CALLER')
        self.service_mbbs = Service.objects.get(code='MBBS')
        self.service_mba = Service.objects.get(code='MBA')
        self.caller.services.add(self.service_mbbs)
        self.other_caller.services.add(self.service_mba)
        self.api.force_authenticate(self.caller)

    def test_1_due_followup_appears_first(self):
        now = timezone.now()
        # Create other priority leads first
        lead_pending = Lead.objects.create(name='Pending Other', phone='9000000001', status=Lead.Status.PENDING)
        lead_assigned = Lead.objects.create(
            name='Assigned Lead', phone='9000000002', status=Lead.Status.PENDING,
            assigned_caller=self.caller, assigned_at=now - timedelta(hours=3),
        )
        lead_website, _, _ = create_website_lead({'name': 'Website Lead', 'phone': '9000000003', 'service': 'MBBS'})

        # Priority 1: Due follow-up
        lead_followup = Lead.objects.create(
            name='Followup Lead', phone='9000000004', status=Lead.Status.CALL_BACK,
            assigned_caller=self.caller,
        )
        FollowUp.objects.create(
            lead=lead_followup, caller=self.caller, scheduled_at=now - timedelta(minutes=15),
            status=FollowUp.Status.PENDING,
        )

        response = self.api.get(self.available_url)
        self.assertEqual(response.status_code, 200)
        lead_ids = [item['id'] for item in response.data]
        self.assertEqual(lead_ids[0], lead_followup.pk)
        self.assertEqual(response.data[0]['queue_priority'], 1)
        self.assertEqual(response.data[0]['queue_category'], 'FOLLOW_UP')

    def test_2_multiple_due_followups_order_by_earliest_scheduled_at(self):
        now = timezone.now()
        lead_later = Lead.objects.create(name='Later Due', phone='9000000011', assigned_caller=self.caller)
        FollowUp.objects.create(
            lead=lead_later, caller=self.caller, scheduled_at=now - timedelta(minutes=30),
            status=FollowUp.Status.PENDING,
        )

        lead_earlier = Lead.objects.create(name='Earlier Due', phone='9000000012', assigned_caller=self.caller)
        FollowUp.objects.create(
            lead=lead_earlier, caller=self.caller, scheduled_at=now - timedelta(hours=2),
            status=FollowUp.Status.PENDING,
        )

        response = self.api.get(self.available_url)
        lead_ids = [item['id'] for item in response.data]
        self.assertEqual(lead_ids[:2], [lead_earlier.pk, lead_later.pk])

    def test_3_website_lead_appears_after_due_followup(self):
        now = timezone.now()
        lead_followup = Lead.objects.create(name='Due Lead', phone='9000000021', assigned_caller=self.caller)
        FollowUp.objects.create(
            lead=lead_followup, caller=self.caller, scheduled_at=now - timedelta(minutes=10),
            status=FollowUp.Status.PENDING,
        )
        lead_website, _, _ = create_website_lead({'name': 'Website Lead', 'phone': '9000000022', 'service': 'MBBS'})

        response = self.api.get(self.available_url)
        lead_ids = [item['id'] for item in response.data]
        self.assertEqual(lead_ids[:2], [lead_followup.pk, lead_website.pk])
        self.assertEqual(response.data[1]['queue_priority'], 2)
        self.assertEqual(response.data[1]['queue_category'], 'WEBSITE')

    def test_4_website_lead_appears_before_manual_assignment(self):
        now = timezone.now()
        lead_assigned = Lead.objects.create(
            name='Manual Lead', phone='9000000031', assigned_caller=self.caller,
            assigned_at=now - timedelta(days=2),
        )
        lead_website, _, _ = create_website_lead({'name': 'Website Lead', 'phone': '9000000032', 'service': 'MBBS'})

        response = self.api.get(self.available_url)
        lead_ids = [item['id'] for item in response.data]
        self.assertEqual(lead_ids[:2], [lead_website.pk, lead_assigned.pk])
        self.assertEqual(response.data[0]['queue_priority'], 2)
        self.assertEqual(response.data[1]['queue_priority'], 3)
        self.assertEqual(response.data[1]['queue_category'], 'ASSIGNED')

    def test_5_manual_assignment_remains_available(self):
        now = timezone.now()
        lead_assigned = Lead.objects.create(
            name='My Assigned Lead', phone='9000000041', status=Lead.Status.PENDING,
            assigned_caller=self.caller, assigned_at=now - timedelta(hours=1),
        )
        response = self.api.get(self.available_url)
        lead_ids = [item['id'] for item in response.data]
        self.assertIn(lead_assigned.pk, lead_ids)
        assigned_data = next(item for item in response.data if item['id'] == lead_assigned.pk)
        self.assertEqual(assigned_data['queue_priority'], 3)
        self.assertEqual(assigned_data['queue_category'], 'ASSIGNED')

    def test_6_oldest_website_lead_first(self):
        now = timezone.now()
        lead_old, _, _ = create_website_lead({'name': 'Old Web', 'phone': '9000000051', 'service': 'MBBS'})
        lead_new, _, _ = create_website_lead({'name': 'New Web', 'phone': '9000000052', 'service': 'MBBS'})

        Lead.objects.filter(pk=lead_old.pk).update(created_at=now - timedelta(hours=3))
        Lead.objects.filter(pk=lead_new.pk).update(created_at=now - timedelta(hours=1))

        response = self.api.get(self.available_url)
        lead_ids = [item['id'] for item in response.data]
        self.assertEqual(lead_ids[:2], [lead_old.pk, lead_new.pk])

    def test_7_oldest_manual_assignment_first(self):
        now = timezone.now()
        lead_older = Lead.objects.create(
            name='Older Assigned', phone='9000000061', assigned_caller=self.caller,
            assigned_at=now - timedelta(hours=5),
        )
        lead_newer = Lead.objects.create(
            name='Newer Assigned', phone='9000000062', assigned_caller=self.caller,
            assigned_at=now - timedelta(hours=1),
        )
        response = self.api.get(self.available_url)
        lead_ids = [item['id'] for item in response.data]
        self.assertEqual(lead_ids[:2], [lead_older.pk, lead_newer.pk])

    def test_8_other_pending_leads_use_fallback_priority(self):
        now = timezone.now()
        lead_assigned = Lead.objects.create(
            name='Assigned Lead', phone='9000000071', assigned_caller=self.caller,
            assigned_at=now - timedelta(hours=1),
        )
        lead_batch_old = Lead.objects.create(
            name='Batch Old', phone='9000000072', status=Lead.Status.PENDING,
        )
        lead_batch_new = Lead.objects.create(
            name='Batch New', phone='9000000073', status=Lead.Status.PENDING,
        )
        Lead.objects.filter(pk=lead_batch_old.pk).update(created_at=now - timedelta(hours=4))
        Lead.objects.filter(pk=lead_batch_new.pk).update(created_at=now - timedelta(hours=2))

        response = self.api.get(self.available_url)
        lead_ids = [item['id'] for item in response.data]
        self.assertEqual(lead_ids, [lead_assigned.pk, lead_batch_old.pk, lead_batch_new.pk])

        old_data = next(item for item in response.data if item['id'] == lead_batch_old.pk)
        self.assertEqual(old_data['queue_priority'], 4)
        self.assertEqual(old_data['queue_category'], 'OTHER')

    def test_9_claimed_lead_is_excluded_from_available_queue(self):
        lead, _, _ = create_website_lead({'name': 'Claimed Lead', 'phone': '9000000081', 'service': 'MBBS'})
        claim_url = f'/api/v1/mobile/leads/{lead.pk}/claim/'
        self.assertEqual(self.api.post(claim_url).status_code, 200)

        response = self.api.get(self.available_url)
        lead_ids = [item['id'] for item in response.data]
        self.assertNotIn(lead.pk, lead_ids)

        self.api.force_authenticate(self.other_caller)
        response_other = self.api.get(self.available_url)
        self.assertNotIn(lead.pk, [item['id'] for item in response_other.data])

    def test_10_active_call_is_excluded(self):
        now = timezone.now()
        lead = Lead.objects.create(name='In Call Lead', phone='9000000091', assigned_caller=self.caller)
        call = Call.objects.create(lead=lead, caller=self.caller, phone_number=lead.phone, started_at=now)

        response = self.api.get(self.available_url)
        self.assertNotIn(lead.pk, [item['id'] for item in response.data])

        call.ended_at = now + timedelta(minutes=5)
        call.save(update_fields=['ended_at'])

        response_after = self.api.get(self.available_url)
        self.assertIn(lead.pk, [item['id'] for item in response_after.data])

    def test_11_closed_non_pending_lead_is_excluded(self):
        closed_statuses = [
            Lead.Status.ADMISSION_DONE,
            Lead.Status.NOT_INTERESTED,
            Lead.Status.WRONG_NUMBER,
            Lead.Status.DISCONNECTED,
            Lead.Status.NO_CANDIDATE,
        ]
        created = []
        for i, st in enumerate(closed_statuses):
            l = Lead.objects.create(
                name=f'Closed {st}', phone=f'90000001{i}1', status=st,
                assigned_caller=self.caller,
            )
            created.append(l)

        response = self.api.get(self.available_url)
        returned_ids = {item['id'] for item in response.data}
        for l in created:
            self.assertNotIn(l.pk, returned_ids)

    def test_12_caller_service_eligibility_is_respected(self):
        # Caller has MBBS only
        lead_mbbs, _, _ = create_website_lead({'name': 'MBBS Lead', 'phone': '9000000201', 'service': 'MBBS'})
        lead_mba, _, _ = create_website_lead({'name': 'MBA Lead', 'phone': '9000000202', 'service': 'MBA'})

        response = self.api.get(self.available_url)
        returned_ids = {item['id'] for item in response.data}
        self.assertIn(lead_mbbs.pk, returned_ids)
        self.assertNotIn(lead_mba.pk, returned_ids)

        # When caller's active services are deactivated
        self.service_mbbs.is_active = False
        self.service_mbbs.save()

        response_inactive = self.api.get(self.available_url)
        self.assertEqual(response_inactive.data, [])

    def test_13_another_callers_manually_assigned_lead_is_not_exposed(self):
        lead_other = Lead.objects.create(
            name='Other Caller Lead', phone='9000000301', assigned_caller=self.other_caller,
        )
        response = self.api.get(self.available_url)
        self.assertNotIn(lead_other.pk, [item['id'] for item in response.data])

    def test_14_multiple_pending_followups_for_same_lead_use_earliest_due(self):
        now = timezone.now()
        lead_multi = Lead.objects.create(name='Multi Followup', phone='9000000401', assigned_caller=self.caller)
        FollowUp.objects.create(
            lead=lead_multi, caller=self.caller, scheduled_at=now - timedelta(hours=3),
            status=FollowUp.Status.PENDING,
        )
        FollowUp.objects.create(
            lead=lead_multi, caller=self.caller, scheduled_at=now - timedelta(hours=1),
            status=FollowUp.Status.PENDING,
        )

        lead_compare = Lead.objects.create(name='Compare Followup', phone='9000000402', assigned_caller=self.caller)
        FollowUp.objects.create(
            lead=lead_compare, caller=self.caller, scheduled_at=now - timedelta(hours=2),
            status=FollowUp.Status.PENDING,
        )

        response = self.api.get(self.available_url)
        lead_ids = [item['id'] for item in response.data]
        self.assertEqual(lead_ids[:2], [lead_multi.pk, lead_compare.pk])

    def test_15_future_followup_does_not_receive_due_followup_priority(self):
        now = timezone.now()
        lead_future = Lead.objects.create(
            name='Future Followup', phone='9000000501', assigned_caller=self.caller,
            assigned_at=now - timedelta(hours=1),
        )
        FollowUp.objects.create(
            lead=lead_future, caller=self.caller, scheduled_at=now + timedelta(hours=2),
            status=FollowUp.Status.PENDING,
        )

        lead_website, _, _ = create_website_lead({'name': 'Website Lead', 'phone': '9000000502', 'service': 'MBBS'})

        response = self.api.get(self.available_url)
        lead_ids = [item['id'] for item in response.data]
        self.assertEqual(lead_ids[:2], [lead_website.pk, lead_future.pk])

        future_data = next(item for item in response.data if item['id'] == lead_future.pk)
        self.assertEqual(future_data['queue_priority'], 3)
        self.assertEqual(future_data['queue_category'], 'ASSIGNED')

    def test_16_pagination_remains_correct(self):
        now = timezone.now()
        leads = [
            Lead.objects.create(
                name=f'Batch Lead {i:02d}', phone=f'9000006{i:03d}', status=Lead.Status.PENDING,
            )
            for i in range(25)
        ]

        # 1. Direct queryset pagination
        qs = caller_lead_queue(self.caller)
        paginator = Paginator(qs, 10)
        self.assertEqual(paginator.count, 25)
        page1 = list(paginator.page(1))
        page2 = list(paginator.page(2))
        page3 = list(paginator.page(3))
        self.assertEqual(len(page1), 10)
        self.assertEqual(len(page2), 10)
        self.assertEqual(len(page3), 5)
        self.assertEqual(len(set(page1 + page2 + page3)), 25)

        # 2. API endpoint pagination via ?page= and ?page_size=
        res_p1 = self.api.get(f'{self.available_url}&page=1&page_size=10')
        self.assertEqual(res_p1.status_code, 200)
        self.assertIn('results', res_p1.data)
        self.assertEqual(res_p1.data['count'], 25)
        self.assertEqual(len(res_p1.data['results']), 10)

        # 3. API endpoint unpaginated request preserves flat list
        res_unpaginated = self.api.get(self.available_url)
        self.assertEqual(res_unpaginated.status_code, 200)
        self.assertIsInstance(res_unpaginated.data, list)
        self.assertEqual(len(res_unpaginated.data), 25)

    def test_17_existing_claim_endpoint_still_works(self):
        lead, _, _ = create_website_lead({'name': 'Claimable Web', 'phone': '9000000701', 'service': 'MBBS'})
        claim_url = f'/api/v1/mobile/leads/{lead.pk}/claim/'
        response = self.api.post(claim_url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['claimed_by'], self.caller.pk)
        self.assertIsNotNone(response.data['claimed_at'])

    def test_18_existing_batch_import_behavior_preserved(self):
        lead = Lead.objects.create(
            name='Uncategorized Import', phone='9000000801', status=Lead.Status.PENDING,
            source='csv_import', service_type=None,
        )
        response = self.api.get(self.available_url)
        returned = next(item for item in response.data if item['id'] == lead.pk)
        self.assertEqual(returned['queue_priority'], 4)
        self.assertEqual(returned['queue_category'], 'OTHER')

    def test_19_existing_manual_assignment_semantics_preserved(self):
        lead = Lead.objects.create(
            name='Manual Admin Assigned', phone='9000000901', status=Lead.Status.PENDING,
            assigned_caller=self.caller,
        )
        self.assertFalse(hasattr(lead, 'availability'))
        response = self.api.get(self.available_url)
        returned = next(item for item in response.data if item['id'] == lead.pk)
        self.assertEqual(returned['queue_priority'], 3)
        self.assertEqual(returned['queue_category'], 'ASSIGNED')

    def test_20_stable_final_tie_breaker_uses_lead_id_ascending(self):
        now = timezone.now()
        lead_a = Lead.objects.create(
            name='Tie A', phone='9000001001', assigned_caller=self.caller, assigned_at=now,
        )
        lead_b = Lead.objects.create(
            name='Tie B', phone='9000001002', assigned_caller=self.caller, assigned_at=now,
        )
        response = self.api.get(self.available_url)
        lead_ids = [item['id'] for item in response.data]
        self.assertLess(lead_a.pk, lead_b.pk)
        self.assertEqual(lead_ids[:2], [lead_a.pk, lead_b.pk])


class AvailableLeadsEndpointTests(TestCase):
    available_url = '/api/v1/mobile/leads/available/'
    assigned_url = '/api/v1/mobile/leads/'

    def setUp(self):
        self.api = APIClient()
        self.caller = User.objects.create_user('caller-avail', role='CALLER')
        self.other_caller = User.objects.create_user('caller-other', role='CALLER')
        self.service_mbbs = Service.objects.get(code='MBBS')
        self.service_mba = Service.objects.get(code='MBA')
        self.caller.services.add(self.service_mbbs)
        self.other_caller.services.add(self.service_mba)
        self.api.force_authenticate(self.caller)

    def test_1_available_returns_unclaimed_website_lead(self):
        lead_web, _, _ = create_website_lead({'name': 'Web Unclaimed', 'phone': '9100000001', 'service': 'MBBS'})
        response = self.api.get(self.available_url)
        self.assertEqual(response.status_code, 200)
        ids = [item['id'] for item in response.data]
        self.assertIn(lead_web.pk, ids)
        item = next(it for it in response.data if it['id'] == lead_web.pk)
        self.assertEqual(item['queue_category'], 'WEBSITE')
        self.assertEqual(item['queue_priority'], 2)

    def test_2_available_does_not_return_manually_assigned_lead(self):
        lead_manual = Lead.objects.create(
            name='Manual Lead', phone='9100000002', status=Lead.Status.PENDING,
            assigned_caller=self.caller,
        )
        response = self.api.get(self.available_url)
        self.assertEqual(response.status_code, 200)
        ids = [item['id'] for item in response.data]
        self.assertNotIn(lead_manual.pk, ids)

    def test_3_available_does_not_return_imported_assigned_lead(self):
        lead_imported = Lead.objects.create(
            name='Imported Lead', phone='9100000003', status=Lead.Status.PENDING,
            source='csv_import', assigned_caller=self.caller,
        )
        response = self.api.get(self.available_url)
        self.assertEqual(response.status_code, 200)
        ids = [item['id'] for item in response.data]
        self.assertNotIn(lead_imported.pk, ids)

    def test_4_available_does_not_return_other_unassigned_non_website_lead(self):
        lead_other = Lead.objects.create(
            name='Other Lead', phone='9100000004', status=Lead.Status.PENDING,
            source='manual',
        )
        response = self.api.get(self.available_url)
        self.assertEqual(response.status_code, 200)
        ids = [item['id'] for item in response.data]
        self.assertNotIn(lead_other.pk, ids)

    def test_5_available_does_not_return_website_lead_claimed_by_other_caller(self):
        lead_web, _, _ = create_website_lead({'name': 'Claimed Web', 'phone': '9100000005', 'service': 'MBBS'})
        now = timezone.now()
        LeadAvailability.objects.filter(lead=lead_web).update(
            claimed_by=self.other_caller,
            claimed_at=now,
        )
        response = self.api.get(self.available_url)
        self.assertEqual(response.status_code, 200)
        ids = [item['id'] for item in response.data]
        self.assertNotIn(lead_web.pk, ids)

    def test_6_available_respects_service_eligibility(self):
        lead_mba, _, _ = create_website_lead({'name': 'MBA Lead', 'phone': '9100000006', 'service': 'MBA'})
        response = self.api.get(self.available_url)
        self.assertEqual(response.status_code, 200)
        ids = [item['id'] for item in response.data]
        self.assertNotIn(lead_mba.pk, ids)

    def test_7_available_respects_retry_cooldown(self):
        lead_web, _, _ = create_website_lead({'name': 'Cooldown Web', 'phone': '9100000007', 'service': 'MBBS'})
        now = timezone.now()
        LeadAvailability.objects.filter(lead=lead_web).update(
            available_at=now + timedelta(minutes=10)
        )
        response = self.api.get(self.available_url)
        ids = [item['id'] for item in response.data]
        self.assertNotIn(lead_web.pk, ids)

        # Once cooldown has elapsed:
        LeadAvailability.objects.filter(lead=lead_web).update(
            available_at=now - timedelta(minutes=1)
        )
        response2 = self.api.get(self.available_url)
        ids2 = [item['id'] for item in response2.data]
        self.assertIn(lead_web.pk, ids2)

    def test_8_available_excludes_closed_website_leads(self):
        lead_closed, _, _ = create_website_lead({'name': 'Closed Web', 'phone': '9100000008', 'service': 'MBBS'})
        lead_closed.status = Lead.Status.NOT_INTERESTED
        lead_closed.save(update_fields=['status'])

        response = self.api.get(self.available_url)
        self.assertEqual(response.status_code, 200)
        ids = [item['id'] for item in response.data]
        self.assertNotIn(lead_closed.pk, ids)

    def test_9_available_excludes_active_call_leads(self):
        lead_web, _, _ = create_website_lead({'name': 'In Call Web', 'phone': '9100000009', 'service': 'MBBS'})
        now = timezone.now()
        Call.objects.create(
            lead=lead_web, caller=self.other_caller, phone_number=lead_web.phone,
            started_at=now, ended_at=None,
        )
        response = self.api.get(self.available_url)
        self.assertEqual(response.status_code, 200)
        ids = [item['id'] for item in response.data]
        self.assertNotIn(lead_web.pk, ids)

    def test_10_claiming_website_lead_removes_it_from_available(self):
        lead_web, _, _ = create_website_lead({'name': 'To Claim', 'phone': '9100000010', 'service': 'MBBS'})
        r1 = self.api.get(self.available_url)
        self.assertIn(lead_web.pk, [item['id'] for item in r1.data])

        claim_res = self.api.post(f'/api/v1/mobile/leads/{lead_web.pk}/claim/')
        self.assertEqual(claim_res.status_code, 200)

        r2 = self.api.get(self.available_url)
        self.assertNotIn(lead_web.pk, [item['id'] for item in r2.data])

    def test_11_assigned_leads_remain_accessible_through_mobile_leads(self):
        lead_manual = Lead.objects.create(
            name='Caller Manual Lead', phone='9100000011', status=Lead.Status.PENDING,
            assigned_caller=self.caller,
        )
        response = self.api.get(self.assigned_url)
        self.assertEqual(response.status_code, 200)
        ids = [item['id'] for item in response.data]
        self.assertIn(lead_manual.pk, ids)

    def test_12_different_caller_cannot_see_another_callers_manually_assigned_lead(self):
        lead_other_caller = Lead.objects.create(
            name='Other Caller Lead', phone='9100000012', status=Lead.Status.PENDING,
            assigned_caller=self.other_caller,
        )
        response = self.api.get(self.assigned_url)
        self.assertEqual(response.status_code, 200)
        ids = [item['id'] for item in response.data]
        self.assertNotIn(lead_other_caller.pk, ids)

    def test_13_regression_given_W_M_O_available_returns_only_W(self):
        lead_w, _, _ = create_website_lead({'name': 'W Website', 'phone': '9100000021', 'service': 'MBBS'})
        lead_m = Lead.objects.create(
            name='M Manual', phone='9100000022', status=Lead.Status.PENDING,
            assigned_caller=self.caller,
        )
        lead_o = Lead.objects.create(
            name='O Other', phone='9100000023', status=Lead.Status.PENDING,
        )

        response = self.api.get(self.available_url)
        self.assertEqual(response.status_code, 200)
        ids = [item['id'] for item in response.data]

        self.assertIn(lead_w.pk, ids)
        self.assertNotIn(lead_m.pk, ids)
        self.assertNotIn(lead_o.pk, ids)
        self.assertEqual(ids, [lead_w.pk])


