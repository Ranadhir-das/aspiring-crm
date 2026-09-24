from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from apps.leads.models import Lead
from apps.leads.services import preview_import


class PreviewImportTests(TestCase):
    def test_previews_generated_csv_without_writing_leads(self):
        file = SimpleUploadedFile('generated.csv', b'name,phone\nFirst,9876543201\nSecond,9876543202\n')
        result = preview_import(file)
        self.assertTrue(result['success'])
        self.assertEqual(result['total_rows'], 2)
        self.assertEqual(result['error_count'], 0)
        self.assertEqual(len(result['rows']), 2)
        self.assertFalse(Lead.objects.exists())
