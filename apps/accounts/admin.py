from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import User, EmployeeService, PushDevice


@admin.register(PushDevice)
class PushDeviceAdmin(admin.ModelAdmin):
    list_display = ('user', 'platform', 'device_name', 'masked_token', 'active', 'last_seen', 'created_at', 'updated_at')
    list_filter = ('active', 'platform')
    search_fields = ('user__username', 'device_name')
    readonly_fields = ('user', 'expo_push_token', 'platform', 'device_name', 'last_seen', 'created_at', 'updated_at')
    list_select_related = ('user',)

    def has_add_permission(self, request):
        return False


class EmployeeServiceInline(admin.TabularInline):
    model = EmployeeService
    extra = 1
    autocomplete_fields = ('service',)


@admin.register(User)
class CustomUserAdmin(UserAdmin):
    inlines = (EmployeeServiceInline,)

    list_display = (
        "username",
        "email",
        "get_full_name",
        "role",
        "designation",
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
                    "designation",
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
                    "designation",
                )
            },
        ),
    )

    @admin.display(description="Name")
    def get_full_name(self, obj):
        return obj.get_full_name() or "-"
