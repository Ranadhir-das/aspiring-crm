from datetime import timedelta
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient
from apps.accounts.models import User, CallerSession
from apps.web.models import WorkReport, Project, ProjectWorkReport
from apps.accounts.api.photo_tickets import (
    generate_photo_ticket,
    resolve_photo_ticket_user,
    DEFAULT_TICKET_TTL,
)
from django.core.signing import BadSignature, SignatureExpired
from django.core.exceptions import PermissionDenied
import base64
from rest_framework.authtoken.models import Token


class PhotoTicketAuthenticationTests(TestCase):
    def setUp(self):
        self.now = timezone.now()
        # Test 1x1 JPEG byte data
        self.sample_jpeg = b'\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x01\x00H\x00H\x00\x00\xff\xdb\x00C\x00\x08\x06\x06\x07\x06\x05\x08\x07\x07\x07\t\t\x08\n\x0c\x14\r\x0c\x0b\x0b\x0c\x19\x12\x13\x0f\x14\x1d\x1a\x1f\x1e\x1d\x1a\x1c\x1c $.\' \",#\x1c\x1c(7),01444\x1f\'9=82<.342\xff\xc0\x00\x0b\x08\x00\x01\x00\x01\x01\x01\x11\x00\xff\xc4\x00\x1f\x00\x00\x01\x05\x01\x01\x01\x01\x01\x01\x00\x00\x00\x00\x00\x00\x00\x00\x01\x02\x03\x04\x05\x06\x07\x08\t\n\x0b\xff\xda\x00\x08\x01\x01\x00\x00?\x00\xbf\x00\xff\xd9'

        # Users
        self.emp1 = User.objects.create_user(username='emp_alice', role='EMPLOYEE')
        self.emp2 = User.objects.create_user(username='emp_bob', role='EMPLOYEE')
        self.manager = User.objects.create_user(username='mgr_carol', role='MANAGER')

        # Tokens
        self.token_emp1, _ = Token.objects.get_or_create(user=self.emp1)
        self.token_emp2, _ = Token.objects.get_or_create(user=self.emp2)
        self.token_mgr, _ = Token.objects.get_or_create(user=self.manager)

        # Verified Caller Sessions
        CallerSession.objects.create(
            caller=self.emp1,
            verified_at=self.now,
            expires_at=self.now + timedelta(hours=8),
        )
        CallerSession.objects.create(
            caller=self.emp2,
            verified_at=self.now,
            expires_at=self.now + timedelta(hours=8),
        )
        CallerSession.objects.create(
            caller=self.manager,
            verified_at=self.now,
            expires_at=self.now + timedelta(hours=8),
        )

        # WorkReport with photo for emp1
        self.report1 = WorkReport.objects.create(
            employee=self.emp1,
            date=self.now.date(),
            notes="Alice report",
            photo=self.sample_jpeg,
        )

        # WorkReport with photo for emp2
        self.report2 = WorkReport.objects.create(
            employee=self.emp2,
            date=self.now.date(),
            notes="Bob report",
            photo=self.sample_jpeg,
        )

        # Project and ProjectWorkReport
        self.project = Project.objects.create(
            title="Alpha Project",
            employee=self.emp1,
            created_by=self.emp1,
            status=Project.Status.IN_PROGRESS,
        )
        self.pwr1 = ProjectWorkReport.objects.create(
            project=self.project,
            employee=self.emp1,
            duration_minutes=60,
            description="Alice task",
            photo=self.sample_jpeg,
        )

        self.client = APIClient()

    def test_01_valid_ticket_allows_photo_access(self):
        ticket = generate_photo_ticket(self.emp1, 'work_report', self.report1.pk)
        resolved_user = resolve_photo_ticket_user(ticket, 'work_report', self.report1.pk)
        self.assertEqual(resolved_user, self.emp1)

        # Call endpoint without Authorization header, using ?ticket=
        response = self.client.get(f"/api/v1/mobile/employee/reports/{self.report1.pk}/photo/?ticket={ticket}")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'image/jpeg')
        self.assertEqual(response.content, self.sample_jpeg)

    def test_02_expired_ticket_rejected(self):
        ticket = generate_photo_ticket(self.emp1, 'work_report', self.report1.pk)
        # Calling with max_age=-1 forces expiration
        with self.assertRaises(SignatureExpired):
            resolve_photo_ticket_user(ticket, 'work_report', self.report1.pk, max_age=-1)

        # HTTP request check: simulate an expired ticket
        response = self.client.get(f"/api/v1/mobile/employee/reports/{self.report1.pk}/photo/?ticket={ticket}:fake")
        self.assertEqual(response.status_code, 403)

    def test_03_tampered_signature_rejected(self):
        ticket = generate_photo_ticket(self.emp1, 'work_report', self.report1.pk)
        tampered = ticket[:-4] + "xxxx"
        with self.assertRaises(BadSignature):
            resolve_photo_ticket_user(tampered, 'work_report', self.report1.pk)

        response = self.client.get(f"/api/v1/mobile/employee/reports/{self.report1.pk}/photo/?ticket={tampered}")
        self.assertEqual(response.status_code, 403)

    def test_04_wrong_photo_id_rejected(self):
        # Ticket issued for report1 (Alice), but presented for report2 (Bob)
        ticket = generate_photo_ticket(self.emp1, 'work_report', self.report1.pk)
        with self.assertRaises(PermissionDenied):
            resolve_photo_ticket_user(ticket, 'work_report', self.report2.pk)

        response = self.client.get(f"/api/v1/mobile/employee/reports/{self.report2.pk}/photo/?ticket={ticket}")
        self.assertEqual(response.status_code, 403)

    def test_05_wrong_photo_type_rejected(self):
        # Ticket issued for work_report, but requested for project_work_report
        ticket = generate_photo_ticket(self.emp1, 'work_report', self.report1.pk)
        with self.assertRaises(PermissionDenied):
            resolve_photo_ticket_user(ticket, 'project_work_report', self.report1.pk)

    def test_06_wrong_employee_ownership_rejected(self):
        # Emp2 generates a ticket for Bob's report, but attempts to forge/request Alice's report
        # If Bob generates a ticket for Alice's report:
        ticket = generate_photo_ticket(self.emp2, 'work_report', self.report1.pk)
        # Ticket itself is signed, but when presented to Alice's report, object-level check fails!
        response = self.client.get(f"/api/v1/mobile/employee/reports/{self.report1.pk}/photo/?ticket={ticket}")
        self.assertEqual(response.status_code, 404)  # Emp2 has no access to Emp1's report (404/403)

    def test_07_manager_can_access_employee_photo_with_ticket(self):
        # Manager generates a ticket for Alice's report
        mgr_ticket = generate_photo_ticket(self.manager, 'work_report', self.report1.pk)
        response = self.client.get(f"/api/v1/mobile/employee/reports/{self.report1.pk}/photo/?ticket={mgr_ticket}")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, self.sample_jpeg)

    def test_08_standard_authorization_header_works(self):
        # Normal authenticated request via Authorization: Token ...
        self.client.credentials(HTTP_AUTHORIZATION=f'Token {self.token_emp1.key}')
        response = self.client.get(f"/api/v1/mobile/employee/reports/{self.report1.pk}/photo/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, self.sample_jpeg)

    def test_09_unauthenticated_request_rejected(self):
        self.client.credentials()  # Clear auth
        response = self.client.get(f"/api/v1/mobile/employee/reports/{self.report1.pk}/photo/")
        self.assertEqual(response.status_code, 401)

    def test_10_general_token_query_param_rejected(self):
        # Passing ?token=<long_lived_auth_token> MUST FAIL on standard endpoints now that fallback is removed
        self.client.credentials()
        response = self.client.get(f"/api/v1/mobile/employee/projects/?token={self.token_emp1.key}")
        self.assertEqual(response.status_code, 401)

    def test_11_project_work_report_photo_ticket_and_header(self):
        # Test project work report photo API with header
        self.client.credentials(HTTP_AUTHORIZATION=f'Token {self.token_emp1.key}')
        response = self.client.get(f"/api/v1/mobile/employee/projects/work-reports/{self.pwr1.pk}/photo/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, self.sample_jpeg)

        # Test with ticket
        ticket = generate_photo_ticket(self.emp1, 'project_work_report', self.pwr1.pk)
        self.client.credentials()
        response = self.client.get(f"/api/v1/mobile/employee/projects/work-reports/{self.pwr1.pk}/photo/?ticket={ticket}")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, self.sample_jpeg)

    def test_12_ticket_generation_endpoint(self):
        # Request a short-lived ticket via ?ticket=get with header authentication
        self.client.credentials(HTTP_AUTHORIZATION=f'Token {self.token_emp1.key}')
        response = self.client.get(f"/api/v1/mobile/employee/reports/{self.report1.pk}/photo/?ticket=get")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn('ticket', data)
        self.assertEqual(data['expires_in'], DEFAULT_TICKET_TTL)
        self.assertIn('photo_url', data)

        # Unauthenticated request for a ticket must fail
        self.client.credentials()
        response = self.client.get(f"/api/v1/mobile/employee/reports/{self.report1.pk}/photo/?ticket=get")
        self.assertEqual(response.status_code, 401)
