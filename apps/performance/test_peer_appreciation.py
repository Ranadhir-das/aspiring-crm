import json
from django.core.exceptions import ValidationError as DjangoValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient
from apps.accounts.models import User
from apps.performance.models import PeerAppreciation
from apps.performance.services import (
    get_current_appreciation_period,
    get_eligible_peers,
    get_peer_appreciation_summary,
    get_peer_review_status,
    record_peer_appreciation,
)


class PeerAppreciationTests(TestCase):
    def setUp(self):
        self.caller1 = User.objects.create_user('alice', role=User.Role.CALLER, is_active=True, first_name='Alice')
        self.caller2 = User.objects.create_user('bob', role=User.Role.CALLER, is_active=True, first_name='Bob')
        self.caller3 = User.objects.create_user('charlie', role=User.Role.CALLER, is_active=True, first_name='Charlie')
        self.admin = User.objects.create_user('admin', role=User.Role.ADMIN, is_active=True)
        self.inactive_caller = User.objects.create_user('inactive', role=User.Role.CALLER, is_active=False)
        self.client = APIClient()
        self.url_submit = reverse('peer-appreciation')
        self.url_status = reverse('peer-appreciation-status')
        self.current_month = get_current_appreciation_period()

    def test_self_review_rejected(self):
        # Service level
        with self.assertRaises(DjangoValidationError) as ctx:
            record_peer_appreciation(reviewer=self.caller1, employee_id=self.caller1.pk, score=8)
        self.assertIn('You cannot review yourself.', str(ctx.exception))

        # API level
        self.client.force_authenticate(self.caller1)
        res = self.client.post(self.url_submit, {'employee': self.caller1.pk, 'score': 8}, format='json')
        self.assertEqual(res.status_code, 400)
        self.assertIn('You cannot review yourself.', str(res.data))

    def test_score_range_validation(self):
        self.client.force_authenticate(self.caller1)

        # Less than 1
        res = self.client.post(self.url_submit, {'employee': self.caller2.pk, 'score': 0}, format='json')
        self.assertEqual(res.status_code, 400)
        self.assertIn('Score must be between 1 and 10.', str(res.data))

        # Greater than 10
        res = self.client.post(self.url_submit, {'employee': self.caller2.pk, 'score': 11}, format='json')
        self.assertEqual(res.status_code, 400)
        self.assertIn('Score must be between 1 and 10.', str(res.data))

        # Non-integer float
        res = self.client.post(self.url_submit, {'employee': self.caller2.pk, 'score': 7.5}, format='json')
        self.assertEqual(res.status_code, 400)
        self.assertIn('Score must be between 1 and 10.', str(res.data))

    def test_invalid_month_format(self):
        self.client.force_authenticate(self.caller1)
        res = self.client.post(self.url_submit, {'employee': self.caller2.pk, 'score': 8, 'month': '2026-1'}, format='json')
        self.assertEqual(res.status_code, 400)
        self.assertIn('Invalid review month.', str(res.data))

        res = self.client.post(self.url_submit, {'employee': self.caller2.pk, 'score': 8, 'month': 'invalid-date'}, format='json')
        self.assertEqual(res.status_code, 400)
        self.assertIn('Invalid review month.', str(res.data))

    def test_non_eligible_target_rejected(self):
        self.client.force_authenticate(self.caller1)

        # Inactive caller
        res = self.client.post(self.url_submit, {'employee': self.inactive_caller.pk, 'score': 8}, format='json')
        self.assertEqual(res.status_code, 400)
        self.assertIn('This employee is not eligible for peer appreciation.', str(res.data))

        # Admin user (not a peer for caller)
        res = self.client.post(self.url_submit, {'employee': self.admin.pk, 'score': 8}, format='json')
        self.assertEqual(res.status_code, 400)
        self.assertIn('This employee is not eligible for peer appreciation.', str(res.data))

        # Non-existent user
        res = self.client.post(self.url_submit, {'employee': 99999, 'score': 8}, format='json')
        self.assertEqual(res.status_code, 400)
        self.assertIn('This employee is not eligible for peer appreciation.', str(res.data))

    def test_successful_review_and_duplicate_rejection(self):
        self.client.force_authenticate(self.caller1)

        # First review succeeds
        res = self.client.post(self.url_submit, {'employee': self.caller2.pk, 'score': 9}, format='json')
        self.assertEqual(res.status_code, 201)
        self.assertEqual(res.data['score'], 9)
        self.assertEqual(res.data['employee'], self.caller2.pk)
        self.assertEqual(res.data['reviewer'], self.caller1.pk)

        # Duplicate review for same employee and month rejected
        res2 = self.client.post(self.url_submit, {'employee': self.caller2.pk, 'score': 10}, format='json')
        self.assertEqual(res2.status_code, 400)
        self.assertIn('This employee has already been reviewed for this month.', str(res2.data))

    def test_sequential_reviews_next_employee(self):
        self.client.force_authenticate(self.caller1)

        # Review first employee (caller2)
        res1 = self.client.post(self.url_submit, {'employee': self.caller2.pk, 'score': 7}, format='json')
        self.assertEqual(res1.status_code, 201)

        # Status check after first review
        status_res = self.client.get(self.url_status)
        self.assertEqual(status_res.status_code, 200)
        self.assertFalse(status_res.data['is_complete'])
        self.assertEqual(status_res.data['completed_count'], 1)
        self.assertEqual(status_res.data['remaining_count'], 1)
        self.assertEqual(len(status_res.data['pending_employees']), 1)
        self.assertEqual(status_res.data['pending_employees'][0]['id'], self.caller3.pk)

        # Review second employee (caller3)
        res2 = self.client.post(self.url_submit, {'employee': self.caller3.pk, 'score': 8}, format='json')
        self.assertEqual(res2.status_code, 201)

        # Status check after all reviews completed
        status_res2 = self.client.get(self.url_status)
        self.assertEqual(status_res2.status_code, 200)
        self.assertTrue(status_res2.data['is_complete'])
        self.assertEqual(status_res2.data['completed_count'], 2)
        self.assertEqual(status_res2.data['remaining_count'], 0)
        self.assertEqual(len(status_res2.data['pending_employees']), 0)

    def test_json_string_and_double_stringified_payload_handling(self):
        self.client.force_authenticate(self.caller1)

        # Payload sent as raw JSON string (single stringify)
        raw_json = json.dumps({'employee': self.caller2.pk, 'score': 8, 'month': self.current_month})
        res = self.client.post(
            self.url_submit,
            data=raw_json,
            content_type='application/json'
        )
        self.assertEqual(res.status_code, 201)
        self.assertEqual(res.data['score'], 8)

        # Next employee with double-stringified JSON (simulate earlier bug)
        double_encoded = json.dumps(json.dumps({'employee': self.caller3.pk, 'score': 9, 'month': self.current_month}))
        res2 = self.client.post(
            self.url_submit,
            data=double_encoded,
            content_type='application/json'
        )
        self.assertEqual(res2.status_code, 201)
        self.assertEqual(res2.data['score'], 9)

    def test_get_peer_appreciation_summary(self):
        # Caller2 and Caller3 rate Caller1
        record_peer_appreciation(reviewer=self.caller2, employee_id=self.caller1.pk, score=8, month=self.current_month)
        record_peer_appreciation(reviewer=self.caller3, employee_id=self.caller1.pk, score=10, month=self.current_month)

        summary = get_peer_appreciation_summary(self.caller1.pk, month=self.current_month)
        self.assertEqual(summary['review_count'], 2)
        self.assertEqual(summary['average_score'], 9.0)

        # GET summary via API
        self.client.force_authenticate(self.caller1)
        res = self.client.get(self.url_submit)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data['review_count'], 2)
        self.assertEqual(res.data['average_score'], 9.0)

    def test_authoritative_review_period_previous_completed_month(self):
        from datetime import date
        # October 1, 2026 -> review period = 2026-09
        self.assertEqual(get_current_appreciation_period(date(2026, 10, 1)), '2026-09')

        # October 31, 2026 -> review period = 2026-09
        self.assertEqual(get_current_appreciation_period(date(2026, 10, 31)), '2026-09')

        # November 1, 2026 -> review period = 2026-10
        self.assertEqual(get_current_appreciation_period(date(2026, 11, 1)), '2026-10')

        # Year boundary: January 15, 2027 -> review period = 2026-12
        self.assertEqual(get_current_appreciation_period(date(2027, 1, 15)), '2026-12')

    def test_status_returns_authoritative_month_for_all_employees(self):
        self.client.force_authenticate(self.caller1)
        res = self.client.get(self.url_status)
        self.assertEqual(res.status_code, 200)
        expected_period = get_current_appreciation_period()
        self.assertEqual(res.data['month'], expected_period)

        # First employee submitted using backend authoritative month
        emp1 = res.data['pending_employees'][0]
        sub1 = self.client.post(self.url_submit, {'employee': emp1['id'], 'score': 8, 'month': res.data['month']}, format='json')
        self.assertEqual(sub1.status_code, 201)
        self.assertEqual(sub1.data['month'], expected_period)

        # Status checked again; month remains authoritative and consistent
        res2 = self.client.get(self.url_status)
        self.assertEqual(res2.data['month'], expected_period)
        self.assertEqual(res2.data['completed_count'], 1)

        # Next employee submitted using same month
        emp2 = res2.data['pending_employees'][0]
        sub2 = self.client.post(self.url_submit, {'employee': emp2['id'], 'score': 9, 'month': res2.data['month']}, format='json')
        self.assertEqual(sub2.status_code, 201)
        self.assertEqual(sub2.data['month'], expected_period)

