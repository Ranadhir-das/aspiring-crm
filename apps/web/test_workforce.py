from datetime import timedelta
from decimal import Decimal
from django.test import TestCase, Client
from django.utils import timezone
from apps.accounts.models import User
from apps.leads.models import Lead
from apps.calls.models import Call
from .models import (LeaveRequest, Attendance, WorkReport, Project, Expense,
                     Payroll, PayrollQuery, Customer, Invoice, InvoiceItem, Payment, FEEDBACK, AuditEvent)


class WorkforceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_user('admin', password='test', role='ADMIN')
        cls.manager = User.objects.create_user('manager', password='test', role='MANAGER')
        cls.employee = User.objects.create_user('employee', password='test', role='IT', first_name='Private employee')
        cls.other = User.objects.create_user('other', password='test', role='VIDEO_EDITOR')
        cls.caller = User.objects.create_user('caller', password='test', role='CALLER')
        cls.accountant = User.objects.create_user('accountant', password='test', role='ACCOUNTANT')
        cls.today = timezone.localdate()

    def login(self, user):
        self.client.force_login(user)

    def test_all_employee_roles_login_without_lead_access(self):
        for role in ['IT', 'VIDEO_EDITOR', 'EMPLOYEE', 'ACCOUNTANT']:
            user = User.objects.create_user('role_'+role, password='test', role=role)
            self.assertTrue(self.client.login(username=user.username, password='test'))
            self.assertEqual(self.client.get('/employee/').status_code, 200)
            self.assertEqual(self.client.get('/leads/').status_code, 403)

    def test_employee_pages_render(self):
        self.login(self.employee)
        for path in ['/employee/', '/leave/', '/leave/apply/', '/attendance/', '/holidays/', '/projects/', '/reports/', '/reports/new/', '/feedback/', '/payroll/']:
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 200)

    def test_management_finance_pages_render(self):
        self.login(self.admin)
        for path in ['/employees/', f'/employees/{self.employee.pk}/', '/holidays/new/', '/projects/new/', '/expenses/', '/expenses/new/', '/payroll/new/', '/activity/', '/customers/', '/customers/new/', '/invoices/', '/invoices/new/', '/consultations/', '/people/', '/finance/', '/leads/distribute/']:
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 200)

    def test_leave_validation_overlap_and_approval(self):
        self.login(self.employee)
        data = {'start_date': self.today, 'end_date': self.today + timedelta(days=2), 'reason': 'Family event'}
        self.assertEqual(self.client.post('/leave/apply/', data).status_code, 302)
        leave = LeaveRequest.objects.get(employee=self.employee)
        self.assertEqual(leave.days, 3)
        response = self.client.post('/leave/apply/', data)
        self.assertContains(response, 'overlaps')
        self.assertEqual(LeaveRequest.objects.count(), 1)
        self.assertEqual(self.client.post(f'/leave/{leave.pk}/action/', {'action': 'approve'}).status_code, 403)
        self.login(self.manager)
        self.client.post(f'/leave/{leave.pk}/action/', {'action': 'approve', 'review_note': 'Approved by manager'})
        leave.refresh_from_db()
        self.assertEqual(leave.status, 'APPROVED')
        self.assertEqual(leave.reviewer_id, self.manager.pk)
        self.client.post(f'/leave/{leave.pk}/action/', {'action': 'reject'})
        leave.refresh_from_db()
        self.assertEqual(leave.status, 'APPROVED')

    def test_leave_dates_and_isolation(self):
        leave = LeaveRequest.objects.create(employee=self.other, start_date=self.today, end_date=self.today, reason='Private reason')
        self.login(self.employee)
        self.assertNotContains(self.client.get('/leave/'), 'Private reason')
        self.assertEqual(self.client.post(f'/leave/{leave.pk}/action/', {'action': 'cancel'}).status_code, 403)
        response = self.client.post('/leave/apply/', {'start_date': self.today + timedelta(days=2), 'end_date': self.today, 'reason': 'Bad dates'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(LeaveRequest.objects.count(), 1)

    def test_attendance_repeated_checkin_and_checkout(self):
        self.login(self.employee)
        self.client.post('/attendance/action/', {'action': 'in', 'status': 'WFH'})
        self.client.post('/attendance/action/', {'action': 'in', 'status': 'PRESENT'})
        record = Attendance.objects.get(employee=self.employee)
        self.assertEqual(record.status, 'WFH')
        self.client.post('/attendance/action/', {'action': 'out'})
        record.refresh_from_db()
        self.assertIsNotNone(record.checked_out)
        self.assertGreaterEqual(record.hours, 0)

    def report_data(self, **extra):
        return {'date': self.today, 'notes': 'Daily progress', **{'feedback_'+k: 0 for k, _ in FEEDBACK}, **extra}

    def test_reports_persist_edit_and_do_not_duplicate(self):
        self.login(self.employee)
        data = self.report_data(feedback_INTERESTED=4)
        self.assertEqual(self.client.post('/reports/new/', data).status_code, 302)
        report = WorkReport.objects.get(employee=self.employee)
        self.assertEqual(report.feedback_total, 4)
        self.client.post('/reports/new/', data)
        self.assertEqual(WorkReport.objects.count(), 1)
        self.client.post(f'/reports/{report.pk}/', self.report_data(feedback_INTERESTED=5))
        report.refresh_from_db()
        self.assertEqual(report.feedback_total, 5)
        self.login(self.other)
        self.assertEqual(self.client.get(f'/reports/{report.pk}/').status_code, 404)
        self.assertNotContains(self.client.get('/reports/?export=csv'), 'Daily progress')

    def test_manual_feedback_and_calls_are_not_added_together(self):
        lead = Lead.objects.create(name='Reassigned', phone='9876543210', assigned_caller=self.other)
        Call.objects.create(lead=lead, caller=self.caller, started_at=timezone.now(), outcome='INTERESTED')
        WorkReport.objects.create(employee=self.caller, date=self.today, feedback={'INTERESTED': 5})
        self.login(self.caller)
        self.assertEqual(self.client.get('/feedback/').context['total'], 1)
        self.assertEqual(self.client.get('/feedback/?source=manual').context['total'], 5)
        self.login(self.employee)
        self.assertEqual(self.client.get('/feedback/').context['total'], 0)
        self.assertEqual(self.client.get(f'/feedback/?employee={self.caller.pk}').context['total'], 0)

    def test_payroll_privacy_and_query_ownership(self):
        payment = Payroll.objects.create(employee=self.employee, month=self.today, payment_date=self.today, amount=20000)
        self.login(self.other)
        self.assertNotContains(self.client.get('/payroll/'), '20000')
        self.assertEqual(self.client.post(f'/payroll/{payment.pk}/query/', {'query_text': 'Forged'}).status_code, 404)
        self.login(self.manager)
        self.assertNotContains(self.client.get('/payroll/'), '20000')
        self.assertEqual(self.client.get('/payroll/new/').status_code, 403)
        self.login(self.employee)
        self.client.post(f'/payroll/{payment.pk}/query/', {'query_text': 'Explain deduction'})
        query = PayrollQuery.objects.get(payment=payment)
        self.assertEqual(self.client.post(f'/payroll/queries/{query.pk}/resolve/', {'resolution': 'Self approve'}).status_code, 403)
        self.login(self.accountant)
        self.client.post(f'/payroll/queries/{query.pk}/resolve/', {'resolution': 'Breakdown provided'})
        query.refresh_from_db()
        self.assertIsNotNone(query.resolved_at)

    def test_role_escalation_and_self_edit_blocked(self):
        self.login(self.manager)
        self.assertEqual(self.client.get(f'/employees/{self.employee.pk}/').status_code, 403)
        self.login(self.admin)
        self.assertEqual(self.client.get(f'/employees/{self.admin.pk}/').status_code, 403)
        self.client.post(f'/employees/{self.employee.pk}/', {'role': 'SUPER_ADMIN', 'is_active': 'on'})
        self.employee.refresh_from_db()
        self.assertEqual(self.employee.role, 'IT')

    def test_project_update_and_finance_protection(self):
        project = Project.objects.create(title='Video work', employee=self.other)
        self.login(self.employee)
        self.assertEqual(self.client.post(f'/projects/{project.pk}/status/', {'status': 'COMPLETED'}).status_code, 404)
        for path in ['/expenses/', '/invoices/', '/customers/', '/activity/']:
            self.assertEqual(self.client.get(path).status_code, 403)

    def test_invoice_decimal_totals_and_overpayment(self):
        customer = Customer.objects.create(name='Test customer')
        invoice = Invoice.objects.create(customer=customer, tax_rate=18)
        InvoiceItem.objects.create(invoice=invoice, description='Service', quantity=2, unit_price=Decimal('125.50'))
        self.assertEqual(invoice.total, Decimal('296.18'))
        self.login(self.accountant)
        self.assertEqual(self.client.get(f'/invoices/{invoice.pk}/').status_code, 200)
        self.client.post(f'/invoices/{invoice.pk}/', {'date': self.today, 'amount': '300', 'mode': 'CASH'})
        self.assertEqual(Payment.objects.count(), 0)
        self.client.post(f'/invoices/{invoice.pk}/', {'date': self.today, 'amount': '296.18', 'mode': 'CASH'})
        self.assertEqual(Payment.objects.count(), 1)

    def test_registration_inactive_and_csrf_enforced(self):
        response = self.client.post('/register/', {'username': 'newstaff', 'password1': 'Secure-test-pass-912', 'password2': 'Secure-test-pass-912', 'role': 'ADMIN'})
        self.assertEqual(response.status_code, 200)
        new = User.objects.get(username='newstaff')
        self.assertFalse(new.is_active)
        self.assertEqual(new.role, 'EMPLOYEE')
        self.assertFalse(self.client.login(username='newstaff', password='Secure-test-pass-912'))
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.employee)
        self.assertEqual(client.post('/attendance/action/', {'action': 'in'}).status_code, 403)

    def test_distribution_preview_and_capacity_are_atomic(self):
        self.login(self.manager)
        lead = Lead.objects.create(name='Allocate me', phone='9876543233', source='Website')
        data = {'source': 'Website', f'caller_{self.caller.pk}': 2, 'action': 'assign'}
        self.assertContains(self.client.post('/leads/distribute/', data), 'Only 1 matching leads')
        lead.refresh_from_db()
        self.assertIsNone(lead.assigned_caller_id)
        data[f'caller_{self.caller.pk}'] = 1
        data['action'] = 'preview'
        self.assertContains(self.client.post('/leads/distribute/', data), 'Allocation preview')
        lead.refresh_from_db()
        self.assertIsNone(lead.assigned_caller_id)
        data['action'] = 'assign'
        self.client.post('/leads/distribute/', data)
        lead.refresh_from_db()
        self.assertEqual(lead.assigned_caller_id, self.caller.pk)
        self.assertEqual(lead.assignment_history.count(), 1)

    def test_invoice_creation_and_required_items(self):
        self.login(self.accountant)
        customer = Customer.objects.create(name='Invoice customer')
        data = {'customer': customer.pk, 'date': self.today, 'tax_rate': '0', 'items-TOTAL_FORMS': '1', 'items-INITIAL_FORMS': '0',
                'items-MIN_NUM_FORMS': '1', 'items-MAX_NUM_FORMS': '50', 'items-0-description': 'Consulting', 'items-0-quantity': '2', 'items-0-unit_price': '50.25'}
        response = self.client.post('/invoices/new/', data)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Invoice.objects.get().total, Decimal('100.50'))

    def test_populated_views_and_invalid_date_filters(self):
        from .models import Holiday, LeadCollaboration, LeadQualification
        Holiday.objects.create(name='Holiday test', date=self.today)
        Expense.objects.create(employee=self.accountant, category='Other', date=self.today, amount=10, description='Example expense')
        payroll = Payroll.objects.create(employee=self.employee, month=self.today, payment_date=self.today, amount=100)
        PayrollQuery.objects.create(payment=payroll, query_text='Question')
        WorkReport.objects.create(employee=self.employee, date=self.today, feedback={'INTERESTED': 3})
        lead = Lead.objects.create(name='Consultation', phone='9876543255', assigned_caller=self.caller)
        LeadCollaboration.objects.create(lead=lead, author=self.caller, note='Need support', consult_admin=True)
        self.login(self.admin)
        for path in ['/holidays/', '/expenses/', '/payroll/', '/reports/', '/feedback/?source=manual', '/consultations/', f'/leads/{lead.pk}/collaboration/', '/people/', '/finance/']:
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 200)
        self.assertFalse(LeadQualification.objects.exists(), 'Reading a page must not create a qualification')
        response = self.client.get('/reports/?start_date=bad-date')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['records'].paginator.count, 0)
