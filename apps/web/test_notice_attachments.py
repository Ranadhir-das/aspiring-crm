import io
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.web.models import Notice, NoticeAttachment


class NoticeAttachmentTests(TestCase):
    def setUp(self):
        self.web_client = Client()
        self.api_client = APIClient()

        self.admin = User.objects.create_user(
            username="admin_user",
            password="password123",
            role=User.Role.ADMIN,
        )
        self.caller = User.objects.create_user(
            username="caller_user",
            password="password123",
            role=User.Role.CALLER,
        )
        self.manager = User.objects.create_user(
            username="manager_user",
            password="password123",
            role=User.Role.MANAGER,
        )

        # Create valid mock files
        # Valid PNG magic: \x89PNG\r\n\x1a\n
        self.valid_png = SimpleUploadedFile(
            "test_image.png",
            b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15c4",
            content_type="image/png",
        )
        # Valid PDF magic: %PDF-1.4
        self.valid_pdf = SimpleUploadedFile(
            "document.pdf",
            b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF",
            content_type="application/pdf",
        )

    def test_admin_create_notice_with_attachments(self):
        self.web_client.force_login(self.admin)
        res = self.web_client.post(
            "/notices/new/",
            {
                "title": "Welcome All",
                "body": "Please read attached PDF and guidelines.",
                "roles": ["CALLER", "MANAGER"],
                "attachments": [self.valid_png, self.valid_pdf],
            },
        )
        self.assertEqual(res.status_code, 302)

        notice = Notice.objects.get(title="Welcome All")
        self.assertEqual(notice.attachments.count(), 2)
        png_att = notice.attachments.filter(mime_type="image/png").first()
        pdf_att = notice.attachments.filter(mime_type="application/pdf").first()
        self.assertIsNotNone(png_att)
        self.assertIsNotNone(pdf_att)
        self.assertEqual(png_att.original_filename, "test_image.png")
        self.assertEqual(pdf_att.original_filename, "document.pdf")

    def test_reject_dangerous_attachment(self):
        self.web_client.force_login(self.admin)
        dangerous_file = SimpleUploadedFile(
            "script.exe",
            b"MZ\x90\x00\x03\x00\x00\x00",
            content_type="application/octet-stream",
        )
        res = self.web_client.post(
            "/notices/new/",
            {
                "title": "Malware Notice",
                "body": "Test body",
                "attachments": [dangerous_file],
            },
        )
        self.assertEqual(res.status_code, 302)
        # Notice created but dangerous file rejected
        notice = Notice.objects.get(title="Malware Notice")
        self.assertEqual(notice.attachments.count(), 0)

    def test_mobile_employee_notices_api_includes_attachments(self):
        notice = Notice.objects.create(
            title="General Notice",
            body="Important information",
            roles=["CALLER"],
            created_by=self.admin,
        )
        att = NoticeAttachment.objects.create(
            notice=notice,
            uploaded_by=self.admin,
            file=self.valid_pdf,
            original_filename="document.pdf",
            mime_type="application/pdf",
            file_size=50,
        )

        self.api_client.force_authenticate(user=self.caller)
        res = self.api_client.get("/api/v1/mobile/employee/notices/")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(len(data), 1)
        self.assertIn("attachments", data[0])
        self.assertEqual(len(data[0]["attachments"]), 1)
        self.assertEqual(data[0]["attachments"][0]["original_filename"], "document.pdf")
        self.assertIn("/download/", data[0]["attachments"][0]["file_url"])

    def test_authorized_notice_attachment_download(self):
        notice = Notice.objects.create(
            title="Caller Only Notice",
            body="Only for callers",
            roles=["CALLER"],
            created_by=self.admin,
        )
        att = NoticeAttachment.objects.create(
            notice=notice,
            uploaded_by=self.admin,
            file=self.valid_pdf,
            original_filename="document.pdf",
            mime_type="application/pdf",
            file_size=50,
        )

        # Caller can download
        self.api_client.force_authenticate(user=self.caller)
        res = self.api_client.get(f"/api/v1/mobile/notices/{notice.pk}/attachments/{att.pk}/download/")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res["Content-Type"], "application/pdf")
        self.assertIn("document.pdf", res["Content-Disposition"])

    def test_unauthorized_notice_attachment_download_blocked(self):
        # Notice targeted only to MANAGER
        notice = Notice.objects.create(
            title="Manager Only Notice",
            body="Only for managers",
            roles=["MANAGER"],
            created_by=self.admin,
        )
        att = NoticeAttachment.objects.create(
            notice=notice,
            uploaded_by=self.admin,
            file=self.valid_pdf,
            original_filename="confidential.pdf",
            mime_type="application/pdf",
            file_size=50,
        )

        # Caller cannot access
        self.api_client.force_authenticate(user=self.caller)
        res = self.api_client.get(f"/api/v1/mobile/notices/{notice.pk}/attachments/{att.pk}/download/")
        self.assertEqual(res.status_code, 404)

    def test_admin_delete_attachment(self):
        self.web_client.force_login(self.admin)
        notice = Notice.objects.create(
            title="Notice to edit",
            body="Body",
            created_by=self.admin,
        )
        att = NoticeAttachment.objects.create(
            notice=notice,
            uploaded_by=self.admin,
            file=self.valid_pdf,
            original_filename="document.pdf",
            mime_type="application/pdf",
            file_size=50,
        )
        res = self.web_client.post(f"/notices/{notice.pk}/attachments/{att.pk}/delete/")
        self.assertEqual(res.status_code, 302)
        self.assertFalse(NoticeAttachment.objects.filter(pk=att.pk).exists())
