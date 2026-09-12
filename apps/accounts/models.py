from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
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
