from io import BytesIO
from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.core.cache.backends.locmem import LocMemCache
from django.test import Client, TestCase
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.leads.api.public_views import PublicLeadBurstThrottle, PublicLeadDailyThrottle
from apps.leads.models import Lead, Service, WebsiteLeadSubmission, WebsiteSource

UserModel = get_user_model()


class WebsiteIntegrationsAdminTests(TestCase):
    def setUp(self):
        cache = LocMemCache(f"website-admin-test-{uuid4()}", {})
        self.addCleanup(cache.clear)
        for cls in (PublicLeadBurstThrottle, PublicLeadDailyThrottle):
            patched = patch.object(cls, "cache", cache)
            patched.start()
            self.addCleanup(patched.stop)

        self.client = Client()
        self.api_client = APIClient()

        # Services
        self.service_apostille, _ = Service.objects.get_or_create(
            code="APOSTILLE", defaults={"name": "Apostille Services", "is_active": True}
        )
        self.service_apostille.is_active = True
        self.service_apostille.save()

        self.service_mbbs, _ = Service.objects.get_or_create(
            code="MBBS", defaults={"name": "MBBS Admissions", "is_active": True}
        )
        self.service_mbbs.is_active = True
        self.service_mbbs.save()

        self.service_inactive, _ = Service.objects.get_or_create(
            code="INACTIVE_SVC", defaults={"name": "Inactive Service", "is_active": False}
        )
        self.service_inactive.is_active = False
        self.service_inactive.save()

        # Users
        self.admin_user = UserModel.objects.create_user(
            username="admin_user",
            email="admin@example.com",
            password="password123",
            role=User.Role.ADMIN,
        )
        self.manager_user = UserModel.objects.create_user(
            username="manager_user",
            email="manager@example.com",
            password="password123",
            role=User.Role.MANAGER,
        )
        self.caller_user = UserModel.objects.create_user(
            username="caller_user",
            email="caller@example.com",
            password="password123",
            role=User.Role.CALLER,
        )

        # Configured website source
        self.source = WebsiteSource.objects.create(
            name="AuthenticAttest",
            code="authentic_attest",
            api_key="ws_test_authentic_attest_secret_key_12345",
            default_service=self.service_apostille,
            allowed_origins=["https://authenticattest.com"],
            is_active=True,
        )
        self.source.allowed_services.set([self.service_apostille])

    # -------------------------------------------------------------
    # 1. PERMISSION TESTS
    # -------------------------------------------------------------
    def test_anonymous_user_redirected_to_login(self):
        response = self.client.get(reverse("web:website-integrations"))
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response.url)

    def test_caller_role_forbidden_from_web_views(self):
        self.client.login(username="caller_user", password="password123")

        # List
        resp = self.client.get(reverse("web:website-integrations"))
        self.assertEqual(resp.status_code, 403)

        # Create
        resp = self.client.get(reverse("web:website-integration-create"))
        self.assertEqual(resp.status_code, 403)

        # Detail
        resp = self.client.get(reverse("web:website-integration-detail", kwargs={"pk": self.source.pk}))
        self.assertEqual(resp.status_code, 403)

        # Edit
        resp = self.client.get(reverse("web:website-integration-edit", kwargs={"pk": self.source.pk}))
        self.assertEqual(resp.status_code, 403)

        # Toggle
        resp = self.client.post(reverse("web:website-integration-toggle-active", kwargs={"pk": self.source.pk}))
        self.assertEqual(resp.status_code, 403)

        # Regenerate key
        resp = self.client.post(reverse("web:website-integration-regenerate-key", kwargs={"pk": self.source.pk}))
        self.assertEqual(resp.status_code, 403)

    def test_manager_and_admin_have_full_access(self):
        for user in [self.manager_user, self.admin_user]:
            self.client.force_login(user)
            resp = self.client.get(reverse("web:website-integrations"))
            self.assertEqual(resp.status_code, 200)

            resp = self.client.get(reverse("web:website-integration-detail", kwargs={"pk": self.source.pk}))
            self.assertEqual(resp.status_code, 200)

    # -------------------------------------------------------------
    # 2. LIST VIEW & MASKED KEY
    # -------------------------------------------------------------
    def test_list_view_renders_sources_with_masked_keys(self):
        self.client.force_login(self.admin_user)
        response = self.client.get(reverse("web:website-integrations"))
        self.assertEqual(response.status_code, 200)

        # Masked key must be present in HTML
        self.assertContains(response, self.source.api_key_masked)
        # Raw API key must NEVER be in list view HTML
        self.assertNotContains(response, self.source.api_key)
        # Website details
        self.assertContains(response, "AuthenticAttest")
        self.assertContains(response, "authentic_attest")
        self.assertContains(response, "https://authenticattest.com")

    # -------------------------------------------------------------
    # 3. ADD WEBSITE FORM & VALIDATION
    # -------------------------------------------------------------
    def test_create_website_source_validation_rejects_wildcard_origin(self):
        self.client.force_login(self.admin_user)
        payload = {
            "name": "Invalid Origin Site",
            "code": "invalid_origin",
            "default_service": self.service_apostille.pk,
            "allowed_services": [self.service_apostille.pk],
            "allowed_origins_raw": "*",
            "is_active": True,
        }
        response = self.client.post(reverse("web:website-integration-create"), data=payload)
        self.assertEqual(response.status_code, 200)
        self.assertFormError(response.context["form"], "allowed_origins_raw", "Wildcard '*' is not permitted as a production origin.")

    def test_create_website_source_validation_rejects_http_and_paths(self):
        self.client.force_login(self.admin_user)
        payload = {
            "name": "HTTP Site",
            "code": "http_site",
            "default_service": self.service_apostille.pk,
            "allowed_services": [self.service_apostille.pk],
            "allowed_origins_raw": "http://insecure.com\nhttps://secure.com/path",
            "is_active": True,
        }
        response = self.client.post(reverse("web:website-integration-create"), data=payload)
        self.assertEqual(response.status_code, 200)
        form_errors = str(response.context["form"].errors)
        self.assertTrue("must use HTTPS" in form_errors or "must not contain paths" in form_errors)

    def test_create_website_source_validation_rejects_duplicate_code(self):
        self.client.force_login(self.admin_user)
        payload = {
            "name": "Duplicate Code Site",
            "code": "AUTHENTIC_ATTEST",  # case-insensitive match with existing
            "default_service": self.service_apostille.pk,
            "allowed_services": [self.service_apostille.pk],
            "allowed_origins_raw": "https://other.com",
            "is_active": True,
        }
        response = self.client.post(reverse("web:website-integration-create"), data=payload)
        self.assertEqual(response.status_code, 200)
        self.assertFormError(response.context["form"], "code", "Website code 'authentic_attest' is already in use.")

    def test_create_website_source_success_and_one_time_key_display(self):
        self.client.force_login(self.admin_user)
        payload = {
            "name": "MBBS Global",
            "code": "mbbs_global",
            "default_service": self.service_mbbs.pk,
            "allowed_services": [self.service_mbbs.pk],
            "allowed_origins_raw": "https://mbbsglobal.edu\nhttps://portal.mbbsglobal.edu",
            "is_active": True,
        }
        response = self.client.post(reverse("web:website-integration-create"), data=payload)
        self.assertEqual(response.status_code, 302)

        new_source = WebsiteSource.objects.get(code="mbbs_global")
        self.assertEqual(new_source.name, "MBBS Global")
        self.assertEqual(new_source.allowed_origins, ["https://mbbsglobal.edu", "https://portal.mbbsglobal.edu"])
        self.assertTrue(new_source.api_key.startswith("ws_"))

        # Redirects to credential display page
        self.assertEqual(response.url, reverse("web:website-integration-credential", kwargs={"pk": new_source.pk}))

        # First visit: raw key is present in HTML!
        cred_response = self.client.get(reverse("web:website-integration-credential", kwargs={"pk": new_source.pk}))
        self.assertEqual(cred_response.status_code, 200)
        self.assertContains(cred_response, new_source.api_key)
        self.assertContains(cred_response, "Save this API key now")

        # Second visit / refresh: key is popped from session, raw key is GONE!
        refresh_response = self.client.get(reverse("web:website-integration-credential", kwargs={"pk": new_source.pk}))
        self.assertEqual(refresh_response.status_code, 200)
        self.assertNotContains(refresh_response, new_source.api_key)
        self.assertContains(refresh_response, new_source.api_key_masked)
        self.assertContains(refresh_response, "API Key Configured")

    # -------------------------------------------------------------
    # 4. DETAIL & EDIT VIEWS
    # -------------------------------------------------------------
    def test_detail_view_shows_masked_key_and_recent_submissions(self):
        self.client.force_login(self.admin_user)

        # Create a lead & submission for this source
        lead = Lead.objects.create(
            name="Rahul Verma",
            phone="919876543210",
            service="APOSTILLE",
            service_type=self.service_apostille,
            source="authentic_attest",
        )
        WebsiteLeadSubmission.objects.create(
            lead=lead,
            website_source=self.source,
            name="Rahul Verma",
            phone="919876543210",
            service="APOSTILLE",
            service_type=self.service_apostille,
            source="authentic_attest",
            campaign="spring2026",
            is_duplicate=False,
            ip_address="192.168.1.50",
        )

        resp = self.client.get(reverse("web:website-integration-detail", kwargs={"pk": self.source.pk}))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, self.source.api_key_masked)
        self.assertNotContains(resp, self.source.api_key)
        self.assertContains(resp, "Rahul Verma")
        self.assertContains(resp, "spring2026")
        self.assertContains(resp, "New Lead")

    def test_edit_preserves_existing_api_key(self):
        self.client.force_login(self.admin_user)
        original_key = self.source.api_key

        payload = {
            "name": "AuthenticAttest Renamed",
            "code": "authentic_attest",
            "default_service": self.service_apostille.pk,
            "allowed_services": [self.service_apostille.pk, self.service_mbbs.pk],
            "allowed_origins_raw": "https://authenticattest.com\nhttps://new.authenticattest.com",
            "is_active": True,
        }
        resp = self.client.post(
            reverse("web:website-integration-edit", kwargs={"pk": self.source.pk}),
            data=payload,
        )
        self.assertEqual(resp.status_code, 302)

        self.source.refresh_from_db()
        self.assertEqual(self.source.name, "AuthenticAttest Renamed")
        self.assertEqual(self.source.api_key, original_key)  # KEY NEVER CHANGED!
        self.assertEqual(len(self.source.allowed_origins), 2)

    # -------------------------------------------------------------
    # 5. TOGGLE ACTIVE & REGENERATE KEY
    # -------------------------------------------------------------
    def test_toggle_active_status(self):
        self.client.force_login(self.admin_user)
        self.assertTrue(self.source.is_active)

        # Toggle to inactive
        resp = self.client.post(reverse("web:website-integration-toggle-active", kwargs={"pk": self.source.pk}))
        self.assertEqual(resp.status_code, 302)
        self.source.refresh_from_db()
        self.assertFalse(self.source.is_active)

        # Inactive blocks public API
        pub_client = APIClient()
        resp_api = pub_client.post(
            "/api/v1/public/leads/",
            {"name": "Blocked Lead", "phone": "9876543211", "service": "APOSTILLE"},
            format="json",
            HTTP_X_API_KEY=self.source.api_key,
        )
        self.assertEqual(resp_api.status_code, 403)

        # Toggle back to active
        self.client.post(reverse("web:website-integration-toggle-active", kwargs={"pk": self.source.pk}))
        self.source.refresh_from_db()
        self.assertTrue(self.source.is_active)

        # Public API now accepts
        resp_api2 = pub_client.post(
            "/api/v1/public/leads/",
            {"name": "Allowed Lead", "phone": "9876543211", "service": "APOSTILLE"},
            format="json",
            HTTP_X_API_KEY=self.source.api_key,
        )
        self.assertEqual(resp_api2.status_code, 202)

    def test_regenerate_api_key_invalidates_old_key(self):
        self.client.force_login(self.admin_user)
        old_key = self.source.api_key

        resp = self.client.post(reverse("web:website-integration-regenerate-key", kwargs={"pk": self.source.pk}))
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.url, reverse("web:website-integration-credential", kwargs={"pk": self.source.pk}))

        self.source.refresh_from_db()
        new_key = self.source.api_key
        self.assertNotEqual(old_key, new_key)
        self.assertTrue(new_key.startswith("ws_"))

        # Credential page displays new key once
        cred_resp = self.client.get(reverse("web:website-integration-credential", kwargs={"pk": self.source.pk}))
        self.assertContains(cred_resp, new_key)

        # Old key fails with 401
        pub_client = APIClient()
        fail_resp = pub_client.post(
            "/api/v1/public/leads/",
            {"name": "Test", "phone": "9876543212", "service": "APOSTILLE"},
            format="json",
            HTTP_X_API_KEY=old_key,
        )
        self.assertEqual(fail_resp.status_code, 401)

        # New key succeeds with 202
        success_resp = pub_client.post(
            "/api/v1/public/leads/",
            {"name": "Test", "phone": "9876543212", "service": "APOSTILLE"},
            format="json",
            HTTP_X_API_KEY=new_key,
        )
        self.assertEqual(success_resp.status_code, 202)

    # -------------------------------------------------------------
    # 6. ORIGIN RESTRICTION & CORS
    # -------------------------------------------------------------
    def test_public_api_enforces_allowed_origins(self):
        pub_client = APIClient()

        # Origin header matching allowed_origins -> accepted
        resp_allowed = pub_client.post(
            "/api/v1/public/leads/",
            {"name": "Browser User", "phone": "9876543215", "service": "APOSTILLE"},
            format="json",
            HTTP_X_API_KEY=self.source.api_key,
            HTTP_ORIGIN="https://authenticattest.com",
        )
        self.assertEqual(resp_allowed.status_code, 202)

        # Origin header NOT in allowed_origins -> rejected (403)
        resp_rejected = pub_client.post(
            "/api/v1/public/leads/",
            {"name": "Bad Origin User", "phone": "9876543216", "service": "APOSTILLE"},
            format="json",
            HTTP_X_API_KEY=self.source.api_key,
            HTTP_ORIGIN="https://unauthorized-domain.com",
        )
        self.assertEqual(resp_rejected.status_code, 403)
        self.assertIn("not permitted", str(resp_rejected.data))

        # No origin header (server-side curl or backend webhook) -> accepted
        resp_server = pub_client.post(
            "/api/v1/public/leads/",
            {"name": "Server Script", "phone": "9876543217", "service": "APOSTILLE"},
            format="json",
            HTTP_X_API_KEY=self.source.api_key,
        )
        self.assertEqual(resp_server.status_code, 202)

    # -------------------------------------------------------------
    # 7. DRF ADMIN APIS
    # -------------------------------------------------------------
    def test_drf_admin_api_list_and_create(self):
        self.api_client.force_authenticate(user=self.admin_user)

        # List
        resp = self.api_client.get("/api/v1/leads/website-sources/")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        # Verify masked key in list response
        self.assertIn(self.source.api_key_masked, [s["api_key_masked"] for s in resp.data["results"] if "api_key_masked" in s] if "results" in resp.data else [s["api_key_masked"] for s in resp.data])

        # Create
        create_payload = {
            "name": "API Created Source",
            "code": "api_created",
            "default_service": self.service_apostille.pk,
            "allowed_services": [self.service_apostille.pk],
            "allowed_origins": ["https://apicreated.com"],
            "is_active": True,
        }
        create_resp = self.api_client.post("/api/v1/leads/website-sources/", create_payload, format="json")
        self.assertEqual(create_resp.status_code, status.HTTP_201_CREATED)
        self.assertIn("api_key", create_resp.data)  # Raw key returned ONCE upon creation
        self.assertTrue(create_resp.data["api_key"].startswith("ws_"))
        self.assertIn("warning", create_resp.data)

        new_id = create_resp.data["id"]

        # Detail GET returns masked key, NOT raw key
        detail_resp = self.api_client.get(f"/api/v1/leads/website-sources/{new_id}/")
        self.assertEqual(detail_resp.status_code, status.HTTP_200_OK)
        self.assertNotIn("api_key", detail_resp.data)
        self.assertIn("api_key_masked", detail_resp.data)

        # Toggle API
        toggle_resp = self.api_client.post(f"/api/v1/leads/website-sources/{new_id}/toggle/")
        self.assertEqual(toggle_resp.status_code, status.HTTP_200_OK)
        self.assertFalse(toggle_resp.data["is_active"])

        # Regenerate API key endpoint returns new key once
        regen_resp = self.api_client.post(f"/api/v1/leads/website-sources/{new_id}/regenerate-key/")
        self.assertEqual(regen_resp.status_code, status.HTTP_200_OK)
        self.assertIn("api_key", regen_resp.data)
        self.assertIn("warning", regen_resp.data)

    def test_drf_admin_api_caller_forbidden(self):
        self.api_client.force_authenticate(user=self.caller_user)
        resp = self.api_client.get("/api/v1/leads/website-sources/")
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)
