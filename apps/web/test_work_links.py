from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient
from apps.accounts.models import User
from apps.web.models import WorkReport
from apps.web.workforce_forms import ReportForm


class WorkLinksTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('links-employee', role='IT')
        self.api = APIClient()
        self.api.force_authenticate(self.user)
        self.links = ['https://example.com/lead-report', 'https://example.com/call-report', 'https://example.com/document']
        self.payload = {'date': str(timezone.localdate()), 'notes': 'Completed work', 'work_links': self.links}

    def test_mobile_save_read_edit_and_clear(self):
        response = self.api.post('/api/v1/mobile/employee/reports/', self.payload, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        report = WorkReport.objects.get(employee=self.user)
        self.assertEqual(report.work_links, self.links)
        self.assertEqual(report.work_link, self.links[0])
        self.assertEqual(self.api.get('/api/v1/mobile/employee/').data['reports'][0]['work_links'], self.links)
        self.api.post('/api/v1/mobile/employee/reports/', {**self.payload, 'work_links': self.links[1:]}, format='json')
        report.refresh_from_db()
        self.assertEqual(report.work_links, self.links[1:])
        self.api.post('/api/v1/mobile/employee/reports/', {**self.payload, 'work_links': []}, format='json')
        report.refresh_from_db()
        self.assertEqual(report.work_links, [])
        self.assertEqual(report.work_link, '')
        self.assertEqual(WorkReport.objects.count(), 1)

    def test_invalid_link_does_not_save(self):
        response = self.api.post('/api/v1/mobile/employee/reports/', {**self.payload, 'work_links': ['javascript:alert(1)']}, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertFalse(WorkReport.objects.exists())

    def test_legacy_client_keeps_additional_links(self):
        self.api.post('/api/v1/mobile/employee/reports/', self.payload, format='json')
        response = self.api.post('/api/v1/mobile/employee/reports/', {'date': self.payload['date'], 'notes': 'Legacy edit', 'work_link': 'https://example.com/new'}, format='json')
        self.assertEqual(response.status_code, 200)
        report = WorkReport.objects.get(employee=self.user)
        self.assertEqual(report.work_links, ['https://example.com/new', *self.links[1:]])

    def test_crm_form_save_display_and_isolation(self):
        self.client.force_login(self.user)
        response = self.client.post('/reports/new/', self.payload)
        self.assertEqual(response.status_code, 302)
        report = WorkReport.objects.get(employee=self.user)
        self.assertEqual(report.work_links, self.links)
        for url in ['/reports/', f'/reports/{report.pk}/']:
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200)
            for link in self.links:
                self.assertContains(response, link)
        other = User.objects.create_user('links-other', role='IT')
        self.client.force_login(other)
        self.assertEqual(self.client.get(f'/reports/{report.pk}/').status_code, 404)
        self.api.force_authenticate(other)
        self.assertEqual(self.api.get('/api/v1/mobile/employee/').data['reports'], [])

    def test_existing_single_link_is_loaded_in_form(self):
        report = WorkReport.objects.create(employee=self.user, work_link=self.links[0])
        form = ReportForm(instance=report, employee=self.user)
        self.assertEqual(form.initial['work_links'], [self.links[0]])
