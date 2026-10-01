from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.chat.models import ChatAttachment, ChatChannel, ChatMessage


class ChatAttachmentTests(TestCase):
    def setUp(self):
        self.client = APIClient()

        self.user1 = User.objects.create_user(
            username="chat_user_1",
            password="password123",
            role=User.Role.CALLER,
        )
        self.user2 = User.objects.create_user(
            username="chat_user_2",
            password="password123",
            role=User.Role.CALLER,
        )
        self.outsider = User.objects.create_user(
            username="outsider_user",
            password="password123",
            role=User.Role.CALLER,
        )

        self.channel = ChatChannel.objects.create(
            name="General Discussion",
            kind=ChatChannel.Kind.GROUP,
        )
        self.channel.members.add(self.user1, self.user2)

        # Mock valid PNG
        self.valid_png = SimpleUploadedFile(
            "screenshot.png",
            b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15c4",
            content_type="image/png",
        )
        # Mock valid PDF
        self.valid_pdf = SimpleUploadedFile(
            "admission-form.pdf",
            b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF",
            content_type="application/pdf",
        )

    def test_send_message_with_attachment_multipart(self):
        self.client.force_authenticate(user=self.user1)
        res = self.client.post(
            f"/api/v1/mobile/chat/channels/{self.channel.pk}/messages/",
            {
                "text": "Please check this document.",
                "file": self.valid_pdf,
            },
            format="multipart",
        )
        self.assertEqual(res.status_code, 201)
        data = res.json()
        self.assertEqual(data["text"], "Please check this document.")
        self.assertIn("attachments", data)
        self.assertEqual(len(data["attachments"]), 1)
        self.assertEqual(data["attachments"][0]["original_name"], "admission-form.pdf")
        self.assertEqual(data["attachments"][0]["mime_type"], "application/pdf")
        self.assertIn("/download/", data["attachments"][0]["file_url"])

    def test_send_attachment_only_message(self):
        self.client.force_authenticate(user=self.user1)
        res = self.client.post(
            f"/api/v1/mobile/chat/channels/{self.channel.pk}/messages/",
            {
                "file": self.valid_png,
            },
            format="multipart",
        )
        self.assertEqual(res.status_code, 201)
        data = res.json()
        self.assertEqual(data["text"], "")
        self.assertEqual(len(data["attachments"]), 1)
        self.assertEqual(data["attachments"][0]["mime_type"], "image/png")

    def test_existing_json_text_message_unaffected(self):
        self.client.force_authenticate(user=self.user1)
        res = self.client.post(
            f"/api/v1/mobile/chat/channels/{self.channel.pk}/messages/",
            {"text": "Simple text message without attachments"},
            format="json",
        )
        self.assertEqual(res.status_code, 201)
        data = res.json()
        self.assertEqual(data["text"], "Simple text message without attachments")
        self.assertEqual(len(data["attachments"]), 0)

    def test_reject_dangerous_chat_attachment(self):
        self.client.force_authenticate(user=self.user1)
        bad_file = SimpleUploadedFile(
            "virus.exe",
            b"MZ\x90\x00\x03\x00\x00\x00",
            content_type="application/octet-stream",
        )
        res = self.client.post(
            f"/api/v1/mobile/chat/channels/{self.channel.pk}/messages/",
            {
                "text": "Check this out",
                "file": bad_file,
            },
            format="multipart",
        )
        self.assertEqual(res.status_code, 400)

    def test_download_chat_attachment_by_member(self):
        msg = ChatMessage.objects.create(
            channel=self.channel,
            sender=self.user1,
            text="Here is the PDF",
        )
        att = ChatAttachment.objects.create(
            message=msg,
            file=self.valid_pdf,
            original_name="admission-form.pdf",
            mime_type="application/pdf",
            file_size=50,
        )

        # user2 is member of the channel
        self.client.force_authenticate(user=self.user2)
        res = self.client.get(f"/api/v1/mobile/chat/attachments/{att.pk}/download/")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res["Content-Type"], "application/pdf")
        self.assertIn("admission-form.pdf", res["Content-Disposition"])

    def test_download_chat_attachment_blocked_for_outsider(self):
        msg = ChatMessage.objects.create(
            channel=self.channel,
            sender=self.user1,
            text="Private PDF",
        )
        att = ChatAttachment.objects.create(
            message=msg,
            file=self.valid_pdf,
            original_name="admission-form.pdf",
            mime_type="application/pdf",
            file_size=50,
        )

        # outsider is NOT member of the channel
        self.client.force_authenticate(user=self.outsider)
        res = self.client.get(f"/api/v1/mobile/chat/attachments/{att.pk}/download/")
        self.assertEqual(res.status_code, 403)
