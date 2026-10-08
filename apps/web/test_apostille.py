from unittest.mock import patch

from django.test import Client, TestCase
from django.urls import reverse

from apps.accounts.models import User
from apps.leads.models import Apostille, Lead
from apps.performance.models import PointsEntry


class ApostilleWebTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_user(username='apostille-web-admin', role='ADMIN')
        cls.caller = User.objects.create_user(username='apostille-web-caller', role='CALLER')
        cls.lead = Lead.objects.create(name='Searchable Student', phone='9876543210', assigned_caller=cls.caller)
        cls.record = Apostille.objects.create(name='External Person', phone='9876543211', country='India',
                                             number_of_documents=2, amount_received=2000, caller=cls.caller)

    def setUp(self):
        self.client.force_login(self.admin)

    def payload(self, **changes):
        data = dict(candidate_type='EXTERNAL', name='New Candidate', phone='9876543212',
                    number_of_documents='3', country='Canada', amount_received='5000', caller=self.caller.pk)
        data.update(changes)
        return data

    def urls(self):
        return [reverse('web:apostilles'), reverse('web:apostille-new'),
                reverse('web:apostille-detail', args=[self.record.pk]),
                reverse('web:apostille-edit', args=[self.record.pk]),
                reverse('web:apostille-delete', args=[self.record.pk]),
                reverse('web:apostille-points-preview'), reverse('web:apostille-lead-search')]

    def test_pages_render(self):
        for url in self.urls()[:5]:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)
        detail = self.client.get(reverse('web:apostille-detail', args=[self.record.pk]))
        self.assertContains(detail, 'External Person')
        self.assertContains(detail, 'Net ledger contribution')
        self.assertEqual(detail.context['contribution'], 10)

    def test_both_admin_roles_can_create_edit_view_delete(self):
        for role in ('ADMIN', 'SUPER_ADMIN'):
            with self.subTest(role=role):
                user = User.objects.create_user(username=f'apostille-{role}', role=role)
                self.client.force_login(user)
                response = self.client.post(reverse('web:apostille-new'), self.payload(points=999999))
                record = Apostille.objects.latest('pk')
                self.assertRedirects(response, reverse('web:apostille-detail', args=[record.pk]))
                self.assertEqual(record.created_by, user)
                self.assertEqual(record.points_entries.get().points, 30)
                self.assertEqual(record.points_entries.get().recorded_by, user)
                response = self.client.post(reverse('web:apostille-edit', args=[record.pk]), self.payload(amount_received=10000))
                self.assertEqual(response.status_code, 302)
                record.refresh_from_db()
                self.assertEqual(record.points, 50)
                self.client.post(reverse('web:apostille-edit', args=[record.pk]), self.payload(amount_received=10000))
                self.assertEqual(record.points_entries.count(), 2)
                self.assertEqual(self.client.post(reverse('web:apostille-delete', args=[record.pk])).status_code, 302)
                self.assertFalse(Apostille.objects.filter(pk=record.pk).exists())

    def test_non_admin_roles_forbidden_on_every_endpoint_and_method(self):
        for role in User.Role.values:
            if role in ('ADMIN', 'SUPER_ADMIN'):
                continue
            user = User.objects.create_user(username=f'denied-{role}', role=role)
            self.client.force_login(user)
            for url in self.urls():
                for method in ('get', 'post', 'put', 'patch', 'delete'):
                    with self.subTest(role=role, url=url, method=method):
                        self.assertEqual(getattr(self.client, method)(url).status_code, 403)
        self.assertEqual(Apostille.objects.count(), 1)
        self.assertEqual(self.record.points_entries.count(), 1)

    def test_anonymous_redirected(self):
        self.client.logout()
        for url in self.urls():
            self.assertEqual(self.client.get(url).status_code, 302)

    def test_existing_lead_create_search_and_link(self):
        search = self.client.get(reverse('web:apostille-lead-search'), {'q': 'Searchable'}).json()
        self.assertEqual(search['results'][0]['id'], self.lead.pk)
        response = self.client.post(reverse('web:apostille-new'), self.payload(
            candidate_type='EXISTING_LEAD', lead=self.lead.pk, name='', phone=''))
        self.assertEqual(response.status_code, 302)
        record = Apostille.objects.latest('pk')
        detail = self.client.get(reverse('web:apostille-detail', args=[record.pk]))
        self.assertContains(detail, reverse('web:lead-detail', args=[self.lead.pk]))
        self.assertContains(detail, self.lead.name)
        self.assertEqual(Lead.objects.count(), 1)

    def test_invalid_form_does_not_save_or_award(self):
        for changes in ({'name': ''}, {'number_of_documents': '1.5'}, {'caller': self.admin.pk},
                        {'amount_received': '-1'}, {'candidate_type': 'EXISTING_LEAD'},
                        {'lead': self.lead.pk}, {'candidate_type': 'EXISTING_LEAD', 'lead': self.lead.pk}):
            with self.subTest(changes=changes):
                response = self.client.post(reverse('web:apostille-new'), self.payload(**changes))
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.context['form'].errors)
        self.assertEqual(Apostille.objects.count(), 1)
        self.assertEqual(PointsEntry.objects.filter(event='APOSTILLE').count(), 1)

    def test_inactive_caller_not_newly_assignable_but_existing_attribution_retained(self):
        self.caller.is_active = False
        self.caller.save(update_fields=['is_active'])
        response = self.client.post(reverse('web:apostille-new'), self.payload())
        self.assertIn('caller', response.context['form'].errors)
        self.assertEqual(self.client.post(reverse('web:apostille-edit', args=[self.record.pk]), self.payload()).status_code, 302)

    def test_invalid_edit_renders_errors_without_changing_points(self):
        response = self.client.post(reverse('web:apostille-edit', args=[self.record.pk]), self.payload(amount_received='-1'))
        self.assertEqual(response.status_code, 200)
        self.assertIn('amount_received', response.context['form'].errors)
        self.record.refresh_from_db()
        self.assertEqual(self.record.points, 10)
        self.assertEqual(self.record.points_entries.count(), 1)

    def test_candidate_source_can_change_without_duplicate_awards(self):
        url = reverse('web:apostille-edit', args=[self.record.pk])
        response = self.client.post(url, self.payload(candidate_type='EXISTING_LEAD', lead=self.lead.pk,
                                                     name='', phone='', amount_received=2000))
        self.assertEqual(response.status_code, 302)
        self.record.refresh_from_db()
        self.assertEqual(self.record.lead, self.lead)
        self.assertEqual(self.record.name, '')
        self.assertEqual(self.client.post(url, self.payload(amount_received=2000)).status_code, 302)
        self.record.refresh_from_db()
        self.assertIsNone(self.record.lead)
        self.assertEqual(self.record.candidate_name, 'New Candidate')
        self.assertEqual(self.record.points_entries.count(), 1)

    def test_preview_uses_server_rules_and_validation(self):
        for amount, points in [('0', 0), ('1499.99', 0), ('1500', 10), ('100000', 1000)]:
            self.assertEqual(self.client.get(reverse('web:apostille-points-preview'), {'amount': amount}).json(), {'points': points})
        for amount in ('-1', '', 'NaN', '1.001', '10000000000'):
            self.assertEqual(self.client.get(reverse('web:apostille-points-preview'), {'amount': amount}).status_code, 400)

    def test_search_filters_and_pagination(self):
        for index in range(21):
            Apostille.objects.create(name=f'Visitor {index}', phone='9876543212', country='Canada',
                                     number_of_documents=1, amount_received=0, caller=self.caller)
        url = reverse('web:apostilles')
        response = self.client.get(url)
        self.assertEqual(len(response.context['records']), 20)
        self.assertEqual(len(self.client.get(url, {'page': 2}).context['records']), 2)
        self.assertEqual(self.client.get(url, {'q': 'External Person'}).context['records'].paginator.count, 1)
        self.assertEqual(self.client.get(url, {'q': 'Canada', 'caller': self.caller.pk, 'candidate_type': 'EXTERNAL'}).context['records'].paginator.count, 21)
        self.assertEqual(self.client.get(url, {'caller': 'invalid'}).context['records'].paginator.count, 0)

    def test_delete_get_never_mutates_and_requires_csrf(self):
        url = reverse('web:apostille-delete', args=[self.record.pk])
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.admin)
        response = client.get(url)
        self.assertContains(response, 'Are you sure you want to delete this Apostille record?')
        self.assertTrue(Apostille.objects.filter(pk=self.record.pk).exists())
        self.assertEqual(client.post(url).status_code, 403)
        self.assertEqual(client.post(reverse('web:apostille-new'), self.payload()).status_code, 403)
        self.assertEqual(client.post(reverse('web:apostille-edit', args=[self.record.pk]), self.payload()).status_code, 403)
        self.assertEqual(client.post(url, {'csrfmiddlewaretoken': client.cookies['csrftoken'].value}).status_code, 302)

    def test_audit_failure_rolls_back_record_and_points(self):
        with patch('apps.web.apostille_views.AuditEvent.objects.create', side_effect=RuntimeError('audit unavailable')):
            with self.assertRaises(RuntimeError):
                self.client.post(reverse('web:apostille-new'), self.payload())
        self.assertEqual(Apostille.objects.count(), 1)
        self.assertEqual(PointsEntry.objects.filter(event='APOSTILLE').count(), 1)

    def test_navigation_immediately_after_admissions_and_hidden_for_other_roles(self):
        for role in User.Role.values:
            user = User.objects.create_user(username=f'nav-{role}', role=role)
            self.client.force_login(user)
            response = self.client.get(reverse('web:dashboard'), follow=True)
            content = response.content.decode()
            link = f'href="{reverse("web:apostilles")}"'
            if role in ('ADMIN', 'SUPER_ADMIN'):
                self.assertIn(link, content)
                before = content[:content.index(link)]
                self.assertIn('Admissions', before)
                self.assertEqual(before[before.rfind('Admissions'):].count('<a '), 1)
            else:
                self.assertNotIn(link, content)

    def test_missing_record_is_404_and_unsupported_admin_methods_are_405(self):
        self.assertEqual(self.client.get(reverse('web:apostille-detail', args=[999999])).status_code, 404)
        self.assertEqual(self.client.delete(reverse('web:apostille-delete', args=[self.record.pk])).status_code, 405)
