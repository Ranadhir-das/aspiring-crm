from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    registration_pending = models.BooleanField(default=False)
    class Role(models.TextChoices):
        SUPER_ADMIN = "SUPER_ADMIN", "Super Admin"
        ADMIN = "ADMIN", "Admin"
        MANAGER = "MANAGER", "Manager"
        CALLER = "CALLER", "Caller"
        IT = "IT", "IT"
        VIDEO_EDITOR = "VIDEO_EDITOR", "Video Editor"
        ACCOUNTANT = "ACCOUNTANT", "Accountant"
        EMPLOYEE = "EMPLOYEE", "Employee"

    role = models.CharField(
        max_length=30,
        choices=Role.choices,
        default=Role.CALLER,
    )

    manager = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="team_members",
        limit_choices_to={
            "role__in": [
                "ADMIN",
                "MANAGER",
            ]
        },
    )

    phone = models.CharField(
        max_length=20,
        blank=True,
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
    )

    updated_at = models.DateTimeField(
        auto_now=True,
    )

    def __str__(self):
        name = self.get_full_name()

        if name:
            return f"{name} ({self.get_role_display()})"

        return f"{self.username} ({self.get_role_display()})"


class CallerSession(models.Model):
    import uuid
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    caller = models.ForeignKey(User, on_delete=models.CASCADE, related_name='app_sessions')
    logged_in_at = models.DateTimeField(auto_now_add=True)
    logged_out_at = models.DateTimeField(null=True, blank=True)
    last_seen = models.DateTimeField(auto_now_add=True)
    foreground = models.BooleanField(default=True)
    active_seconds = models.FloatField(default=0)
    verified_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    end_reason = models.CharField(max_length=20, blank=True)
    attendance = models.ForeignKey('web.Attendance', null=True, blank=True, on_delete=models.SET_NULL, related_name='sessions')
    verification = models.ForeignKey('web.AttendancePhotoRequest', null=True, blank=True, on_delete=models.SET_NULL)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    latitude = models.FloatField(null=True, blank=True)
    longitude = models.FloatField(null=True, blank=True)

    @property
    def active_display(self):
        seconds = int(self.active_seconds)
        return f'{seconds // 3600}h {(seconds % 3600) // 60}m {seconds % 60}s'

    @property
    def has_location(self):
        return self.latitude is not None and self.longitude is not None

    @property
    def map_url(self):
        return f'https://www.google.com/maps?q={self.latitude},{self.longitude}' if self.has_location else ''

    @property
    def online(self):
        from django.utils import timezone
        return not self.logged_out_at and self.foreground and (timezone.now() - self.last_seen).total_seconds() < 90
