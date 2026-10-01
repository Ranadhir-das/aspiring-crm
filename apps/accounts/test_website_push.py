from datetime import timedelta
from unittest.mock import patch

from django.core.files.base import ContentFile
from django.db import transaction, IntegrityError
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.accounts.models import User, PushDevice, PushReceipt
from apps.accounts.push_notifications import notify_callers_about_new_website_lead, check_push_receipts
from apps.leads.claiming import available_website_leads, claim_website_lead
from apps.leads.models import Lead, Service, WebsiteSource, WebsiteLeadSubmission
from apps.leads.public_intake import create_website_lead
from apps.leads.services import commit_import, bulk_assign_leads


@override_settings(WEBSITE_LEAD_PUSH_ENABLED=True)
class WebsitePushTests(TestCase):
    def setUp(self):
        self.service = Service.objects.get(code='APOSTILLE')
        self.source = WebsiteSource.objects.create(name='Site', code='TEST_PUSH', default_service=self.service)
        self.source.allowed_services.add(self.service)
        self.caller = User.objects.create_user('push-eligible', role='CALLER')
        self.caller.services.add(self.service)
        self.device = PushDevice.objects.create(user=self.caller, expo_push_token='ExpoPushToken[eligible]', last_seen=timezone.now())
        self.count = 0
        def reply(path, payload):
            self.count += 1
            return {'data': [{'status': 'ok', 'id': f'ticket-{self.count}-{n}'} for n, _ in enumerate(payload)]}
        self.mock = patch('apps.accounts.push_notifications.expo_request', side_effect=reply).start()
        self.addCleanup(patch.stopall)

    def create(self, phone='9876543210'):
        with self.captureOnCommitCallbacks(execute=True):
            return create_website_lead({'name': 'Rahul Sharma', 'phone': phone}, website_source=self.source)[0]

    def test_new_lead_eligible_payload_and_no_assignment(self):
        lead = self.create()
        payload = self.mock.call_args.args[1][0]
        self.assertEqual(payload['to'], self.device.expo_push_token)
        self.assertEqual(payload['title'], 'New Lead Available')
        self.assertEqual(payload['data'], {'type': 'NEW_WEBSITE_LEAD', 'lead_id': lead.pk, 'service': 'APOSTILLE'})
        self.assertEqual(payload['body'], 'Rahul Sharma • APOSTILLE lead is available')
        self.assertEqual(payload['channelId'], 'default')
        self.assertEqual(payload['sound'], 'default')
        self.assertNotIn(lead.phone, str(payload))
        self.assertIsNone(lead.assigned_caller_id)
        self.assertIsNone(lead.availability.claimed_at)
        self.assertIn(lead, available_website_leads(self.caller))

    def test_ineligible_inactive_users_and_noncallers_excluded(self):
        for name, active, role, mapped in [('wrong-service', True, 'CALLER', False),
                                         ('inactive', False, 'CALLER', True), ('manager', True, 'MANAGER', True)]:
            user = User.objects.create_user(name, is_active=active, role=role)
            if mapped:
                user.services.add(self.service)
            PushDevice.objects.create(user=user, expo_push_token=f'ExpoPushToken[{name}]')
        self.create()
        self.assertEqual(len(self.mock.call_args.args[1]), 1)

    def test_multiple_active_devices_only(self):
        PushDevice.objects.create(user=self.caller, expo_push_token='ExpoPushToken[second]')
        PushDevice.objects.create(user=self.caller, expo_push_token='ExpoPushToken[inactive]', active=False)
        self.create()
        tokens = [item['to'] for item in self.mock.call_args.args[1]]
        self.assertEqual(len(tokens), 2)
        self.assertEqual(len(set(tokens)), 2)

    def test_duplicate_tokens_cannot_exist(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            PushDevice.objects.create(user=self.caller, expo_push_token=self.device.expo_push_token)
        self.create()
        self.assertEqual(len(self.mock.call_args.args[1]), 1)

    def test_unregistered_ticket_deactivates_without_deleting(self):
        self.mock.side_effect = None
        self.mock.return_value = {'data': [{'status': 'error', 'details': {'error': 'DeviceNotRegistered'}}]}
        self.create()
        self.device.refresh_from_db()
        self.assertFalse(self.device.active)
        self.create('9876543211')
        self.assertEqual(self.mock.call_count, 1)

    def test_network_failure_does_not_rollback_lead(self):
        self.mock.side_effect = TimeoutError()
        lead = self.create()
        self.assertTrue(Lead.objects.filter(pk=lead.pk).exists())
        self.assertEqual(WebsiteLeadSubmission.objects.count(), 1)
        self.assertTrue(lead.availability)

    def test_bad_expo_response_does_not_fail_or_deactivate_device(self):
        self.mock.side_effect = None
        for response in [{'errors': [{'code': 'INVALID_CREDENTIALS'}]}, {'data': None}, {'data': []}]:
            self.mock.return_value = response
            self.create(f'98765432{self.count:02d}')
            self.count += 1
        self.device.refresh_from_db()
        self.assertTrue(self.device.active)

    def test_no_eligible_caller_succeeds(self):
        self.caller.services.clear()
        self.create()
        self.mock.assert_not_called()
        self.assertEqual(Lead.objects.count(), 1)

    def test_eligible_caller_without_device_succeeds(self):
        self.device.delete()
        self.create()
        self.mock.assert_not_called()

    def test_duplicate_website_submission_only_notifies_once(self):
        self.create()
        self.create('98765 43210')
        self.assertEqual(self.mock.call_count, 1)
        self.assertEqual(Lead.objects.count(), 1)
        self.assertEqual(WebsiteLeadSubmission.objects.count(), 2)

    def test_manual_and_import_assignment_never_notify(self):
        with self.captureOnCommitCallbacks(execute=True):
            lead = Lead.objects.create(name='Manual', phone='9876543211')
            bulk_assign_leads([lead.pk], self.caller, self.caller)
            commit_import(ContentFile(b'name,phone\nBatch,9876543212\n', name='leads.csv'), self.caller)
        self.mock.assert_not_called()

    def test_after_commit_only_and_rollback_discards_callback(self):
        with self.captureOnCommitCallbacks(execute=True):
            with transaction.atomic():
                create_website_lead({'name': 'Committed', 'phone': '9876543210'}, website_source=self.source)
                self.mock.assert_not_called()
        self.assertEqual(self.mock.call_count, 1)
        self.mock.reset_mock()
        with self.captureOnCommitCallbacks(execute=True):
            try:
                with transaction.atomic():
                    create_website_lead({'name': 'Rollback', 'phone': '9876543211'}, website_source=self.source)
                    raise ValueError('rollback')
            except ValueError:
                pass
        self.mock.assert_not_called()

    def test_claimed_before_commit_callback_is_skipped(self):
        with self.captureOnCommitCallbacks(execute=True):
            lead = create_website_lead({'name': 'Claimed', 'phone': '9876543210'}, website_source=self.source)[0]
            claim_website_lead(lead.pk, self.caller)
        self.mock.assert_not_called()

    def test_shared_eligibility_function_is_used(self):
        with patch('apps.accounts.push_notifications.eligible_website_callers', return_value=User.objects.none()) as eligible:
            self.create()
        eligible.assert_called_once_with(self.service.pk)
        self.mock.assert_not_called()

    def test_batches_at_most_100(self):
        PushDevice.objects.bulk_create([PushDevice(user=self.caller, expo_push_token=f'ExpoPushToken[batch{i}]') for i in range(101)])
        self.create()
        self.assertEqual([len(c.args[1]) for c in self.mock.call_args_list], [100, 2])

    def test_receipt_deactivates_unregistered_device(self):
        self.create()
        receipt = PushReceipt.objects.get()
        PushReceipt.objects.update(created_at=timezone.now() - timedelta(minutes=16))
        self.mock.side_effect = None
        self.mock.return_value = {'data': {receipt.pk: {'status': 'error', 'details': {'error': 'DeviceNotRegistered'}}}}
        self.assertEqual(check_push_receipts(), 1)
        self.device.refresh_from_db()
        self.assertFalse(self.device.active)
        self.assertFalse(PushReceipt.objects.exists())

    def test_old_receipt_cannot_deactivate_reregistered_device(self):
        self.create()
        receipt = PushReceipt.objects.get()
        PushReceipt.objects.update(created_at=timezone.now() - timedelta(minutes=16))
        PushDevice.objects.filter(pk=self.device.pk).update(last_seen=timezone.now())
        self.mock.side_effect = None
        self.mock.return_value = {'data': {receipt.pk: {'status': 'error', 'details': {'error': 'DeviceNotRegistered'}}}}
        check_push_receipts()
        self.device.refresh_from_db()
        self.assertTrue(self.device.active)

    def test_receipts_wait_and_network_errors_retain_pending_tickets(self):
        self.create()
        self.mock.reset_mock()
        self.assertEqual(check_push_receipts(), 0)
        self.mock.assert_not_called()
        PushReceipt.objects.update(created_at=timezone.now() - timedelta(minutes=16))
        self.mock.side_effect = TimeoutError()
        self.assertEqual(check_push_receipts(), 0)
        self.assertEqual(PushReceipt.objects.count(), 1)

    def test_receipt_credential_errors_do_not_deactivate_devices(self):
        self.create()
        receipt = PushReceipt.objects.get()
        PushReceipt.objects.update(created_at=timezone.now() - timedelta(minutes=16))
        self.mock.side_effect = None
        self.mock.return_value = {'data': {receipt.pk: {'status': 'error', 'details': {'error': 'InvalidCredentials'}}}}
        self.assertEqual(check_push_receipts(), 1)
        self.device.refresh_from_db()
        self.assertTrue(self.device.active)

    def test_api_network_failure_returns_accepted(self):
        from rest_framework.test import APIClient
        self.mock.side_effect = TimeoutError()
        with self.captureOnCommitCallbacks(execute=True):
            response = APIClient().post('/api/v1/public/leads/', {'name': 'Visitor', 'phone': '9876543299'},
                                        format='json', HTTP_X_API_KEY=self.source.api_key)
        self.assertEqual(response.status_code, 202)
        self.assertTrue(Lead.objects.filter(pk=response.data['lead_id']).exists())

    @override_settings(WEBSITE_LEAD_PUSH_ENABLED=False)
    def test_disabled_delivery_preserves_intake(self):
        self.create()
        self.mock.assert_not_called()
