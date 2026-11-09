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
    login_remark = models.CharField(max_length=500, blank=True, default='')

    class Meta:
        constraints = [models.UniqueConstraint(fields=['employee', 'date'], name='one_attendance_daily')]

    @property
    def hours(self):
        return round(((self.checked_out or timezone.now()) - self.checked_in).total_seconds() / 3600, 2) if self.checked_in else 0

    @property
    def is_late(self):
        if self.checked_in:
            local = timezone.localtime(self.checked_in)
            return (local.hour > 10) or (local.hour == 10 and local.minute > 0)
        return False


class AttendanceCorrectionRequest(models.Model):
    class RequestType(models.TextChoices):
        MISSING_CHECKOUT = "MISSING_CHECKOUT", "Missing Checkout"
        SHORT_SHIFT = "SHORT_SHIFT", "Incomplete / Short Shift"
        MISSED_START = "MISSED_START", "Missed Shift Start"
        WRONG_HOURS = "WRONG_HOURS", "Incorrect Recorded Hours"
        OTHER = "OTHER", "Other Discrepancy"
        LATE_ARRIVAL = "LATE_ARRIVAL", "Late Arrival Request"
        EARLY_SHIFT_END = "EARLY_SHIFT_END", "Early Shift-End Request"

    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending Review"
        APPROVED = "APPROVED", "Approved"
        REJECTED = "REJECTED", "Rejected"

    employee = models.ForeignKey(USER, on_delete=models.CASCADE, related_name="attendance_corrections")
    date = models.DateField(db_index=True)
    session = models.ForeignKey("activity.WorkSession", on_delete=models.SET_NULL, null=True, blank=True, related_name="correction_requests")
    attendance = models.ForeignKey("web.Attendance", on_delete=models.SET_NULL, null=True, blank=True, related_name="correction_requests")
    request_type = models.CharField(max_length=24, choices=RequestType.choices, default=RequestType.OTHER)
    reason = models.TextField(help_text="Detailed reason for attendance correction request.")
    requested_start = models.DateTimeField(null=True, blank=True)
    requested_end = models.DateTimeField(null=True, blank=True)
    requested_net_hours = models.DecimalField(max_digits=4, decimal_places=2, null=True, blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING, db_index=True)
    reviewed_by = models.ForeignKey(USER, on_delete=models.SET_NULL, null=True, blank=True, related_name="reviewed_attendance_corrections")
    reviewed_at = models.DateTimeField(null=True, blank=True)
    review_notes = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["employee", "date"]),
            models.Index(fields=["status", "date"]),
        ]

    def __str__(self):
        return f"Correction for {self.employee.username} on {self.date} ({self.status})"


class Holiday(models.Model):
    name = models.CharField(max_length=120)
    date = models.DateField(unique=True)


class WorkReport(models.Model):
    employee = models.ForeignKey(USER, on_delete=models.PROTECT, related_name='work_reports')
    date = models.DateField(default=timezone.localdate)
    work_link = models.URLField(blank=True, max_length=2000)
    work_links = models.JSONField(default=list, blank=True)
    project_reports = models.JSONField(default=list, blank=True)
    notes = models.TextField(blank=True)
    feedback = models.JSONField(default=dict)
    photo = models.BinaryField(null=True, blank=True)
    submitted_by = models.ForeignKey(USER, null=True, on_delete=models.SET_NULL, related_name='submitted_work_reports')
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['employee', 'date'], name='one_report_daily')]

    @property
    def all_work_links(self):
        return self.work_links or ([self.work_link] if self.work_link else [])

    @property
    def feedback_total(self):
        return sum(v for v in self.feedback.values() if isinstance(v, int))

    @property
    def total_project_duration_minutes(self):
        from django.db.models import Sum
        rel_sum = self.project_entries.aggregate(total=Sum('duration_minutes'))['total']
        if rel_sum is not None:
            return rel_sum
        return 0


class Project(models.Model):
    class Status(models.TextChoices):
        ASSIGNED = 'ASSIGNED', 'Assigned'
        STARTED = 'STARTED', 'Started'
        IN_PROGRESS = 'IN_PROGRESS', 'In progress'
        PENDING_REVIEW = 'PENDING_REVIEW', 'Pending review'
        NEEDS_CHANGES = 'NEEDS_CHANGES', 'Needs changes'
        COMPLETED = 'COMPLETED', 'Completed'
        ON_HOLD = 'ON_HOLD', 'On hold'
        CANCELLED = 'CANCELLED', 'Cancelled'

    class Priority(models.TextChoices):
        LOW = 'LOW', 'Low'
        MEDIUM = 'MEDIUM', 'Medium'
        HIGH = 'HIGH', 'High'
        URGENT = 'URGENT', 'Urgent'

    title = models.CharField(max_length=180)
    description = models.TextField(blank=True)
    employee = models.ForeignKey(USER, on_delete=models.PROTECT, related_name='projects')
    created_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name='created_projects')
    assigned_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name='assigned_projects')
    due_date = models.DateField(null=True, blank=True)
    priority = models.CharField(max_length=16, choices=Priority.choices, default=Priority.MEDIUM)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ASSIGNED)
    self_created = models.BooleanField(default=False, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    completion_requested_at = models.DateTimeField(null=True, blank=True)
    completion_reviewed_at = models.DateTimeField(null=True, blank=True)
    reviewed_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name='reviewed_projects')
    review_comments = models.TextField(blank=True)

    class Meta:
        indexes = [
            models.Index(fields=['employee', 'status']),
            models.Index(fields=['status', 'due_date']),
            models.Index(fields=['created_by', 'created_at']),
        ]

    def __str__(self):
        return f"{self.title} ({self.get_status_display()})"

    @property
    def is_overdue(self):
        if self.due_date and self.status not in {self.Status.COMPLETED, self.Status.CANCELLED}:
            return self.due_date < timezone.localdate()
        return False

    @property
    def can_request_completion(self):
        return self.status in {self.Status.STARTED, self.Status.IN_PROGRESS, self.Status.NEEDS_CHANGES}

    @property
    def total_duration_minutes(self):
        """Returns total duration in minutes aggregated exclusively from ProjectWorkReport."""
        if hasattr(self, '_total_duration_minutes'):
            return self._total_duration_minutes
        from django.db.models import Sum, Value
        from django.db.models.functions import Coalesce
        return self.work_reports.aggregate(total=Coalesce(Sum('duration_minutes'), Value(0)))['total']

    @total_duration_minutes.setter
    def total_duration_minutes(self, value):
        self._total_duration_minutes = value

    @property
    def total_duration_formatted(self):
        from apps.web.project_services import format_minutes_to_duration
        return format_minutes_to_duration(self.total_duration_minutes)

    def request_completion(self, user):
        """Allows assigned employee or creator to request project completion review."""
        if user != self.employee and user != self.created_by:
            if getattr(user, 'role', None) not in {'SUPER_ADMIN', 'ADMIN', 'MANAGER'}:
                raise PermissionError("Only assigned employee, project creator, or management can request completion.")
        if not self.can_request_completion:
            raise ValueError(f"Cannot request completion for project in status '{self.status}'.")
        self.status = self.Status.PENDING_REVIEW
        self.completion_requested_at = timezone.now()
        self.save(update_fields=['status', 'completion_requested_at', 'updated_at'])

    def review_completion(self, reviewer, action, comments=''):
        """Management review: action in ['APPROVE', 'NEEDS_CHANGES', 'REJECT']."""
        role = getattr(reviewer, 'role', None)
        if role not in {'SUPER_ADMIN', 'ADMIN', 'MANAGER'}:
            raise PermissionError("Only management can review completion requests.")
        if reviewer.pk == self.employee_id and role != 'SUPER_ADMIN':
            raise PermissionError("Employees cannot review their own project completion.")
        if self.status != self.Status.PENDING_REVIEW:
            raise ValueError(f"Project is not pending review (current: '{self.status}').")

        action = action.upper()
        if action == 'APPROVE':
            self.status = self.Status.COMPLETED
        elif action == 'NEEDS_CHANGES':
            self.status = self.Status.NEEDS_CHANGES
        elif action == 'REJECT':
            self.status = self.Status.IN_PROGRESS
        else:
            raise ValueError(f"Invalid review action: '{action}'.")

        self.reviewed_by = reviewer
        self.completion_reviewed_at = timezone.now()
        self.review_comments = comments
        self.save(update_fields=['status', 'reviewed_by', 'completion_reviewed_at', 'review_comments', 'updated_at'])

    def reopen(self, user, reason=''):
        """Allows management to reopen a completed project."""
        role = getattr(user, 'role', None)
        if role not in {'SUPER_ADMIN', 'ADMIN', 'MANAGER'}:
            raise PermissionError("Only management can reopen completed projects.")
        if self.status != self.Status.COMPLETED:
            raise ValueError(f"Only completed projects can be reopened (current: '{self.status}').")
        self.status = self.Status.IN_PROGRESS
        self.completion_reviewed_at = None
        reopen_note = f"[Reopened by {user.username} on {timezone.now().strftime('%Y-%m-%d %H:%M')}]"
        if reason:
            reopen_note += f": {reason}"
        self.review_comments = f"{self.review_comments}\n{reopen_note}".strip()
        self.save(update_fields=['status', 'completion_reviewed_at', 'review_comments', 'updated_at'])


class ProjectWorkReport(models.Model):
    class Status(models.TextChoices):
        DRAFT = 'DRAFT', 'Draft'
        SUBMITTED = 'SUBMITTED', 'Submitted'
        APPROVED = 'APPROVED', 'Approved'
        NEEDS_CORRECTION = 'NEEDS_CORRECTION', 'Needs correction'

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name='work_reports')
    employee = models.ForeignKey(USER, on_delete=models.PROTECT, related_name='project_work_reports')
    daily_report = models.ForeignKey(WorkReport, on_delete=models.SET_NULL, null=True, blank=True, related_name='project_entries')
    client_entry_id = models.CharField(
        max_length=64,
        null=True,
        blank=True,
        db_index=True,
        help_text="Client-provided UUID or deterministic hash for idempotent submission."
    )
    work_date = models.DateField(default=timezone.localdate, db_index=True)
    duration_minutes = models.PositiveIntegerField(
        validators=[MinValueValidator(1), MaxValueValidator(1440)],
        help_text="Duration of work in minutes (1 to 1440)"
    )
    description = models.TextField(help_text="Detailed progress description")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.SUBMITTED)
    reference_link = models.URLField(blank=True, max_length=2000)
    photo = models.BinaryField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    submitted_at = models.DateTimeField(default=timezone.now)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    reviewed_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name='reviewed_work_reports')
    review_comments = models.TextField(blank=True)
    legacy_raw_entry = models.JSONField(null=True, blank=True, help_text="Raw payload snapshot if mapped from legacy submission")

    class Meta:
        ordering = ['-work_date', '-created_at']
        constraints = [
            models.UniqueConstraint(
                fields=['employee', 'client_entry_id'],
                condition=models.Q(client_entry_id__isnull=False),
                name='unique_employee_client_entry_id'
            )
        ]
        indexes = [
            models.Index(fields=['project', 'work_date']),
            models.Index(fields=['employee', 'work_date']),
            models.Index(fields=['status', 'work_date']),
        ]

    def __str__(self):
        return f"{self.project.title} - {self.employee.username} ({self.work_date}: {self.duration_minutes}m)"

    @property
    def duration_hours(self):
        return round(self.duration_minutes / 60.0, 2)

    @property
    def duration_formatted(self):
        h = self.duration_minutes // 60
        m = self.duration_minutes % 60
        if h > 0 and m > 0:
            return f"{h}h {m}m"
        elif h > 0:
            return f"{h}h"
        return f"{m}m"

    def clean(self):
        super().clean()
        if self.status == self.Status.SUBMITTED and (self.duration_minutes is None or self.duration_minutes <= 0):
            from django.core.exceptions import ValidationError
            raise ValidationError({'duration_minutes': "Duration must be greater than zero for submitted entries."})
        if self.duration_minutes and self.duration_minutes > 1440:
            from django.core.exceptions import ValidationError
            raise ValidationError({'duration_minutes': "Duration cannot exceed 1440 minutes (24 hours)."})
        if self.project_id:
            if self.project.employee_id != self.employee_id and getattr(self.project, 'created_by_id', None) != self.employee_id:
                role = getattr(self.employee, 'role', None)
                if role not in {'SUPER_ADMIN', 'ADMIN', 'MANAGER'}:
                    from django.core.exceptions import ValidationError
                    raise ValidationError({'project': "You cannot submit work entries against a project assigned to another employee."})


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


class AttendancePhotoChallenge(models.Model):
    import uuid
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    employee = models.ForeignKey(USER, on_delete=models.CASCADE)
    action = models.CharField(max_length=8, choices=[('ENROLL', 'Enrollment'), ('IN', 'Check-in'), ('OUT', 'Check-out')])
    created_at = models.DateTimeField(auto_now_add=True)
    used = models.BooleanField(default=False)


class AttendancePhotoRequest(models.Model):
    employee = models.ForeignKey(USER, on_delete=models.PROTECT)
    action = models.CharField(max_length=8)
    photo = models.BinaryField()
    reference = models.ForeignKey('self', null=True, blank=True, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)
    status = models.CharField(max_length=10, default='PENDING')
    reviewer = models.ForeignKey(USER, null=True, on_delete=models.SET_NULL, related_name='reviewed_attendance_photos')
    reviewed_at = models.DateTimeField(null=True)
    review_note = models.CharField(max_length=1000, blank=True)
    match_score = models.FloatField(null=True, blank=True)


class Notice(models.Model):
    title = models.CharField(max_length=200)
    body = models.TextField(max_length=4000)
    # Empty list means "all employees" — a role-scoped announcement board, not a
    # conversation, so there's no per-recipient row to maintain as staff join/leave.
    roles = models.JSONField(default=list, blank=True)
    # Direct individual employee recipients when targeted specifically to selected individuals
    recipients = models.ManyToManyField(USER, blank=True, related_name='received_notices')
    created_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return self.title

    def is_visible_to(self, user):
        if not user or not user.is_authenticated:
            return False
        if self.created_by_id and self.created_by_id == user.pk:
            return True
        direct_recipients = self.recipients.all()
        if direct_recipients:
            return any(u.pk == user.pk for u in direct_recipients)
        return not self.roles or user.role in self.roles

    @property
    def has_individual_recipients(self):
        return bool(self.recipients.all())

    @property
    def audience_label(self):
        direct_recipients = list(self.recipients.all())
        if direct_recipients:
            count = len(direct_recipients)
            if count == 1:
                u = direct_recipients[0]
                name = u.get_full_name() or u.username
                return f"{name} ({u.username})" if u.get_full_name() and u.get_full_name() != u.username else name
            elif count <= 3:
                names = [u.get_full_name() or u.username for u in direct_recipients]
                return ', '.join(names)
            else:
                first_names = [direct_recipients[0].get_full_name() or direct_recipients[0].username,
                               direct_recipients[1].get_full_name() or direct_recipients[1].username]
                return f"{count} employees ({', '.join(first_names)} + {count - 2} more)"
        if not self.roles:
            return 'All employees'
        from apps.accounts.models import User
        labels = dict(User.Role.choices)
        return ', '.join(labels.get(role, role) for role in self.roles)


class NoticeAttachment(models.Model):
    notice = models.ForeignKey(Notice, on_delete=models.CASCADE, related_name='attachments')
    uploaded_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    file = models.FileField(upload_to='notice_attachments/%Y/%m/')
    original_filename = models.CharField(max_length=255)
    mime_type = models.CharField(max_length=100)
    file_size = models.PositiveIntegerField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['created_at']

    def __str__(self):
        return f"{self.original_filename} ({self.notice.title})"

