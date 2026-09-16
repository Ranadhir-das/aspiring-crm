from django.contrib import admin

from .models import ActivityLog


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
