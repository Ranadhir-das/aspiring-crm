import json

from django.contrib import admin
from django.utils.html import format_html

from .models import Notification


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ('recipient', 'type', 'title', 'is_read', 'created_at', 'read_at')
    list_filter = ('type', 'is_read', 'created_at')
    list_select_related = ('recipient',)
    search_fields = ('recipient__username', 'title')
    readonly_fields = ('recipient', 'type', 'title', 'body', 'formatted_data',
                       'is_read', 'read_at', 'created_at')
    exclude = ('data',)

    @admin.display(description='Data')
    def formatted_data(self, obj):
        return format_html('<pre>{}</pre>', json.dumps(obj.data, indent=2, ensure_ascii=False))

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
