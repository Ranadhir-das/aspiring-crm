from django.contrib import admin

from .models import Call


@admin.register(Call)
class CallAdmin(admin.ModelAdmin):

    list_display = (
        "lead",
        "caller",
        "started_at",
        "duration_seconds",
        "outcome",
        "created_at",
    )

    list_filter = (
        "outcome",
        "caller",
        "started_at",
    )

    search_fields = (
        "lead__name",
        "lead__phone",
        "caller__username",
        "caller__email",
    )

    readonly_fields = (
        "created_at",
    )

    ordering = (
        "-started_at",
    )