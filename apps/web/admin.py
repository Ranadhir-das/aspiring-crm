from django.contrib import admin
from .models import Notice, NoticeAttachment


class NoticeAttachmentInline(admin.TabularInline):
    model = NoticeAttachment
    extra = 0
    readonly_fields = ('file', 'original_filename', 'mime_type', 'file_size', 'created_at')


@admin.register(Notice)
class NoticeAdmin(admin.ModelAdmin):
    list_display = ('title', 'audience_label', 'created_by', 'created_at')
    list_filter = ('created_at',)
    search_fields = ('title', 'body')
    inlines = [NoticeAttachmentInline]


@admin.register(NoticeAttachment)
class NoticeAttachmentAdmin(admin.ModelAdmin):
    list_display = ('original_filename', 'notice', 'mime_type', 'file_size', 'created_at')
    list_filter = ('mime_type', 'created_at')
    search_fields = ('original_filename', 'notice__title')
