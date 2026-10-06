import os
from pathlib import Path
from django.test import TestCase
from django.template.loader import get_template
from django.urls import reverse
from apps.accounts.models import User
from apps.leads.models import Lead
from .employee_links import employee_link_html


class WorkspaceUITests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_user('ui-admin', role='ADMIN')
        cls.employee = User.objects.create_user('ui-employee', role='EMPLOYEE', first_name='<Visitor>')
        cls.caller = User.objects.create_user('ui-caller', role='CALLER')
        cls.lead = Lead.objects.create(name='Example Student', phone='9876543210', assigned_caller=cls.caller)

    def test_employee_permissions_and_safe_links(self):
        url = reverse('web:caller-detail', args=[self.employee.pk])
        self.assertIn('&lt;Visitor&gt;', employee_link_html(self.admin, self.employee))
        self.assertNotIn('<a', employee_link_html(self.caller, self.employee))
        for viewer, status in [(self.admin, 200), (self.employee, 200), (self.caller, 403)]:
            self.client.force_login(viewer)
            response = self.client.get(url)
            self.assertEqual(response.status_code, status)
            if status == 200:
                self.assertIn("Today's calls", response.context["employee_summary"])
                self.assertContains(response, 'Recent counselling')
        self.client.force_login(self.admin)
        self.assertEqual(self.client.post(url, {'action': 'adjust'}).status_code, 403)
        self.client.logout()
        self.assertEqual(self.client.get(url).status_code, 302)

    def test_all_templates_compile(self):
        for path in (Path(__file__).parent / 'templates' / 'web').glob('*.html'):
            with self.subTest(template=path.name):
                get_template('web/' + path.name)

    def test_authenticated_page_inventory(self):
        self.client.force_login(self.admin)
        pages = 'dashboard leads calls followups counselling admissions team employees people-overview employee-home performance caller-sessions lead-import lead-create admission-new lead-routing services caller-services website-integrations website-integration-create chat-home notices projects project-new reports report-new feedback attendance leaves leave-apply holidays holiday-new payroll expenses customers invoices photo-attendance activity-log consultations lead-distribute'.split()
        targets = [(name, reverse('web:' + name)) for name in pages]
        targets += [('employee-profile', reverse('web:caller-detail', args=[self.employee.pk])),
                    ('caller-profile', reverse('web:caller-detail', args=[self.caller.pk])),
                    ('lead-detail', reverse('web:lead-detail', args=[self.lead.pk]))]
        for name, url in targets:
            with self.subTest(page=name):
                response = self.client.get(url, follow=True)
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, 'web/workspace.css')
                if os.environ.get('CRM_UI_SNAPSHOTS'):
                    folder = Path(os.environ['CRM_UI_SNAPSHOTS'])
                    folder.mkdir(parents=True, exist_ok=True)
                    html = response.content.decode('utf-8')
                    static = (Path(__file__).parent / 'static').resolve().as_uri()
                    html = html.replace('/static/', static + '/')
                    (folder / (name + '.html')).write_text(html, encoding='utf-8')
