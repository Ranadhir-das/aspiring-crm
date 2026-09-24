from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from apps.accounts.models import User
from apps.leads.models import Lead, LeadImportBatch
from apps.leads.services import commit_import


class CommitImportTests(TestCase):
    def test_commits_generated_csv_and_skips_duplicates(self):
        admin = User.objects.create_user('import-admin', role='ADMIN')
        def csv():
            return SimpleUploadedFile('generated.csv', b'name,phone\nFirst,9876543201\nSecond,9876543202\n')
        result = commit_import(csv(), admin)
        self.assertTrue(result['success'])
        self.assertEqual(result['created_count'], 2)
        self.assertEqual(Lead.objects.count(), 2)
        self.assertEqual(LeadImportBatch.objects.get(pk=result['batch_id']).imported_by_id, admin.pk)
        repeated = commit_import(csv(), admin)
        self.assertEqual(repeated['created_count'], 0)
        self.assertEqual(repeated['duplicate_count'], 2)
