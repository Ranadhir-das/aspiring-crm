from datetime import datetime, timedelta

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.leads.models import Counselling, Lead
from apps.performance.models import PointsEntry
from .counselling_views import meeting_links


class CounsellingPageTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_user('counsel-admin', role='ADMIN')
        cls.manager = User.objects.create_user('counsel-manager', role='MANAGER')
        cls.super_admin = User.objects.create_user('counsel-super', role='SUPER_ADMIN')
        cls.caller = User.objects.create_user('counsel-caller', role='CALLER', first_name='Priya', last_name='Nair')
        cls.former = User.objects.create_user('former-caller', role='CALLER', is_active=False)
        cls.employee = User.objects.create_user('counsel-employee', role='EMPLOYEE')
        cls.accountant = User.objects.create_user('counsel-accountant', role='ACCOUNTANT')
        cls.lead = Lead.objects.create(name='Rohan Verma', phone='9876543210', assigned_caller=cls.caller)
        cls.now = timezone.now()
        # Bulk fixture creation avoids awarding points unrelated to this read-only page.
        cls.linked, cls.visitor, cls.online = Counselling.objects.bulk_create([
            Counselling(lead=cls.lead, caller=cls.caller, conducted_at=cls.now,
                        counselling_type='GOOGLE_MEET', college='Example University', course='MBBS',
                        notes='Discussed scholarship. https://meet.google.com/abc-defg-hij'),
            Counselling(caller=cls.former, visitor_name='Ananya Sen', visitor_phone='9123456789',
                        counselling_type='WALK_IN', conducted_at=cls.now-timedelta(days=2), notes='Parent attended'),
            Counselling(caller=cls.caller, visitor_name='Online Student', visitor_phone='9988776655',
                        counselling_type='ONLINE', conducted_at=cls.now-timedelta(days=1)),
        ])

    def setUp(self):
        self.url = reverse('web:counselling')
        self.client.force_login(self.admin)

    def ids(self, **params):
        return [record.pk for record in self.client.get(self.url, params).context['records']]

    def test_management_access_and_active_sidebar(self):
        for user in [self.admin, self.manager, self.super_admin]:
            with self.subTest(role=user.role):
                self.client.force_login(user)
                response = self.client.get(self.url)
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, f'aria-current="page" href="{self.url}"')
                self.assertEqual(response.context['section'], 'counselling')

    def test_non_management_and_anonymous_access(self):
        for user in [self.caller, self.employee, self.accountant]:
            self.client.force_login(user)
            self.assertEqual(self.client.get(self.url).status_code, 403)
        self.client.force_login(self.caller)
        self.assertNotContains(self.client.get(reverse('web:admissions')), f'href="{self.url}"')
        self.client.logout()
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse('web:login'), response.url)
        self.client.force_login(self.former)
        self.assertEqual(self.client.get(self.url).status_code, 302)

    def test_records_include_existing_lead_external_and_all_types(self):
        response = self.client.get(self.url)
        for text in ['Rohan Verma', '9876543210', 'Ananya Sen', '9123456789', 'Priya Nair',
                     'Google Meet Counselling', 'Walk-in Counselling', 'Online / Phone Counselling',
                     'External visitor', 'Parent attended', 'Example University', 'MBBS',
                     'https://meet.google.com/abc-defg-hij']:
            self.assertContains(response, text)
        self.assertContains(response, reverse('web:lead-detail', args=[self.lead.pk]))
        self.assertEqual(self.ids(), [self.linked.pk, self.online.pk, self.visitor.pk])

    def test_search_name_phone_employee_notes_and_details(self):
        for search, expected in [('Rohan', [self.linked.pk]), ('9876543210', [self.linked.pk]),
                                 ('Ananya', [self.visitor.pk]), ('9123456789', [self.visitor.pk]),
                                 ('Priya', [self.linked.pk, self.online.pk]),
                                 ('scholarship', [self.linked.pk]), ('Example University', [self.linked.pk]),
                                 ('MBBS', [self.linked.pk]), ('  Ananya  ', [self.visitor.pk])]:
            with self.subTest(search=search):
                self.assertEqual(self.ids(q=search), expected)

    def test_type_employee_and_source_filters_combine(self):
        for kind, pk in [('GOOGLE_MEET', self.linked.pk), ('ONLINE', self.online.pk), ('WALK_IN', self.visitor.pk)]:
            self.assertEqual(self.ids(counselling_type=kind), [pk])
        self.assertEqual(self.ids(caller=self.former.pk), [self.visitor.pk])
        self.assertEqual(self.ids(channel='lead'), [self.linked.pk])
        self.assertEqual(self.ids(channel='external'), [self.online.pk, self.visitor.pk])
        self.assertEqual(self.ids(channel='external', caller=self.caller.pk, counselling_type='ONLINE'), [self.online.pk])
        self.assertEqual(self.ids(channel='external', q='Rohan'), [])

    @override_settings(TIME_ZONE='Asia/Kolkata')
    def test_date_filters_use_local_day_and_include_boundaries(self):
        start = timezone.make_aware(datetime(2026, 10, 6))
        Counselling.objects.filter(pk=self.linked.pk).update(conducted_at=start)
        Counselling.objects.filter(pk=self.online.pk).update(conducted_at=start+timedelta(hours=23, minutes=59))
        Counselling.objects.filter(pk=self.visitor.pk).update(conducted_at=start-timedelta(seconds=1))
        self.assertEqual(self.ids(start_date='2026-10-06', end_date='2026-10-06'), [self.online.pk, self.linked.pk])
        self.assertEqual(self.ids(end_date='2026-10-05'), [self.visitor.pk])
        self.assertEqual(self.ids(start_date='2026-10-07'), [])

    def test_invalid_filters_show_errors_without_unfiltered_results(self):
        for params in [dict(start_date='bad'), dict(end_date='2026-99-01'), dict(caller='bad'),
                       dict(caller=999999), dict(counselling_type='BAD'), dict(channel='BAD'),
                       dict(start_date='2026-10-07', end_date='2026-10-06')]:
            with self.subTest(params=params):
                response = self.client.get(self.url, params)
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.context['filter_form'].errors)
                self.assertEqual(response.context['records'].paginator.count, 0)

    def test_pagination_keeps_filters_and_stable_order(self):
        Counselling.objects.bulk_create([Counselling(caller=self.caller, visitor_name=f'Paged {i}',
            counselling_type='WALK_IN', conducted_at=self.now) for i in range(24)])
        response = self.client.get(self.url, {'q': 'Paged', 'channel': 'external'})
        self.assertEqual(len(response.context['records']), 20)
        self.assertContains(response, 'q=Paged&amp;channel=external&amp;page=2')
        first = {record.pk for record in response.context['records']}
        second = self.ids(q='Paged', channel='external', page=2)
        self.assertEqual(len(second), 4)
        self.assertTrue(first.isdisjoint(second))
        self.assertEqual(len(self.ids(q='Paged', page='bad')), 20)

    def test_empty_state_and_safe_links_and_notes(self):
        self.assertContains(self.client.get(self.url, {'q': 'No such person'}), 'No counselling records found.')
        Counselling.objects.filter(pk=self.visitor.pk).update(notes='<script>alert(1)</script> javascript:alert(2) https://meet.google.com/abc-defg-hij')
        response = self.client.get(self.url)
        self.assertNotContains(response, '<script>alert(1)</script>')
        self.assertContains(response, '&lt;script&gt;alert(1)&lt;/script&gt;')
        self.assertNotContains(response, 'href="javascript:')
        self.assertContains(response, 'rel="noopener noreferrer"')
        self.assertEqual(meeting_links('javascript:alert(1) https://meet.google.com/abc-defg-hij. https://meet.google.com/abc-defg-hij'), ['https://meet.google.com/abc-defg-hij'])

    def test_read_only_no_data_or_points_changes(self):
        before = list(Counselling.objects.order_by('pk').values())
        lead_before = list(Lead.objects.values())
        points_before = list(PointsEntry.objects.values())
        self.client.get(self.url)
        self.assertEqual(self.client.post(self.url, {'notes': 'Overwrite'}).status_code, 405)
        self.assertEqual(self.client.delete(self.url).status_code, 405)
        self.assertEqual(list(Counselling.objects.order_by('pk').values()), before)
        self.assertEqual(list(Lead.objects.values()), lead_before)
        self.assertEqual(list(PointsEntry.objects.values()), points_before)

    def test_detail_for_lead_and_visitor(self):
        for record, name in [(self.linked, 'Rohan Verma'), (self.visitor, 'Ananya Sen')]:
            url = reverse('web:counselling-detail', args=[record.pk])
            self.assertContains(self.client.get(self.url), url)
            for user in [self.admin, self.manager, self.super_admin]:
                self.client.force_login(user)
                response = self.client.get(url)
                self.assertContains(response, name)
                self.assertContains(response, record.get_counselling_type_display())
                self.assertEqual(response.context['section'], 'counselling')
        response = self.client.get(reverse('web:counselling-detail', args=[self.linked.pk]))
        self.assertContains(response, 'https://meet.google.com/abc-defg-hij')
        self.assertContains(response, 'Example University')
        self.assertContains(response, 'MBBS')

    def test_detail_permissions_missing_record_and_read_only(self):
        url = reverse('web:counselling-detail', args=[self.linked.pk])
        for user in [self.caller, self.employee, self.accountant]:
            self.client.force_login(user)
            self.assertEqual(self.client.get(url).status_code, 403)
        self.client.logout()
        self.assertEqual(self.client.get(url).status_code, 302)
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(reverse('web:counselling-detail', args=[999999])).status_code, 404)
        before = list(Counselling.objects.order_by('pk').values())
        self.assertEqual(self.client.post(url, {'notes': 'Changed'}).status_code, 405)
        self.assertEqual(self.client.delete(url).status_code, 405)
        self.assertEqual(list(Counselling.objects.order_by('pk').values()), before)

    def test_detail_escapes_notes_and_handles_absent_meeting(self):
        url = reverse('web:counselling-detail', args=[self.visitor.pk])
        Counselling.objects.filter(pk=self.visitor.pk).update(notes='<script>alert(1)</script> javascript:alert(2)')
        response = self.client.get(url)
        self.assertContains(response, '&lt;script&gt;alert(1)&lt;/script&gt;')
        self.assertNotContains(response, '<script>alert(1)</script>')
        self.assertNotContains(response, 'href="javascript:')
        self.assertContains(response, 'No meeting link recorded.')
