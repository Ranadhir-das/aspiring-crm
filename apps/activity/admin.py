from django.contrib import admin

from .models import (
    ActivityCategory,
    ActivityBreak,
    ActivityLog,
    BreakReasonConfig,
    WorkActivitySegment,
    WorkSession,
)


@admin.register(ActivityLog)
class ActivityLogAdmin(admin.ModelAdmin):
    list_display = ("description", "verb", "actor", "lead", "created_at")
    list_filter = ("verb", "created_at")
    search_fields = ("description", "actor__username", "lead__name", "lead__phone")
    readonly_fields = ("actor", "lead", "verb", "description", "created_at")
    ordering = ("-created_at",)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(ActivityCategory)
class ActivityCategoryAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "slug",
        "icon_key",
        "color_hex",
        "is_active",
        "is_default_for_caller",
        "is_default_for_counselor",
        "is_default_general",
        "sort_order",
    )
    list_filter = ("is_active", "is_default_for_caller", "is_default_for_counselor", "is_default_general")
    search_fields = ("name", "slug")
    prepopulated_fields = {"slug": ("name",)}
    ordering = ("sort_order", "name")


@admin.register(BreakReasonConfig)
class BreakReasonConfigAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "code",
        "warning_threshold_minutes",
        "requires_note",
        "is_active",
        "sort_order",
    )
    list_filter = ("is_active", "requires_note")
    search_fields = ("name", "code")
    ordering = ("sort_order", "name")


class WorkActivitySegmentInline(admin.TabularInline):
    model = WorkActivitySegment
    extra = 0
    readonly_fields = ("started_at", "ended_at", "category", "note")


class ActivityBreakInline(admin.TabularInline):
    model = ActivityBreak
    extra = 0
    readonly_fields = (
        "reason_config",
        "started_at",
        "ended_at",
        "warning_triggered",
        "warning_threshold_minutes_snapshot",
        "closure_reason",
        "closed_by",
        "admin_closure_note",
    )


@admin.register(WorkSession)
class WorkSessionAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "user",
        "date",
        "status",
        "started_at",
        "ended_at",
        "closure_reason",
    )
    list_filter = ("status", "closure_reason", "date")
    search_fields = ("user__username", "user__first_name", "user__last_name")
    readonly_fields = ("created_at", "updated_at")
    inlines = [WorkActivitySegmentInline, ActivityBreakInline]
    ordering = ("-started_at",)
