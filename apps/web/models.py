from decimal import Decimal
from django.conf import settings
from django.core.validators import MinValueValidator, MaxValueValidator
from django.db import models
from django.db.models import Q, F
from django.utils import timezone

USER = settings.AUTH_USER_MODEL
FEEDBACK = [
    ('INTERESTED', 'Interested'), ('NOT_INTERESTED', 'Not interested'),
    ('NO_ANSWER', 'No answer / ringing'), ('BUSY', 'Busy / call waiting'),
    ('CALL_BACK', 'Call back / follow-up'), ('WRONG_NUMBER', 'Wrong number'),
    ('NOT_REACHABLE', 'Not reachable'), ('SWITCHED_OFF', 'Switched off'),
    ('DISCONNECTED', 'Disconnected'), ('INVALID', 'Invalid number'),
    ('ADMISSION_DONE', 'Admission done'), ('NO_CANDIDATE', 'No candidate'),
    ('LANGUAGE_ISSUE', 'Language issue'), ('CLOSED', 'Closed'),
]


class LeaveRequest(models.Model):
    class Status(models.TextChoices):
        PENDING = 'PENDING', 'Pending'
        APPROVED = 'APPROVED', 'Approved'
        REJECTED = 'REJECTED', 'Rejected'
        CANCELLED = 'CANCELLED', 'Cancelled'
    employee = models.ForeignKey(USER, on_delete=models.PROTECT, related_name='leave_requests')
    start_date = models.DateField()
    end_date = models.DateField()
    reason = models.TextField()
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.PENDING)
    reviewer = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name='reviewed_leaves')
    review_note = models.TextField(blank=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.CheckConstraint(condition=Q(end_date__gte=F('start_date')), name='leave_dates_ordered')]

    @property
    def days(self):
        return (self.end_date - self.start_date).days + 1


class Attendance(models.Model):
    employee = models.ForeignKey(USER, on_delete=models.PROTECT, related_name='attendance_records')
    date = models.DateField(default=timezone.localdate)
    status = models.CharField(max_length=16, choices=[('PRESENT', 'Present'), ('WFH', 'Work from home'), ('WEEK_OFF', 'Week off')], default='PRESENT')
    checked_in = models.DateTimeField(null=True, blank=True)
    checked_out = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['employee', 'date'], name='one_attendance_daily')]

    @property
    def hours(self):
        return round(((self.checked_out or timezone.now()) - self.checked_in).total_seconds() / 3600, 2) if self.checked_in else 0


class Holiday(models.Model):
    name = models.CharField(max_length=120)
    date = models.DateField(unique=True)


class WorkReport(models.Model):
    employee = models.ForeignKey(USER, on_delete=models.PROTECT, related_name='work_reports')
    date = models.DateField(default=timezone.localdate)
    work_link = models.URLField(blank=True)
    notes = models.TextField(blank=True)
    feedback = models.JSONField(default=dict)
    submitted_by = models.ForeignKey(USER, null=True, on_delete=models.SET_NULL, related_name='submitted_work_reports')
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['employee', 'date'], name='one_report_daily')]

    @property
    def feedback_total(self):
        return sum(v for v in self.feedback.values() if isinstance(v, int))


class Project(models.Model):
    title = models.CharField(max_length=180)
    description = models.TextField(blank=True)
    employee = models.ForeignKey(USER, on_delete=models.PROTECT, related_name='projects')
    assigned_by = models.ForeignKey(USER, null=True, on_delete=models.SET_NULL, related_name='assigned_projects')
    due_date = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=16, choices=[('ASSIGNED', 'Assigned'), ('STARTED', 'Started'), ('IN_PROGRESS', 'In progress'), ('COMPLETED', 'Completed')], default='ASSIGNED')
    created_at = models.DateTimeField(auto_now_add=True)


class Expense(models.Model):
    employee = models.ForeignKey(USER, on_delete=models.PROTECT, related_name='expenses')
    category = models.CharField(max_length=60, choices=[(s, s) for s in ['Grocery', 'Electrical Instrument', 'Tickets - Hotels - Travels', 'Office Belonging', 'Medicare', 'Celebrations', 'Other']])
    amount = models.DecimalField(max_digits=12, decimal_places=2, validators=[MinValueValidator(Decimal('0.01'))])
    date = models.DateField(default=timezone.localdate)
    description = models.TextField()
    voided = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)


class Payroll(models.Model):
    employee = models.ForeignKey(USER, on_delete=models.PROTECT, related_name='payroll_records')
    month = models.DateField(help_text='Choose any date in the payroll month.')
    payment_date = models.DateField()
    amount = models.DecimalField('Amount credited', max_digits=12, decimal_places=2, validators=[MinValueValidator(Decimal('0.01'))])
    deduction_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0, validators=[MinValueValidator(0)])
    deduction_reason = models.CharField(max_length=250, blank=True)
    recorded_by = models.ForeignKey(USER, null=True, on_delete=models.SET_NULL, related_name='recorded_payroll')
    created_at = models.DateTimeField(auto_now_add=True)


class PayrollQuery(models.Model):
    payment = models.ForeignKey(Payroll, on_delete=models.PROTECT, related_name='queries')
    query_text = models.TextField()
    resolution = models.TextField(blank=True)
    resolved_by = models.ForeignKey(USER, null=True, on_delete=models.SET_NULL)
    resolved_at = models.DateTimeField(null=True)
    created_at = models.DateTimeField(auto_now_add=True)


class AuditEvent(models.Model):
    actor = models.ForeignKey(USER, null=True, on_delete=models.SET_NULL)
    category = models.CharField(max_length=40)
    description = models.CharField(max_length=500)
    created_at = models.DateTimeField(auto_now_add=True)


class LeadCollaboration(models.Model):
    lead = models.ForeignKey('leads.Lead', on_delete=models.CASCADE, related_name='collaboration_notes')
    author = models.ForeignKey(USER, null=True, on_delete=models.SET_NULL)
    note = models.TextField()
    consult_admin = models.BooleanField(default=False)
    resolved = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)


class LeadQualification(models.Model):
    lead = models.OneToOneField('leads.Lead', on_delete=models.CASCADE, related_name='qualification')
    sub_status = models.CharField(max_length=40, blank=True, choices=[('', 'None')] + FEEDBACK)
    temperature = models.CharField(max_length=16, blank=True, choices=[('', 'None')] + [(v, v) for v in ['Very hot', 'Hot', 'Warm', 'Cold', 'Reject']])
    reminder_enabled = models.BooleanField(default=True)


class Customer(models.Model):
    name = models.CharField(max_length=180)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=30, blank=True)
    notes = models.TextField(blank=True)
    def __str__(self):
        return self.name


class Invoice(models.Model):
    customer = models.ForeignKey(Customer, on_delete=models.PROTECT, related_name='invoices')
    date = models.DateField(default=timezone.localdate)
    room_number = models.CharField(max_length=30, blank=True)
    tax_rate = models.DecimalField(max_digits=5, decimal_places=2, default=0, validators=[MinValueValidator(0), MaxValueValidator(100)])
    created_by = models.ForeignKey(USER, null=True, on_delete=models.SET_NULL)
    @property
    def subtotal(self):
        return sum((item.quantity * item.unit_price for item in self.items.all()), Decimal('0'))
    @property
    def total(self):
        return (self.subtotal * (Decimal('1') + Decimal(str(self.tax_rate)) / Decimal('100'))).quantize(Decimal('.01'))


class InvoiceItem(models.Model):
    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE, related_name='items')
    description = models.CharField(max_length=200)
    quantity = models.PositiveIntegerField(default=1, validators=[MinValueValidator(1)])
    unit_price = models.DecimalField(max_digits=12, decimal_places=2, validators=[MinValueValidator(0)])


class Payment(models.Model):
    invoice = models.ForeignKey(Invoice, on_delete=models.PROTECT, related_name='payments')
    date = models.DateField(default=timezone.localdate)
    amount = models.DecimalField(max_digits=12, decimal_places=2, validators=[MinValueValidator(Decimal('.01'))])
    mode = models.CharField(max_length=16, choices=[('CASH', 'Cash'), ('ONLINE', 'Online'), ('BANK', 'Bank transfer')])
    reference = models.CharField(max_length=150, blank=True)
    recorded_by = models.ForeignKey(USER, null=True, on_delete=models.SET_NULL)
