import io
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.notifications.models import Notification
from apps.notifications.team_events import notify_notice
from apps.web.models import Notice, NoticeAttachment


class IndividualNoticeTests(TestCase):
    def setUp(self):
        self.web_client = Client()
        self.api_client = APIClient()

        self.admin = User.objects.create_user(
            username="admin_user",
            first_name="Admin",
            last_name="Boss",
            password="password123",
            role=User.Role.ADMIN,
        )
        self.caller1 = User.objects.create_user(
            username="caller_alice",
            first_name="Alice",
            last_name="Caller",
            password="password123",
            role=User.Role.CALLER,
        )
        self.caller2 = User.objects.create_user(
            username="caller_bob",
            first_name="Bob",
            last_name="Caller",
            password="password123",
            role=User.Role.CALLER,
        )
        self.counselor = User.objects.create_user(
            username="counselor_carol",
            first_name="Carol",
            last_name="Counselor",
            password="password123",
            role=User.Role.COUNSELOR,
        )
        self.inactive_user = User.objects.create_user(
            username="inactive_dave",
            first_name="Dave",
            last_name="Inactive",
            password="password123",
            role=User.Role.CALLER,
            is_active=False,
        )

    # 1. Model Visibility Logic
    def test_model_visibility_role_based(self):
        # Role-scoped notice
        notice_caller = Notice.objects.create(
            title="Callers Only",
            body="Caller body",
            roles=[User.Role.CALLER],
            created_by=self.admin,
        )
        self.assertTrue(notice_caller.is_visible_to(self.admin))
        self.assertTrue(notice_caller.is_visible_to(self.caller1))
        self.assertTrue(notice_caller.is_visible_to(self.caller2))
        self.assertFalse(notice_caller.is_visible_to(self.counselor))

        # All employees notice (empty roles)
        notice_all = Notice.objects.create(
            title="All Staff",
            body="All body",
            roles=[],
            created_by=self.admin,
        )
        self.assertTrue(notice_all.is_visible_to(self.admin))
        self.assertTrue(notice_all.is_visible_to(self.caller1))
        self.assertTrue(notice_all.is_visible_to(self.counselor))

    def test_model_visibility_individual_recipients(self):
        notice_indiv = Notice.objects.create(
            title="Individual Alice Only",
            body="Confidential note for Alice",
            created_by=self.admin,
        )
        notice_indiv.recipients.set([self.caller1])

        # Creator sees it
        self.assertTrue(notice_indiv.is_visible_to(self.admin))
        # Recipient sees it
        self.assertTrue(notice_indiv.is_visible_to(self.caller1))
        # Same role caller does NOT see it
        self.assertFalse(notice_indiv.is_visible_to(self.caller2))
        # Different role counselor does NOT see it
        self.assertFalse(notice_indiv.is_visible_to(self.counselor))

    def test_model_visibility_multiple_recipients(self):
        notice_multi = Notice.objects.create(
            title="Alice and Carol",
            body="Note for Alice and Carol",
            created_by=self.admin,
        )
        notice_multi.recipients.set([self.caller1, self.counselor])

        self.assertTrue(notice_multi.is_visible_to(self.admin))
        self.assertTrue(notice_multi.is_visible_to(self.caller1))
        self.assertTrue(notice_multi.is_visible_to(self.counselor))
        self.assertFalse(notice_multi.is_visible_to(self.caller2))

    # 2. Audience Label Formatting
    def test_audience_label_formatting(self):
        # Single recipient
        n1 = Notice.objects.create(title="T1", body="B1", created_by=self.admin)
        n1.recipients.set([self.caller1])
        self.assertIn("Alice Caller", n1.audience_label)

        # 2 recipients
        n2 = Notice.objects.create(title="T2", body="B2", created_by=self.admin)
        n2.recipients.set([self.caller1, self.caller2])
        self.assertEqual(n2.audience_label, "Alice Caller, Bob Caller")

        # 4 recipients
        u4 = User.objects.create_user(username="u4", first_name="Dan", password="p")
        n4 = Notice.objects.create(title="T4", body="B4", created_by=self.admin)
        n4.recipients.set([self.caller1, self.caller2, self.counselor, u4])
        self.assertEqual(n4.audience_label, "4 employees (Alice Caller, Bob Caller + 2 more)")

        # Role fallback
        n_role = Notice.objects.create(title="TR", body="BR", roles=[User.Role.CALLER])
        self.assertEqual(n_role.audience_label, "Caller")

        # All employees fallback
        n_all = Notice.objects.create(title="TA", body="BA", roles=[])
        self.assertEqual(n_all.audience_label, "All employees")

    def test_has_individual_recipients_property(self):
        n = Notice.objects.create(title="T", body="B")
        self.assertFalse(n.has_individual_recipients)
        n.recipients.set([self.caller1])
        self.assertTrue(n.has_individual_recipients)

    # 3. Web Views
    def test_admin_create_notice_individual_recipients(self):
        self.web_client.force_login(self.admin)
        res = self.web_client.post(
            "/notices/new/",
            {
                "title": "Private Notice",
                "body": "This is only for Alice and Bob.",
                "recipient_type": "employees",
                "recipients": [str(self.caller1.pk), str(self.caller2.pk)],
            },
        )
        self.assertEqual(res.status_code, 302)

        notice = Notice.objects.get(title="Private Notice")
        self.assertEqual(notice.roles, [])
        self.assertEqual(set(notice.recipients.all()), {self.caller1, self.caller2})

    def test_admin_create_notice_with_duplicate_and_inactive_recipients(self):
        self.web_client.force_login(self.admin)
        res = self.web_client.post(
            "/notices/new/",
            {
                "title": "Cleaned Recipients Notice",
                "body": "Testing duplicate / inactive stripping.",
                "recipient_type": "employees",
                "recipients": [
                    str(self.caller1.pk),
                    str(self.caller1.pk),  # duplicate
                    str(self.inactive_user.pk),  # inactive
                    "999999",  # non-existent
                    "invalid_abc",  # non-integer
                ],
            },
        )
        self.assertEqual(res.status_code, 302)

        notice = Notice.objects.get(title="Cleaned Recipients Notice")
        # Only active valid caller1 should be attached
        self.assertEqual(list(notice.recipients.all()), [self.caller1])

    def test_admin_create_notice_empty_recipients_rejected(self):
        self.web_client.force_login(self.admin)
        initial_count = Notice.objects.count()
        res = self.web_client.post(
            "/notices/new/",
            {
                "title": "Empty Recipients Notice",
                "body": "Should fail.",
                "recipient_type": "employees",
                "recipients": [],
            },
        )
        self.assertEqual(res.status_code, 302)
        self.assertEqual(Notice.objects.count(), initial_count)

    def test_non_admin_cannot_post_notice(self):
        self.web_client.force_login(self.caller1)
        res = self.web_client.post(
            "/notices/new/",
            {
                "title": "Unauthorized",
                "body": "Should be 403.",
                "recipient_type": "role",
            },
        )
        self.assertEqual(res.status_code, 403)

    def test_web_notices_list_display_and_scoping(self):
        notice_alice = Notice.objects.create(
            title="Exclusive Alice Notice",
            body="Secret text for Alice",
            created_by=self.admin,
        )
        notice_alice.recipients.set([self.caller1])

        notice_bob = Notice.objects.create(
            title="Exclusive Bob Notice",
            body="Secret text for Bob",
            created_by=self.admin,
        )
        notice_bob.recipients.set([self.caller2])

        # Alice views notices
        self.web_client.force_login(self.caller1)
        res_alice = self.web_client.get("/notices/")
        self.assertEqual(res_alice.status_code, 200)
        self.assertContains(res_alice, "Exclusive Alice Notice")
        self.assertNotContains(res_alice, "Exclusive Bob Notice")

        # Bob views notices
        self.web_client.force_login(self.caller2)
        res_bob = self.web_client.get("/notices/")
        self.assertEqual(res_bob.status_code, 200)
        self.assertNotContains(res_bob, "Exclusive Alice Notice")
        self.assertContains(res_bob, "Exclusive Bob Notice")

        # Admin views notices — sees both
        self.web_client.force_login(self.admin)
        res_admin = self.web_client.get("/notices/")
        self.assertEqual(res_admin.status_code, 200)
        self.assertContains(res_admin, "Exclusive Alice Notice")
        self.assertContains(res_admin, "Exclusive Bob Notice")
        # Admin also gets eligible_employees in context
        self.assertTrue(len(res_admin.context["eligible_employees"]) >= 3)

    # 4. Notification Dispatch
    def test_notify_notice_dispatches_only_to_selected_employees(self):
        notice = Notice.objects.create(
            title="Targeted Push Notice",
            body="Notification body",
            created_by=self.admin,
        )
        notice.recipients.set([self.caller1, self.admin])  # admin is creator

        notify_notice(notice.pk)

        # Alice should have received a notification
        alice_notifs = Notification.objects.filter(recipient=self.caller1, type="NOTICE")
        self.assertEqual(alice_notifs.count(), 1)
        self.assertEqual(alice_notifs.first().title, "New notice")
        self.assertEqual(alice_notifs.first().data.get("notice_id"), notice.pk)

        # Bob should NOT receive a notification
        bob_notifs = Notification.objects.filter(recipient=self.caller2, type="NOTICE")
        self.assertEqual(bob_notifs.count(), 0)

        # Admin is creator so should NOT receive a notification
        admin_notifs = Notification.objects.filter(recipient=self.admin, type="NOTICE")
        self.assertEqual(admin_notifs.count(), 0)

    # 5. Mobile API & Attachment Access
    def test_mobile_api_employee_notices_scoping(self):
        notice_alice = Notice.objects.create(
            title="Mobile Alice Notice",
            body="Content for Alice",
            created_by=self.admin,
        )
        notice_alice.recipients.set([self.caller1])

        # Alice calls mobile notices endpoint
        self.api_client.force_authenticate(user=self.caller1)
        res = self.api_client.get("/api/v1/mobile/employee/notices/")
        self.assertEqual(res.status_code, 200)
        titles = [n["title"] for n in res.data]
        self.assertIn("Mobile Alice Notice", titles)

        # Bob calls mobile notices endpoint
        self.api_client.force_authenticate(user=self.caller2)
        res = self.api_client.get("/api/v1/mobile/employee/notices/")
        self.assertEqual(res.status_code, 200)
        titles = [n["title"] for n in res.data]
        self.assertNotIn("Mobile Alice Notice", titles)

    def test_mobile_attachment_download_permissions(self):
        notice = Notice.objects.create(
            title="Notice With Attachment",
            body="Body text",
            created_by=self.admin,
        )
        notice.recipients.set([self.caller1])

        sample_file = SimpleUploadedFile(
            "doc.pdf",
            b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF",
            content_type="application/pdf",
        )
        att = NoticeAttachment.objects.create(
            notice=notice,
            uploaded_by=self.admin,
            file=sample_file,
            original_filename="doc.pdf",
            mime_type="application/pdf",
            file_size=len(sample_file),
        )

        # Alice (recipient) can download via mobile API
        self.api_client.force_authenticate(user=self.caller1)
        res_alice = self.api_client.get(
            f"/api/v1/mobile/notices/{notice.pk}/attachments/{att.pk}/download/"
        )
        self.assertEqual(res_alice.status_code, 200)

        # Bob (non-recipient) is forbidden / not found
        self.api_client.force_authenticate(user=self.caller2)
        res_bob = self.api_client.get(
            f"/api/v1/mobile/notices/{notice.pk}/attachments/{att.pk}/download/"
        )
        self.assertIn(res_bob.status_code, [403, 404])

