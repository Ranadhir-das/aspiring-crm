from django.test import TestCase
from django.core.exceptions import PermissionDenied, ValidationError
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.activity.models import ActivityLog
from apps.leads.models import Lead, WhatsAppActivity, WhatsAppTemplate
from apps.leads.whatsapp_service import (
    build_whatsapp_urls,
    normalize_phone_for_whatsapp,
    record_whatsapp_initiated,
    user_can_access_lead,
)


class WhatsAppIntegrationTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.admin = User.objects.create_user(
            username="testadmin",
            password="password123",
            role=User.Role.ADMIN,
        )
        self.caller1 = User.objects.create_user(
            username="caller1",
            password="password123",
            role=User.Role.CALLER,
        )
        self.caller2 = User.objects.create_user(
            username="caller2",
            password="password123",
            role=User.Role.CALLER,
        )

        self.lead1 = Lead.objects.create(
            name="Rahul Sharma",
            phone="9876543210",
            assigned_caller=self.caller1,
        )
        self.lead2 = Lead.objects.create(
            name="Priya Patel",
            phone="+91-91234-56789",
            assigned_caller=self.caller2,
        )

        self.active_template = WhatsAppTemplate.objects.create(
            title="Follow-up Inquiry",
            message="Hello {name}, following up on your application.",
            is_active=True,
            created_by=self.admin,
        )
        self.inactive_template = WhatsAppTemplate.objects.create(
            title="Archived Template",
            message="Old message",
            is_active=False,
            created_by=self.admin,
        )

    def test_phone_normalization_standard(self):
        # 10 digits Indian -> 919876543210
        self.assertEqual(normalize_phone_for_whatsapp("9876543210"), "919876543210")
        # With country code and formatting
        self.assertEqual(normalize_phone_for_whatsapp("+91-98765-43210"), "919876543210")
        # 11 digits with leading 0
        self.assertEqual(normalize_phone_for_whatsapp("09876543210"), "919876543210")
        # International numbers
        self.assertEqual(normalize_phone_for_whatsapp("+14155552671"), "14155552671")
        # Empty or too short raises ValidationError
        with self.assertRaises(ValidationError):
            normalize_phone_for_whatsapp("")
        with self.assertRaises(ValidationError):
            normalize_phone_for_whatsapp("12345")

    def test_url_construction(self):
        urls = build_whatsapp_urls("9876543210", "Hello Rahul!")
        self.assertEqual(urls["phone"], "919876543210")
        self.assertIn("whatsapp://send?phone=919876543210&text=Hello%20Rahul%21", urls["deep_link"])
        self.assertIn("https://wa.me/919876543210?text=Hello%20Rahul%21", urls["web_link"])

    def test_template_list_api_active_only(self):
        self.client.force_authenticate(user=self.caller1)
        res = self.client.get("/api/v1/leads/whatsapp/templates/")
        self.assertEqual(res.status_code, 200)
        titles = [t["title"] for t in res.json()]
        self.assertIn("Follow-up Inquiry", titles)
        self.assertNotIn("Archived Template", titles)

    def test_authorized_caller_can_initiate_whatsapp(self):
        self.client.force_authenticate(user=self.caller1)
        res = self.client.post(
            f"/api/v1/leads/{self.lead1.pk}/whatsapp/initiate/",
            {"template_id": self.active_template.pk, "source": "CALLER"},
            format="json",
        )
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["status"], "ok")
        self.assertEqual(data["phone"], "919876543210")
        self.assertIn("Hello Rahul Sharma", data["message"])
        self.assertIn("whatsapp://send?", data["deep_link"])

        # Verify WhatsAppActivity created
        activity = WhatsAppActivity.objects.filter(lead=self.lead1, user=self.caller1).first()
        self.assertIsNotNone(activity)
        self.assertEqual(activity.source, "CALLER")
        self.assertEqual(activity.template_name, "Follow-up Inquiry")

        # Verify ActivityLog created with WHATSAPP_INITIATED
        log = ActivityLog.objects.filter(lead=self.lead1, verb=ActivityLog.Verb.WHATSAPP_INITIATED).first()
        self.assertIsNotNone(log)
        self.assertIn("WhatsApp opened", log.description)
        self.assertEqual(log.actor, self.caller1)

        # Verify NO WHATSAPP_SENT log exists
        sent_logs = ActivityLog.objects.filter(verb__iexact="WHATSAPP_SENT")
        self.assertEqual(sent_logs.count(), 0)

    def test_unauthorized_caller_cannot_initiate_whatsapp(self):
        # Caller2 attempts to access Caller1's lead
        self.client.force_authenticate(user=self.caller2)
        res = self.client.post(
            f"/api/v1/leads/{self.lead1.pk}/whatsapp/initiate/",
            {"template_id": self.active_template.pk},
            format="json",
        )
        self.assertEqual(res.status_code, 403)
        # Ensure no activity was recorded
        self.assertFalse(WhatsAppActivity.objects.filter(lead=self.lead1, user=self.caller2).exists())

    def test_admin_can_initiate_whatsapp_on_any_lead(self):
        self.client.force_authenticate(user=self.admin)
        res = self.client.post(
            f"/api/v1/leads/{self.lead1.pk}/whatsapp/initiate/",
            {"message": "Custom hello from admin", "source": "CRM"},
            format="json",
        )
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["message"], "Custom hello from admin")
        self.assertEqual(data["template_name"], "Custom")
        self.assertEqual(data["source"], "CRM")

        # Verify ActivityLog
        log = ActivityLog.objects.filter(lead=self.lead1, actor=self.admin).first()
        self.assertIsNotNone(log)
        self.assertEqual(log.verb, ActivityLog.Verb.WHATSAPP_INITIATED)
        self.assertIn("CRM Web", log.description)

    def test_inactive_template_rejected(self):
        self.client.force_authenticate(user=self.caller1)
        res = self.client.post(
            f"/api/v1/leads/{self.lead1.pk}/whatsapp/initiate/",
            {"template_id": self.inactive_template.pk},
            format="json",
        )
        self.assertEqual(res.status_code, 400)

    def test_custom_message_works(self):
        self.client.force_authenticate(user=self.caller1)
        custom_txt = "Hi Rahul, sharing the requested brochure link now."
        res = self.client.post(
            f"/api/v1/leads/{self.lead1.pk}/whatsapp/initiate/",
            {"message": custom_txt},
            format="json",
        )
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["message"], custom_txt)
        self.assertIn("Hi%20Rahul", data["deep_link"])

    def test_lead_detail_page_whatsapp_modal_and_valid_script(self):
        from django.test import Client as DjangoClient
        web_client = DjangoClient()
        web_client.force_login(self.admin)
        res = web_client.get(f"/leads/{self.lead1.pk}/")
        self.assertEqual(res.status_code, 200)
        html = res.content.decode()

        # WhatsApp button & modal markup
        self.assertIn('id="open-wa-btn"', html)
        self.assertIn('onclick="openWhatsAppModal()"', html)
        self.assertIn('id="wa-modal"', html)
        self.assertIn('id="wa-template-select"', html)
        self.assertIn('id="wa-message-body"', html)
        self.assertIn('id="wa-submit-btn"', html)
        self.assertIn('onclick="submitWhatsAppInitiate()"', html)

        # Template options rendered
        self.assertIn(self.active_template.title, html)

        # Valid script structure: JSON script tag outside executable script
        self.assertIn('<script id="wa-lead-name" type="application/json">', html)
        self.assertIn('<script>\nfunction openWhatsAppModal()', html)

        # Ensure NO nested script tags or broken visible JavaScript code
        self.assertNotIn('<script>\nconst leadName = <script', html)
        self.assertNotIn(';function openWhatsAppModal()', html)
        self.assertNotIn('const leadName = <script', html)

    def test_web_session_client_can_initiate_whatsapp(self):
        from django.test import Client as DjangoClient
        web_client = DjangoClient()
        web_client.force_login(self.admin)
        res = web_client.post(
            f"/api/v1/leads/{self.lead1.pk}/whatsapp/initiate/",
            {"template_id": self.active_template.pk, "source": "CRM"},
            content_type="application/json",
        )
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["status"], "ok")
        self.assertEqual(data["source"], "CRM")
        self.assertIn("Hello Rahul Sharma", data["message"])
        self.assertIn("https://wa.me/919876543210", data["web_link"])


