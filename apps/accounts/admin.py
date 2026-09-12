from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import User


@admin.register(User)
class CustomUserAdmin(UserAdmin):

    list_display = (
        "username",
        "email",
        "get_full_name",
        "role",
        "manager",
        "is_active",
        "created_at",
    )

    list_filter = (
        "role",
        "is_active",
        "is_staff",
    )

    search_fields = (
        "username",
        "email",
        "first_name",
        "last_name",
        "phone",
    )

    ordering = (
        "-created_at",
    )

    fieldsets = UserAdmin.fieldsets + (
        (
            "CRM Information",
            {
                "fields": (
                    "role",
                    "manager",
                    "phone",
                )
            },
        ),
    )

    add_fieldsets = UserAdmin.add_fieldsets + (
        (
            "CRM Information",
            {
                "fields": (
                    "role",
                    "manager",
                    "phone",
                )
            },
        ),
    )

    @admin.display(description="Name")
    def get_full_name(self, obj):
        return obj.get_full_name() or "-"