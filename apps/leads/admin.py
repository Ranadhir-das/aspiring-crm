from django.contrib import admin

from .models import Lead, LeadAssignmentHistory, LeadImportBatch


@admin.register(LeadAssignmentHistory)
class LeadAssignmentHistoryAdmin(admin.ModelAdmin):
    list_display = (
        "lead",
        "previous_caller",
        "new_caller",
        "assigned_by",
        "assigned_at",
        "reason",
    )

    list_filter = (
        "assigned_at",
        "new_caller",
    )

    search_fields = (
        "lead__name",
        "lead__phone",
        "previous_caller__username",
        "new_caller__username",
        "assigned_by__username",
    )

    readonly_fields = (
        "assigned_at",
    )

    ordering = ("-assigned_at",)


@admin.register(LeadImportBatch)
class LeadImportBatchAdmin(admin.ModelAdmin):
    list_display = (
        "filename",
        "imported_by",
        "total_rows",
        "created_count",
        "duplicate_count",
        "warning_count",
        "created_at",
    )

    list_filter = (
        "created_at",
    )

    search_fields = (
        "filename",
        "imported_by__username",
    )

    readonly_fields = (
        "created_at",
    )

    ordering = ("-created_at",)


@admin.register(Lead)
class LeadAdmin(admin.ModelAdmin):

    list_display = (
        "name",
        "phone",
        "status",
        "assigned_caller",
        "source",
        "created_at",
    )

    list_filter = (
        "status",
        "source",
        "assigned_caller",
        "created_at",
    )

    search_fields = (
        "name",
        "phone",
        "email",
        "location",
        "college",
    )

    readonly_fields = (
        "created_at",
        "updated_at",
        "assigned_at",
    )

    ordering = (
        "-created_at",
    )