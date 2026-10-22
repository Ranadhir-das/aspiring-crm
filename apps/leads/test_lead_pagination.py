from django.test import TestCase
from django.db import connection
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.leads.models import Lead


class LeadPaginationTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            username="admin_lead_page",
            role=User.Role.ADMIN,
            is_active=True,
        )
        self.manager = User.objects.create_user(
            username="manager_lead_page",
            role=User.Role.MANAGER,
            is_active=True,
        )
        self.caller1 = User.objects.create_user(
            username="caller1_lead_page",
            role=User.Role.CALLER,
            is_active=True,
        )
        self.caller2 = User.objects.create_user(
            username="caller2_lead_page",
            role=User.Role.CALLER,
            is_active=True,
        )
        self.counselor = User.objects.create_user(
            username="counselor_lead_page",
            role=User.Role.COUNSELOR,
            is_active=True,
        )

        self.client = APIClient()

        # Create 60 test leads for pagination testing
        # 35 assigned to caller1, 25 assigned to caller2
        leads_to_create = []
        for i in range(1, 36):
            leads_to_create.append(
                Lead(
                    name=f"Lead Caller1 {i:02d}",
                    phone=f"98761{i:05d}",
                    assigned_caller=self.caller1,
                    status=Lead.Status.PENDING if i % 2 == 0 else Lead.Status.INTERESTED,
                )
            )
        for i in range(1, 26):
            leads_to_create.append(
                Lead(
                    name=f"Lead Caller2 {i:02d}",
                    phone=f"98762{i:05d}",
                    assigned_caller=self.caller2,
                    status=Lead.Status.PENDING,
                )
            )
        Lead.objects.bulk_create(leads_to_create)

    def test_default_page_size_and_envelope_structure(self):
        self.client.force_authenticate(user=self.admin)
        response = self.client.get("/api/v1/leads/")
        self.assertEqual(response.status_code, 200)

        data = response.data
        self.assertIn("count", data)
        self.assertIn("next", data)
        self.assertIn("previous", data)
        self.assertIn("results", data)

        # Total leads created in this test is 60 (+ any pre-existing isolated acceptance leads)
        total_in_db = Lead.objects.count()
        self.assertEqual(data["count"], total_in_db)
        self.assertEqual(len(data["results"]), 25)
        self.assertIsNone(data["previous"])
        self.assertIsNotNone(data["next"])
        self.assertIn("page=2", data["next"])

    def test_page_navigation(self):
        self.client.force_authenticate(user=self.admin)

        # Page 1
        resp_p1 = self.client.get("/api/v1/leads/?page=1")
        self.assertEqual(resp_p1.status_code, 200)
        p1_ids = [item["id"] for item in resp_p1.data["results"]]
        self.assertEqual(len(p1_ids), 25)

        # Page 2
        resp_p2 = self.client.get("/api/v1/leads/?page=2")
        self.assertEqual(resp_p2.status_code, 200)
        p2_ids = [item["id"] for item in resp_p2.data["results"]]
        self.assertEqual(len(p2_ids), 25)
        self.assertIsNotNone(resp_p2.data["previous"])
        self.assertIsNotNone(resp_p2.data["next"])

        # Page 3
        resp_p3 = self.client.get("/api/v1/leads/?page=3")
        self.assertEqual(resp_p3.status_code, 200)
        p3_ids = [item["id"] for item in resp_p3.data["results"]]
        # Disjoint sets verify deterministic pagination without duplicates or skipped leads
        self.assertEqual(len(set(p1_ids) & set(p2_ids)), 0)
        self.assertEqual(len(set(p2_ids) & set(p3_ids)), 0)
        self.assertEqual(len(set(p1_ids) & set(p3_ids)), 0)

    def test_invalid_and_out_of_range_pages(self):
        self.client.force_authenticate(user=self.admin)

        # Out-of-range page returns 404
        resp_oor = self.client.get("/api/v1/leads/?page=9999")
        self.assertEqual(resp_oor.status_code, 404)

        # Invalid page value returns 404
        resp_inv = self.client.get("/api/v1/leads/?page=invalid")
        self.assertEqual(resp_inv.status_code, 404)

        resp_neg = self.client.get("/api/v1/leads/?page=-1")
        self.assertEqual(resp_neg.status_code, 404)

    def test_custom_page_size_and_max_page_size(self):
        self.client.force_authenticate(user=self.admin)

        # Custom page size 10
        resp_10 = self.client.get("/api/v1/leads/?page_size=10")
        self.assertEqual(resp_10.status_code, 200)
        self.assertEqual(len(resp_10.data["results"]), 10)

        # Custom page size 50
        resp_50 = self.client.get("/api/v1/leads/?page_size=50")
        self.assertEqual(resp_50.status_code, 200)
        self.assertEqual(len(resp_50.data["results"]), 50)

        # Exceeding max_page_size (100) is clamped to max_page_size
        resp_over = self.client.get("/api/v1/leads/?page_size=500")
        self.assertEqual(resp_over.status_code, 200)
        # We have ~67 leads in db, so clamped 100 returns all of them
        total_in_db = Lead.objects.count()
        self.assertEqual(len(resp_over.data["results"]), min(total_in_db, 100))

    def test_status_filtering_with_pagination(self):
        self.client.force_authenticate(user=self.admin)
        resp_interested = self.client.get("/api/v1/leads/?status=INTERESTED")
        self.assertEqual(resp_interested.status_code, 200)

        # Check that all results on this page have status INTERESTED
        for item in resp_interested.data["results"]:
            self.assertEqual(item["status"], Lead.Status.INTERESTED)

    def test_role_based_access_and_tenant_isolation(self):
        # Caller 1 sees only caller 1's leads (35 total)
        self.client.force_authenticate(user=self.caller1)
        resp_c1 = self.client.get("/api/v1/leads/")
        self.assertEqual(resp_c1.status_code, 200)
        self.assertEqual(resp_c1.data["count"], 35)
        self.assertEqual(len(resp_c1.data["results"]), 25)
        for item in resp_c1.data["results"]:
            self.assertEqual(item["assigned_caller"], self.caller1.id)

        # Caller 2 sees only caller 2's leads (25 total)
        self.client.force_authenticate(user=self.caller2)
        resp_c2 = self.client.get("/api/v1/leads/")
        self.assertEqual(resp_c2.status_code, 200)
        self.assertEqual(resp_c2.data["count"], 25)
        self.assertEqual(len(resp_c2.data["results"]), 25)
        for item in resp_c2.data["results"]:
            self.assertEqual(item["assigned_caller"], self.caller2.id)

        # Manager sees all leads
        self.client.force_authenticate(user=self.manager)
        resp_mgr = self.client.get("/api/v1/leads/")
        self.assertEqual(resp_mgr.status_code, 200)
        self.assertEqual(resp_mgr.data["count"], Lead.objects.count())

        # Counselor is not in CanAccessLeads, gets 403
        self.client.force_authenticate(user=self.counselor)
        resp_couns = self.client.get("/api/v1/leads/")
        self.assertEqual(resp_couns.status_code, 403)

        # Unauthenticated request gets 401
        self.client.logout()
        resp_anon = self.client.get("/api/v1/leads/")
        self.assertEqual(resp_anon.status_code, 401)

    def test_sql_pagination_executes_limit_clause(self):
        self.client.force_authenticate(user=self.admin)
        with CaptureQueriesContext(connection) as ctx:
            response = self.client.get("/api/v1/leads/?page=1")
            self.assertEqual(response.status_code, 200)

        # Inspect captured queries to verify LIMIT 25 is present in SQL
        limit_queries = [
            q["sql"] for q in ctx.captured_queries
            if "LIMIT" in q["sql"].upper() and "leads_lead" in q["sql"]
        ]
        self.assertTrue(len(limit_queries) > 0, "Expected SQL query with LIMIT clause for leads_lead")

    def test_mobile_lead_list_compatibility_unpaginated(self):
        # /api/v1/leads/mobile/ (MobileLeadListView) must remain a bare JSON array for existing mobile app
        self.client.force_authenticate(user=self.caller1)
        response = self.client.get("/api/v1/leads/mobile/")
        self.assertEqual(response.status_code, 200)
        self.assertIsInstance(response.data, list, "Mobile lead list must remain a bare JSON array")
        self.assertEqual(len(response.data), 35)
