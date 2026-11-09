from django.contrib import admin
from .models import Notice, NoticeAttachment, Project, ProjectWorkReport, WorkReport


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


@admin.register(Project)
class ProjectAdmin(admin.ModelAdmin):
    list_display = ('title', 'employee', 'created_by', 'status', 'priority', 'due_date', 'self_created', 'created_at')
    list_filter = ('status', 'priority', 'self_created', 'due_date', 'created_at')
    search_fields = ('title', 'description', 'employee__username', 'created_by__username')
    readonly_fields = (
        'status',
        'reviewed_by',
        'review_comments',
        'completion_requested_at',
        'completion_reviewed_at',
        'created_at',
        'updated_at',
    )



@admin.register(ProjectWorkReport)
class ProjectWorkReportAdmin(admin.ModelAdmin):
    list_display = ('project', 'employee', 'work_date', 'duration_minutes', 'status', 'created_at')
    list_filter = ('status', 'work_date', 'created_at')
    search_fields = ('project__title', 'employee__username', 'description')
    readonly_fields = ('created_at', 'updated_at', 'submitted_at')


@admin.register(WorkReport)
class WorkReportAdmin(admin.ModelAdmin):
    list_display = ('employee', 'date', 'updated_at')
    list_filter = ('date',)
    search_fields = ('employee__username', 'notes')
    readonly_fields = ('updated_at',)
