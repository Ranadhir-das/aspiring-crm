import os
from io import BytesIO
import openpyxl
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase

from apps.accounts.models import User
from apps.leads.models import Lead, LeadImportBatch
from apps.leads.import_storage import (
    save_import_preview_file,
    get_import_preview_file,
    cleanup_import_preview_file,
)


class FileLeadImportWorkflowTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user("admin_user", role=User.Role.ADMIN)
        self.manager = User.objects.create_user("manager_user", role=User.Role.MANAGER)
        self.caller1 = User.objects.create_user("caller1_user", role=User.Role.CALLER)
        self.caller2 = User.objects.create_user("caller2_user", role=User.Role.CALLER)
        self.client.force_login(self.admin)

    def _create_xlsx_bytes(self, rows):
        """Helper to create an in-memory XLSX file."""
        wb = openpyxl.Workbook()
        ws = wb.active
        for row in rows:
            ws.append(row)
        stream = BytesIO()
        wb.save(stream)
        stream.seek(0)
        return stream.getvalue()

    def test_csv_file_preview_then_import_without_reuploading_file(self):
        """
        Regression Test for Lead Import Bug:
        Step 1: Upload CSV file with action='preview'. Preview succeeds and returns import_token.
        Step 2: Submit action='import' WITHOUT re-attaching the file.
        The system must import the staged file and create leads.
        """
        csv_data = b"name,phone\nJohn Doe,9876543210\nJane Doe,9876543211\n"
        upload = SimpleUploadedFile("students.csv", csv_data, content_type="text/csv")

        # Step 1: Preview
        preview_resp = self.client.post("/leads/import/", {
            "action": "preview",
            "import_method": "file",
            "preferred_course": "MBBS",
            "file": upload,
        })
        self.assertEqual(preview_resp.status_code, 200)
        self.assertIn("preview", preview_resp.context)
        preview = preview_resp.context["preview"]
        self.assertTrue(preview["success"])
        self.assertEqual(preview["total_rows"], 2)
        self.assertEqual(preview["duplicate_count"], 0)
        self.assertIn("import_token", preview)
        import_token = preview["import_token"]
        self.assertTrue(bool(import_token))

        # Ensure no leads were created during preview
        self.assertEqual(Lead.objects.count(), 0)

        # Step 2: Import using import_token (WITHOUT 'file' in POST data, exactly as in browser)
        import_resp = self.client.post("/leads/import/", {
            "action": "import",
            "import_method": "file",
            "preferred_course": "MBBS",
            "import_token": import_token,
        })
        self.assertEqual(import_resp.status_code, 302)
        self.assertIn("/leads/distribute/?batch=", import_resp.url)

        # Verify leads are created
        self.assertEqual(Lead.objects.count(), 2)
        lead1 = Lead.objects.get(phone="9876543210")
        self.assertEqual(lead1.name, "John Doe")
        self.assertEqual(lead1.preferred_course, "MBBS")
        lead2 = Lead.objects.get(phone="9876543211")
        self.assertEqual(lead2.name, "Jane Doe")

    def test_xlsx_file_preview_then_import_without_reuploading_file(self):
        """
        Verify Excel (.xlsx) file: preview -> import without re-uploading file.
        """
        xlsx_rows = [
            ["name", "phone", "location"],
            ["Alice Smith", "9876543220", "Delhi"],
            ["Bob Brown", "9876543221", "Mumbai"],
        ]
        xlsx_bytes = self._create_xlsx_bytes(xlsx_rows)
        upload = SimpleUploadedFile("students.xlsx", xlsx_bytes, content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

        # Preview
        preview_resp = self.client.post("/leads/import/", {
            "action": "preview",
            "import_method": "file",
            "preferred_course": "BTECH",
            "file": upload,
        })
        self.assertEqual(preview_resp.status_code, 200)
        preview = preview_resp.context["preview"]
        import_token = preview["import_token"]

        # Import with caller assignment
        import_resp = self.client.post("/leads/import/", {
            "action": "import",
            "import_method": "file",
            "preferred_course": "BTECH",
            "caller": self.caller1.pk,
            "import_token": import_token,
        })
        self.assertEqual(import_resp.status_code, 302)

        self.assertEqual(Lead.objects.count(), 2)
        alice = Lead.objects.get(phone="9876543220")
        self.assertEqual(alice.name, "Alice Smith")
        self.assertEqual(alice.location, "Delhi")
        self.assertEqual(alice.assigned_caller, self.caller1)

    def test_duplicate_leads_skipped_and_reported(self):
        """
        Verify duplicate leads (both in-file and against existing database records)
        are reported in preview and skipped during commit.
        """
        # Pre-existing lead in CRM
        Lead.objects.create(name="Existing Lead", phone="9876543210", preferred_course="MBBS")

        csv_data = (
            b"name,phone\n"
            b"Duplicate In DB,9876543210\n"  # Duplicate of existing
            b"Fresh Lead,9876543230\n"        # Valid
            b"In-File Dupe,9876543230\n"       # Duplicate in file
        )
        upload = SimpleUploadedFile("dupes.csv", csv_data, content_type="text/csv")

        preview_resp = self.client.post("/leads/import/", {
            "action": "preview",
            "import_method": "file",
            "preferred_course": "MBBS",
            "file": upload,
        })
        preview = preview_resp.context["preview"]
        self.assertEqual(preview["duplicate_count"], 2)
        self.assertEqual(preview["total_rows"], 3)
        self.assertEqual(preview["error_count"], 0)

        import_token = preview["import_token"]

        import_resp = self.client.post("/leads/import/", {
            "action": "import",
            "import_method": "file",
            "preferred_course": "MBBS",
            "import_token": import_token,
        })
        self.assertEqual(import_resp.status_code, 302)

        # Only 1 new lead should be created (total 2 in DB)
        self.assertEqual(Lead.objects.count(), 2)
        self.assertTrue(Lead.objects.filter(phone="9876543230").exists())

    def test_invalid_rows_reported_in_preview_and_skipped(self):
        """
        Verify rows with invalid phone numbers or missing columns are reported.
        """
        csv_data = (
            b"name,phone\n"
            b"Valid Lead,9876543240\n"
            b"Bad Phone,not-a-number\n"
        )
        upload = SimpleUploadedFile("invalid.csv", csv_data, content_type="text/csv")

        preview_resp = self.client.post("/leads/import/", {
            "action": "preview",
            "import_method": "file",
            "preferred_course": "MBBS",
            "file": upload,
        })
        preview = preview_resp.context["preview"]
        self.assertEqual(preview["error_count"], 1)
        self.assertEqual(preview["valid_count"], 1)

        # Commit import
        import_resp = self.client.post("/leads/import/", {
            "action": "import",
            "import_method": "file",
            "preferred_course": "MBBS",
            "import_token": preview["import_token"],
        })
        self.assertEqual(import_resp.status_code, 302)
        self.assertEqual(Lead.objects.count(), 1)
        self.assertEqual(Lead.objects.first().name, "Valid Lead")

    def test_session_fallback_when_import_token_not_in_post(self):
        """
        If import_token is omitted from POST data (e.g. browser submitted only standard inputs),
        the server retrieves the token stored in request.session['lead_import_token'].
        """
        csv_data = b"name,phone\nSession Student,9876543250\n"
        upload = SimpleUploadedFile("session.csv", csv_data, content_type="text/csv")

        preview_resp = self.client.post("/leads/import/", {
            "action": "preview",
            "import_method": "file",
            "preferred_course": "MBBS",
            "file": upload,
        })
        self.assertEqual(preview_resp.status_code, 200)

        # Post action='import' without import_token in POST body
        import_resp = self.client.post("/leads/import/", {
            "action": "import",
            "import_method": "file",
            "preferred_course": "MBBS",
        })
        self.assertEqual(import_resp.status_code, 302)
        self.assertEqual(Lead.objects.filter(phone="9876543250").count(), 1)

    def test_repeated_submissions_do_not_create_duplicate_leads(self):
        """
        Attempting to submit import twice with the same token should safely handle
        already-created leads and not create duplicates.
        """
        csv_data = b"name,phone\nOne Shot,9876543260\n"
        upload = SimpleUploadedFile("once.csv", csv_data, content_type="text/csv")

        preview_resp = self.client.post("/leads/import/", {
            "action": "preview",
            "import_method": "file",
            "preferred_course": "MBBS",
            "file": upload,
        })
        token = preview_resp.context["preview"]["import_token"]

        # First import
        self.client.post("/leads/import/", {
            "action": "import",
            "import_method": "file",
            "preferred_course": "MBBS",
            "import_token": token,
        })
        self.assertEqual(Lead.objects.filter(phone="9876543260").count(), 1)

        # Second import with the same token (the token is cleaned up, so it demands a file or exits cleanly)
        resp2 = self.client.post("/leads/import/", {
            "action": "import",
            "import_method": "file",
            "preferred_course": "MBBS",
            "import_token": token,
        })
        # Lead count must remain exactly 1
        self.assertEqual(Lead.objects.filter(phone="9876543260").count(), 1)

    def test_user_isolation_security(self):
        """
        User B cannot import a file uploaded/previewed by User A.
        """
        csv_data = b"name,phone\nPrivate Lead,9876543270\n"
        upload = SimpleUploadedFile("priv.csv", csv_data, content_type="text/csv")

        # Admin previews file
        token = save_import_preview_file(self.admin.id, upload)

        # Manager tries to use Admin's import token
        self.client.force_login(self.manager)
        resp = self.client.post("/leads/import/", {
            "action": "import",
            "import_method": "file",
            "preferred_course": "MBBS",
            "import_token": token,
        })
        # Should reject because token does not belong to manager
        self.assertContains(resp, "Please choose a CSV or Excel file.")
        self.assertEqual(Lead.objects.count(), 0)

        # Cleanup
        cleanup_import_preview_file(token)

    def test_paste_numbers_import_unaffected(self):
        """
        Verify that Paste Numbers import continues to work properly and without regressions.
        """
        paste_data = {
            "action": "import",
            "import_method": "paste",
            "preferred_course": "MBBS",
            "pasted_numbers": "9876543280\n9876543281\n",
        }
        resp = self.client.post("/leads/import/", paste_data)
        self.assertEqual(resp.status_code, 200)
        self.assertIn("paste_result", resp.context)
        self.assertEqual(resp.context["paste_result"]["created_count"], 2)
        self.assertEqual(Lead.objects.filter(phone="9876543280").count(), 1)
        self.assertEqual(Lead.objects.filter(phone="9876543281").count(), 1)

    def test_rest_api_two_stage_preview_and_commit(self):
        """
        Verify REST API endpoints:
        /api/v1/leads/import/preview/ returns import_identifier
        /api/v1/leads/import/commit/ accepts import_identifier without re-uploading file.
        """
        from rest_framework.test import APIClient
        api_client = APIClient()
        api_client.force_authenticate(user=self.admin)

        csv_data = b"name,phone\nApi Student,9876543290\n"
        upload = SimpleUploadedFile("api.csv", csv_data, content_type="text/csv")

        # 1. API Preview
        prev_res = api_client.post("/api/v1/leads/import/preview/", {
            "preferred_course": "MBBS",
            "file": upload,
        })
        self.assertEqual(prev_res.status_code, 200)
        data = prev_res.json()
        self.assertTrue(data["success"])
        self.assertIn("import_identifier", data)
        import_id = data["import_identifier"]

        # 2. API Commit using import_identifier without file
        commit_res = api_client.post("/api/v1/leads/import/commit/", {
            "preferred_course": "MBBS",
            "import_identifier": import_id,
        })
        self.assertEqual(commit_res.status_code, 201)
        commit_data = commit_res.json()
        self.assertEqual(commit_data["created_count"], 1)
        self.assertEqual(Lead.objects.filter(phone="9876543290").count(), 1)
