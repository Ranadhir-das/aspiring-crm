import os

with open('apps/web/models.py', 'r', encoding='utf-8') as f:
    content = f.read()

snippet = """class AttendanceCorrectionRequest(models.Model):
    class RequestType(models.TextChoices):
        MISSING_CHECKOUT = "MISSING_CHECKOUT", "Missing Checkout"
        SHORT_SHIFT = "SHORT_SHIFT", "Incomplete / Short Shift"
        MISSED_START = "MISSED_START", "Missed Shift Start"
        WRONG_HOURS = "WRONG_HOURS", "Incorrect Recorded Hours"
        OTHER = "OTHER", "Other Discrepancy"

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


"""

target = "class Holiday(models.Model):"
assert target in content, "target not found"
new_content = content.replace(target, snippet + target, 1)

with open('apps/web/models.py', 'w', encoding='utf-8', newline='') as f:
    f.write(new_content)
print("Successfully inserted AttendanceCorrectionRequest model.")

