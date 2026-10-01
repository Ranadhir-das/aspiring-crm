from datetime import timedelta

from django.apps import apps
from django.contrib import admin
from django.db import IntegrityError, transaction, connection
from django.db.models.deletion import ProtectedError
from django.test import TestCase, RequestFactory
from django.utils import timezone
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from apps.accounts.admin import CustomUserAdmin, EmployeeServiceInline
from apps.accounts.models import User, EmployeeService, CallerSession
from apps.leads.models import Service, Lead, LeadAssignmentHistory
from apps.leads.public_intake import create_website_lead
from apps.leads.services import bulk_assign_leads


class ServiceEligibilityTests(TestCase):
    def setUp(self):
        self.api = APIClient()
        self.caller = User.objects.create_user('service-caller', role='CALLER', designation='Senior Counsellor')
        self.other = User.objects.create_user('service-other', role='CALLER')
        self.mbbs = Service.objects.get(code='MBBS')
        self.mba = Service.objects.get(code='MBA')

    def test_initial_catalog_and_public_read_only_access(self):
        response = self.api.get('/api/v1/public/services/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual({s['code'] for s in response.data}, {'MBBS', 'MBA', 'APOSTILLE', 'OTHER'})
        self.assertEqual(set(response.data[0]), {'id', 'name', 'code', 'description'})
        self.assertEqual(self.api.post('/api/v1/public/services/', {}).status_code, 405)
        self.mba.is_active = False
        self.mba.save()
        self.assertNotIn('MBA', {s['code'] for s in self.api.get('/api/v1/public/services/').data})

    def test_multiple_services_unique_mapping_and_removal(self):
        self.caller.services.add(self.mbbs, self.mba)
        self.assertEqual(self.caller.services.count(), 2)
        with self.assertRaises(IntegrityError), transaction.atomic():
            EmployeeService.objects.create(employee=self.caller, service=self.mbbs)
        self.caller.services.remove(self.mba)
        self.assertEqual(list(self.caller.services.all()), [self.mbbs])
        self.assertEqual(self.caller.designation, 'Senior Counsellor')
        self.assertEqual(self.other.designation, '')

    def test_caller_services_are_isolated_active_and_not_writable(self):
        self.caller.services.add(self.mbbs)
        self.other.services.add(self.mba)
        self.api.force_authenticate(self.caller)
        url = '/api/v1/mobile/me/services/'
        response = self.api.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual([s['code'] for s in response.data], ['MBBS'])
        self.assertEqual(self.api.post(url, {'service': self.mba.pk}).status_code, 405)
        self.mbbs.is_active = False
        self.mbbs.save()
        self.assertEqual(self.api.get(url).data, [])
        self.assertTrue(self.caller.services.filter(pk=self.mbbs.pk).exists())

    def test_authentication_role_and_work_session_are_preserved(self):
        url = '/api/v1/mobile/me/services/'
        self.assertEqual(self.api.get(url).status_code, 401)
        employee = User.objects.create_user('non-caller', role='EMPLOYEE')
        self.api.force_authenticate(employee)
        self.assertEqual(self.api.get(url).status_code, 403)
        self.api.force_authenticate(None)
        token = Token.objects.create(user=self.caller)
        self.api.credentials(HTTP_AUTHORIZATION=f'Token {token.key}')
        self.assertEqual(self.api.get(url).status_code, 401)
        session = CallerSession.objects.create(caller=self.caller, verified_at=timezone.now(),
                                              expires_at=timezone.now() + timedelta(hours=1))
        self.assertEqual(self.api.get(url).status_code, 200)
        session.expires_at = timezone.now() - timedelta(seconds=1)
        session.save()
        self.assertEqual(self.api.get(url).status_code, 401)

    def test_website_service_resolution_preserves_phase_one_text(self):
        for n, (text, code) in enumerate([('mbbs', 'MBBS'), (' MBA ', 'MBA'), ('APOSTILLE', 'APOSTILLE'),
                                         ('', 'OTHER'), ('Unlisted enquiry', 'OTHER')]):
            create_website_lead({'name': 'Visitor', 'phone': f'987654320{n}', 'service': text})
            lead = Lead.objects.get(phone=f'987654320{n}')
            self.assertEqual(lead.service_type.code, code)
            self.assertEqual(lead.service, text)
            self.assertIsNone(lead.assigned_caller)
        custom = Service.objects.create(code='DENTAL', name='Dental admissions')
        create_website_lead({'name': 'Visitor', 'phone': '9876543299', 'service': 'Dental admissions'})
        self.assertEqual(Lead.objects.get(phone='9876543299').service_type, custom)
        self.assertFalse(LeadAssignmentHistory.objects.exists())

    def test_inactive_service_and_disabled_fallback_rejected(self):
        from rest_framework.exceptions import ValidationError
        self.mbbs.is_active = False
        self.mbbs.save()
        with self.assertRaises(ValidationError):
            create_website_lead({'name': 'Visitor', 'phone': '9876543210', 'service': 'MBBS'})
        Service.objects.filter(code='OTHER').update(is_active=False)
        with self.assertRaises(ValidationError):
            create_website_lead({'name': 'Visitor', 'phone': '9876543210'})
        self.assertFalse(Lead.objects.exists())

    def test_duplicate_does_not_change_service_or_assignment(self):
        lead = Lead.objects.create(name='Existing', phone='9876543210', service_type=self.mba,
                                   assigned_caller=self.other)
        create_website_lead({'name': 'Visitor', 'phone': '9876543210', 'service': 'MBBS'})
        lead.refresh_from_db()
        self.assertEqual(lead.service_type, self.mba)
        self.assertEqual(lead.assigned_caller, self.other)
        self.assertEqual(Lead.objects.count(), 1)
        with self.assertRaises(ProtectedError):
            self.mba.delete()

    def test_manual_assignment_does_not_require_service_mapping(self):
        lead = Lead.objects.create(name='Batch style', phone='9876543210')
        manager = User.objects.create_user('service-manager', role='MANAGER')
        result = bulk_assign_leads([lead.pk], self.caller, manager)
        self.assertEqual(result['assigned_count'], 1)
        lead.refresh_from_db()
        self.assertIsNone(lead.service_type)
        self.assertEqual(lead.assigned_caller, self.caller)

    def test_admin_forms_support_designation_and_service_mapping(self):
        superuser = User.objects.create_superuser('service-admin', 'admin@example.com', 'password')
        self.client.force_login(superuser)
        response = self.client.get(f'/admin/accounts/user/{self.caller.pk}/change/')
        self.assertContains(response, 'name="designation"')
        self.assertContains(response, 'service_mappings-TOTAL_FORMS')
        self.assertIn(EmployeeServiceInline, CustomUserAdmin.inlines)
        response = self.client.post('/admin/leads/service/add/', {
            'name': 'New service', 'code': 'NEW_SERVICE', 'description': 'Description', 'is_active': 'on', '_save': 'Save'})
        self.assertEqual(response.status_code, 302)
        self.assertTrue(Service.objects.filter(code='NEW_SERVICE').exists())
        request = RequestFactory().get('/')
        request.user = superuser
        inline = EmployeeServiceInline(User, admin.site)
        FormSet = inline.get_formset(request, self.caller)
        prefix = FormSet.get_default_prefix()
        data = {f'{prefix}-TOTAL_FORMS': '1', f'{prefix}-INITIAL_FORMS': '0',
                f'{prefix}-0-service': str(self.mbbs.pk)}
        formset = FormSet(data, instance=self.caller)
        self.assertTrue(formset.is_valid(), formset.errors)
        formset.save()
        mapping = self.caller.service_mappings.get()
        data.update({f'{prefix}-INITIAL_FORMS': '1', f'{prefix}-0-id': str(mapping.pk),
                     f'{prefix}-0-DELETE': 'on'})
        formset = FormSet(data, instance=self.caller)
        self.assertTrue(formset.is_valid(), formset.errors)
        formset.save()
        self.assertFalse(self.caller.services.exists())
        self.client.force_login(self.caller)
        self.assertEqual(self.client.get('/admin/leads/service/').status_code, 302)

    def test_seed_migration_preserves_legacy_text_and_batch_assignment(self):
        from importlib import import_module
        from types import SimpleNamespace
        from apps.leads.models import LeadImportBatch
        Service.objects.all().delete()
        batch = LeadImportBatch.objects.create(filename='old.csv')
        legacy = Lead.objects.create(name='Batch lead', phone='0012345678', import_batch=batch,
                                      assigned_caller=self.caller, status='INTERESTED')
        known = Lead.objects.create(name='Website', phone='9876543210', service=' mbbs ', source='website')
        unknown = Lead.objects.create(name='Other enquiry', phone='9876543211', service='Legacy free text')
        empty = Lead.objects.create(name='Website', phone='9876543212', source='website')
        stamp = legacy.updated_at
        migration = import_module('apps.leads.migrations.0008_service_eligibility')
        migration.seed_services(apps, SimpleNamespace(connection=connection))
        for lead in [legacy, known, unknown, empty]:
            lead.refresh_from_db()
        self.assertEqual(Service.objects.count(), 4)
        self.assertIsNone(legacy.service_type)
        self.assertEqual(legacy.import_batch_id, batch.pk)
        self.assertEqual(legacy.phone, '0012345678')
        self.assertEqual(legacy.status, 'INTERESTED')
        self.assertEqual(legacy.assigned_caller_id, self.caller.pk)
        self.assertEqual(legacy.updated_at, stamp)
        self.assertEqual(known.service, ' mbbs ')
        self.assertEqual(known.service_type.code, 'MBBS')
        self.assertEqual(unknown.service_type.code, 'OTHER')
        self.assertEqual(unknown.service, 'Legacy free text')
        self.assertEqual(empty.service_type.code, 'OTHER')
