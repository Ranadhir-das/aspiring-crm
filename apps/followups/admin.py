from django.contrib import admin

from .models import FollowUp


@admin.register(FollowUp)
class FollowUpAdmin(admin.ModelAdmin):
    list_display = (
        "lead",
        "caller",
        "scheduled_at",
        "status",
        "created_at",
    )

    list_filter = (
        "status",
        "caller",
    )

    search_fields = (
        "lead__name",
        "lead__phone",
        "caller__username",
        "caller__first_name",
        "caller__last_name",
    )

    ordering = ("-scheduled_at",)