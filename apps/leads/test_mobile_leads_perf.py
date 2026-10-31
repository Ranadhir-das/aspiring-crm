from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient
from apps.accounts.models import User
from apps.leads.models import Lead
from apps.calls.models import Call


class MobileLeadPerfAndSyncTestCase(TestCase):
    def setUp(self):
        self.caller = User.objects.create_user(
            username="caller_perf_test",
            email="caller_perf_test@example.com",
            role=User.Role.CALLER,
            password="testpassword123",
        )
        # Create 15 assigned leads with calls
        self.leads = []
        for i in range(1, 16):
            lead = Lead.objects.create(
                name=f"Lead {i}",
                phone=f"98765432{i:02d}",
                status=Lead.Status.PENDING,
                assigned_caller=self.caller,
            )
            # Add interested call
            Call.objects.create(
                caller=self.caller,
                lead=lead,
                phone_number=lead.phone,
                outcome=Call.Outcome.INTERESTED,
                selected_course="MBBS",
                duration_seconds=60,
                started_at=timezone.now(),
                ended_at=timezone.now(),
            )
            self.leads.append(lead)

        self.client = APIClient()
        self.client.force_authenticate(user=self.caller)

    def test_mobile_leads_list_query_count_and_headers(self):
        # Without prefetch, 15 leads with calls and points entries would execute > 30 queries.
        # With prefetch_related and select_related, it executes in exactly 4 queries.
        with self.assertNumQueries(4):
            response = self.client.get("/api/v1/mobile/leads/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 15)
        # Verify no-cache headers
        self.assertEqual(response.headers.get("Cache-Control"), "no-cache, no-store, must-revalidate")
        self.assertEqual(response.headers.get("Pragma"), "no-cache")

        # Verify interested_details serialization
        item = response.data[0]
        self.assertIsNotNone(item.get("interested_details"))
        self.assertEqual(item["interested_details"]["selected_course"], "MBBS")

    def test_call_outcome_syncs_lead_status_atomically(self):
        lead = self.leads[0]
        self.assertEqual(lead.status, Lead.Status.PENDING)

        call_data = {
            "client_event_id": "11111111-2222-3333-4444-555555555555",
            "lead": lead.pk,
            "phone_number": lead.phone,
            "started_at": timezone.now().isoformat(),
            "ended_at": timezone.now().isoformat(),
            "duration_seconds": 45,
            "outcome": "BUSY",
            "notes": "Student busy, call back later",
        }
        res = self.client.post("/api/v1/calls/", data=call_data, format="json")
        self.assertIn(res.status_code, [200, 201])

        lead.refresh_from_db()
        self.assertEqual(lead.status, Lead.Status.BUSY)
        self.assertEqual(lead.notes, "Student busy, call back later")
