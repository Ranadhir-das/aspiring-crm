from django.test import TestCase
from unittest.mock import patch
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.leads.models import Lead, LeadAssignmentHistory


class QuickCaptureBackendTests(TestCase):
    def test_batch_malformed_rows_return_partial_results(self):
        response = self.client.post('/api/v1/mobile/counselor/leads/batch-create/', {
            'leads': [None, 'invalid', {'phone': '9876500011'}],
        }, format='json')
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data['created_count'], 1)
        self.assertEqual(response.data['failed_count'], 2)

    def test_batch_invalid_row_assignment_is_not_silently_unassigned(self):
        response = self.client.post('/api/v1/mobile/counselor/leads/batch-create/', {
            'leads': [{'phone': '9876500012', 'assigned_caller_id': self.other_counselor.pk}],
        }, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertFalse(Lead.objects.filter(phone='9876500012').exists())

    def test_batch_repeated_phone_creates_once(self):
        response = self.client.post('/api/v1/mobile/counselor/leads/batch-create/', {
            'leads': [{'phone': '9876500013'}, {'phone': '+91 98765 00013'}],
        }, format='json')
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data['created_count'], 1)
        self.assertEqual(response.data['failed_count'], 1)

    def test_batch_rollback_sends_no_assignment_notification(self):
        with patch('apps.leads.api.counselor_views.LeadAssignmentHistory.objects.create',
                   side_effect=RuntimeError('synthetic database failure')):
            with patch('apps.accounts.push_notifications.notify_caller_about_assigned_leads') as notify:
                with self.captureOnCommitCallbacks(execute=True):
                    with self.assertRaises(RuntimeError):
                        self.client.post('/api/v1/mobile/counselor/leads/batch-create/', {
                            'assigned_caller_id': self.caller.pk,
                            'leads': [{'phone': '9876500014'}],
                        }, format='json')
                notify.assert_not_called()
        self.assertFalse(Lead.objects.filter(phone='9876500014').exists())

    def test_batch_assignment_notifies_only_after_commit(self):
        with patch('apps.accounts.push_notifications.notify_caller_about_assigned_leads') as notify:
            with self.captureOnCommitCallbacks(execute=True):
                response = self.client.post('/api/v1/mobile/counselor/leads/batch-create/', {
                    'assigned_caller_id': self.caller.pk,
                    'leads': [{'phone': '9876500015'}],
                }, format='json')
                self.assertEqual(response.status_code, 201)
                notify.assert_not_called()
            notify.assert_called_once()

    def setUp(self):
        self.counselor = User.objects.create_user(
            username="counselor_qc",
            role=User.Role.COUNSELOR,
            is_active=True,
        )
        self.other_counselor = User.objects.create_user(
            username="other_counselor",
            role=User.Role.COUNSELOR,
            is_active=True,
        )
        self.caller = User.objects.create_user(
            username="caller_qc",
            role=User.Role.CALLER,
            is_active=True,
            first_name="Active",
            last_name="Caller",
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.counselor)

    def test_create_lead_without_caller_unassigned(self):
        url = "/api/v1/mobile/counselor/leads/create/"
        payload = {
            "phone": "9876543210",
            "name": "Jane Doe",
            "preferred_course": "MBBS",
        }
        resp = self.client.post(url, payload, format="json")
        self.assertEqual(resp.status_code, 201)
        data = resp.json()
        self.assertTrue(data["success"])
        self.assertIsNone(data["lead"]["assigned_caller_id"])
        self.assertIsNone(data["lead"]["assigned_caller_name"])

        lead = Lead.objects.get(pk=data["lead"]["id"])
        self.assertIsNone(lead.assigned_caller)
        self.assertIsNone(lead.assigned_at)
        self.assertEqual(lead.name, "Jane Doe")
        self.assertEqual(lead.phone, "9876543210")

        history = LeadAssignmentHistory.objects.filter(lead=lead).first()
        self.assertIsNotNone(history)
        self.assertEqual(history.reason, "Created by counselor")
        self.assertEqual(history.assigned_by, self.counselor)

        # Confirm lead is visible in counselor's created leads list
        list_resp = self.client.get("/api/v1/mobile/counselor/created-leads/")
        self.assertEqual(list_resp.status_code, 200)
        lead_ids = [l["id"] for l in list_resp.json()["leads"]]
        self.assertIn(lead.pk, lead_ids)

        # Confirm counselor can reassign this unassigned lead later
        reassign_url = f"/api/v1/mobile/counselor/created-leads/{lead.pk}/reassign/"
        reassign_resp = self.client.post(reassign_url, {"caller_id": self.caller.pk}, format="json")
        self.assertEqual(reassign_resp.status_code, 200)
        lead.refresh_from_db()
        self.assertEqual(lead.assigned_caller, self.caller)

    def test_check_duplicates_endpoint(self):
        # Create an existing lead by someone else
        existing_other = Lead.objects.create(
            name="Existing Secret",
            phone="9123456789",
            status=Lead.Status.PENDING,
            assigned_caller=self.caller,
        )

        # Create an existing lead by this counselor
        my_lead = Lead.objects.create(
            name="My Prior Lead",
            phone="9392130350",
            status=Lead.Status.PENDING,
        )
        LeadAssignmentHistory.objects.create(
            lead=my_lead,
            assigned_by=self.counselor,
            reason="Created by counselor",
        )

        url = "/api/v1/mobile/counselor/leads/check-duplicates/"
        payload = {
            "phones": ["9123456789", "9392130350", "9876543219"],
        }
        resp = self.client.post(url, payload, format="json")
        self.assertEqual(resp.status_code, 200)
        results = resp.json()["results"]

        # Other's lead: exists is true, but can_view is false, lead_id, name, and assigned_caller_name are None
        self.assertTrue(results["9123456789"]["exists"])
        self.assertFalse(results["9123456789"]["can_view"])
        self.assertIsNone(results["9123456789"]["lead_id"])
        self.assertIsNone(results["9123456789"]["name"])
        self.assertIsNone(results["9123456789"]["assigned_caller_name"])

        # My lead: exists is true, can_view is true, lead_id, name, and details are present
        self.assertTrue(results["9392130350"]["exists"])
        self.assertTrue(results["9392130350"]["can_view"])
        self.assertEqual(results["9392130350"]["lead_id"], my_lead.pk)
        self.assertEqual(results["9392130350"]["name"], "My Prior Lead")

        # Nonexistent phone
        self.assertFalse(results["9876543219"]["exists"])

    def test_batch_create_leads_privacy_and_partial_success(self):
        # Existing unauthorized lead
        Lead.objects.create(name="Top Secret Student", phone="9998887776")

        # Existing authorized lead created by this counselor
        my_dup = Lead.objects.create(name="My Former Lead", phone="9998887777")
        LeadAssignmentHistory.objects.create(
            lead=my_dup,
            assigned_by=self.counselor,
            reason="Created by counselor",
        )

        url = "/api/v1/mobile/counselor/leads/batch-create/"
        payload = {
            "preferred_course": "BTECH",
            "assigned_caller_id": self.caller.pk,
            "leads": [
                {"phone": "9876500001", "name": "Lead One"},
                {"phone": "9876500002", "name": ""}, # blank name -> Student
                {"phone": "9998887776", "name": "Duplicate Unauthorized"},
                {"phone": "9998887777", "name": "Duplicate Authorized"},
            ],
        }
        resp = self.client.post(url, payload, format="json")
        self.assertEqual(resp.status_code, 201)
        data = resp.json()
        self.assertTrue(data["success"])
        self.assertEqual(data["total"], 4)
        self.assertEqual(data["created_count"], 2)
        self.assertEqual(data["failed_count"], 2)

        # Check partial success: valid non-duplicate leads were created
        lead1 = Lead.objects.get(phone="9876500001")
        self.assertEqual(lead1.name, "Lead One")
        self.assertEqual(lead1.preferred_course, "BTECH")
        self.assertEqual(lead1.assigned_caller, self.caller)

        lead2 = Lead.objects.get(phone="9876500002")
        self.assertEqual(lead2.name, "Student")
        self.assertEqual(lead2.preferred_course, "BTECH")
        self.assertEqual(lead2.assigned_caller, self.caller)

        # Check privacy on failed items
        results_by_phone = {r["phone"]: r for r in data["results"]}

        # Unauthorized duplicate: strictly generic error, no private name or caller
        unauth_result = results_by_phone["9998887776"]
        self.assertFalse(unauth_result["success"])
        self.assertEqual(unauth_result["error"], "A lead with this phone number already exists.")
        self.assertNotIn("Top Secret", unauth_result["error"])

        # Authorized duplicate: detailed error message allowed
        auth_result = results_by_phone["9998887777"]
        self.assertFalse(auth_result["success"])
        self.assertIn("A lead with this phone number already exists: My Former Lead", auth_result["error"])

    def test_single_create_privacy(self):
        # Unauthorized lead
        Lead.objects.create(name="Classified Student", phone="9988776655")

        resp = self.client.post("/api/v1/mobile/counselor/leads/create/", {
            "phone": "9988776655",
            "name": "Attempt",
        }, format="json")
        self.assertEqual(resp.status_code, 400)
        phone_err = resp.json().get("phone")
        self.assertIn("A lead with this phone number already exists.", phone_err)
        self.assertNotIn("Classified Student", str(phone_err))

    def test_non_counselor_forbidden(self):
        self.client.force_authenticate(user=self.caller)
        resp1 = self.client.post("/api/v1/mobile/counselor/leads/batch-create/", {"leads": []})
        self.assertEqual(resp1.status_code, 403)

        resp2 = self.client.post("/api/v1/mobile/counselor/leads/check-duplicates/", {"phones": []})
        self.assertEqual(resp2.status_code, 403)
