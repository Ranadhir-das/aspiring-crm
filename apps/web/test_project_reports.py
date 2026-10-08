from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient
from apps.accounts.models import User
from apps.web.models import WorkReport


class ProjectReportTests(TestCase):
    def setUp(self):
        self.employee = User.objects.create_user('project-worker', role='IT')
        self.other = User.objects.create_user('other-worker', role='VIDEO_EDITOR')
        self.api = APIClient()
        self.api.force_authenticate(self.employee)
        self.project = dict(project_name='Website redesign', duration='4 hours', status='Completed', notes='Reviewed <script>alert(1)</script>')

    def submit(self, **changes):
        return self.api.post('/api/v1/mobile/employee/reports/', {
            'date': timezone.localdate().isoformat(), 'project_reports': [{**self.project, **changes}],
        }, format='json')

    def test_completed_omitted_or_stale_date_is_cleared_and_returned(self):
        for changes in ({}, {'expected_completion_date': '2026-12-31'}):
            response = self.submit(**changes)
            self.assertEqual(response.status_code, 200, response.data)
            report = WorkReport.objects.get(employee=self.employee)
            self.assertEqual(report.project_reports[0]['expected_completion_date'], '')
            home = self.api.get('/api/v1/mobile/employee/')
            self.assertEqual(home.data['reports'][0]['project_reports'], report.project_reports)

    def test_pending_and_in_progress_require_valid_date(self):
        for status in ('Pending', 'In Progress'):
            for date in ('', 'not-a-date', '2026-02-30'):
                with self.subTest(status=status, date=date):
                    self.assertEqual(self.submit(status=status, expected_completion_date=date).status_code, 400)
            self.assertEqual(self.submit(status=status, expected_completion_date='2026-12-31').status_code, 200)
        self.assertEqual(self.submit(status='Unknown').status_code, 400)

    def test_crm_cards_permissions_filters_and_legacy_reports(self):
        self.submit()
        report = WorkReport.objects.get(employee=self.employee)
        WorkReport.objects.create(employee=self.other, date=timezone.localdate(), notes='Legacy daily work')
        for role in ('ADMIN', 'MANAGER'):
            manager = User.objects.create_user('viewer-' + role, role=role)
            self.client.force_login(manager)
            response = self.client.get('/reports/')
            self.assertContains(response, 'Website redesign')
            self.assertContains(response, '4 hours')
            self.assertContains(response, 'project-report-card')
            self.assertContains(response, 'Legacy daily work')
            self.assertNotContains(response, '<script>alert(1)</script>')
            self.assertContains(response, '&lt;script&gt;')
            self.assertNotContains(response, 'Expected completion')
            self.assertContains(self.client.get(f'/reports/{report.pk}/'), 'Website redesign')
            filtered = self.client.get('/reports/', {'employee': self.other.pk})
            self.assertNotContains(filtered, 'Website redesign')
            self.assertContains(filtered, 'Legacy daily work')
            self.assertNotContains(self.client.get('/reports/', {'start_date': '2099-01-01'}), 'Website redesign')
        self.client.force_login(self.other)
        self.assertNotContains(self.client.get('/reports/'), 'Website redesign')
        self.assertEqual(self.client.get(f'/reports/{report.pk}/').status_code, 404)
