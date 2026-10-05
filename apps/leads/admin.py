import logging
from django import forms
from django.contrib import admin
from django.db import transaction
from django.urls import reverse
from django.utils.html import format_html

logger = logging.getLogger(__name__)

from .models import (
    Admission,
    Counselling,
    Lead,
    LeadAssignmentHistory,
    LeadImportBatch,
    Service,
    WebsiteLeadSubmission,
    WebsiteSource,
    WhatsAppActivity,
    WhatsAppTemplate,
)


@admin.register(Service)
class ServiceAdmin(admin.ModelAdmin):
    list_display = ('name', 'code', 'is_active', 'updated_at')
    list_filter = ('is_active',)
    search_fields = ('name', 'code')
    readonly_fields = ('created_at', 'updated_at')


class WebsiteSourceForm(forms.ModelForm):
    class Meta:
        model = WebsiteSource
        fields = '__all__'

    def clean(self):
        data = super().clean()
        default = data.get('default_service')
        allowed = data.get('allowed_services')
        if default and (not default.is_active or allowed is None or default not in allowed):
            self.add_error('default_service', 'Default service must be active and included in allowed services.')
        return data


@admin.register(WebsiteSource)
class WebsiteSourceAdmin(admin.ModelAdmin):
    form = WebsiteSourceForm
    list_display = (
        'name',
        'code',
        'is_active',
        'api_key_masked',
        'allowed_services_display',
        'submission_count',
        'created_at',
    )
    list_filter = ('is_active', 'created_at')
    search_fields = ('name', 'code', 'api_key')
    filter_horizontal = ('allowed_services',)
    readonly_fields = ('created_at', 'updated_at')

    @admin.display(description='API Key')
    def api_key_masked(self, obj):
        if not obj.api_key:
            return '—'
        if len(obj.api_key) > 10:
            return f"{obj.api_key[:6]}...{obj.api_key[-4:]}"
        return obj.api_key

    @admin.display(description='Allowed Services')
    def allowed_services_display(self, obj):
        services = list(obj.allowed_services.values_list('code', flat=True))
        return ', '.join(services) if services else 'No services permitted'

    @admin.display(description='Submissions')
    def submission_count(self, obj):
        return obj.submissions.count()


class WebsiteLeadSubmissionInline(admin.TabularInline):
    model = WebsiteLeadSubmission
    extra = 0
    can_delete = False
    readonly_fields = (
        'submitted_at',
        'source',
        'campaign',
        'service_type',
        'is_duplicate',
        'name',
        'phone',
        'email',
        'location',
        'notes',
        'ip_address',
    )
    ordering = ('-submitted_at',)


@admin.register(WebsiteLeadSubmission)
class WebsiteLeadSubmissionAdmin(admin.ModelAdmin):
    list_display = (
        'phone',
        'name',
        'source_name',
        'campaign',
        'service_code',
        'is_duplicate',
        'lead_link',
        'routing_status',
        'submitted_at',
    )
    list_filter = ('is_duplicate', 'source', 'service_type', 'submitted_at')
    search_fields = ('name', 'phone', 'email', 'campaign', 'source', 'notes', 'lead__name', 'lead__phone')
    readonly_fields = (
        'lead',
        'website_source',
        'name',
        'phone',
        'email',
        'service',
        'service_type',
        'source',
        'campaign',
        'location',
        'notes',
        'is_duplicate',
        'ip_address',
        'submitted_at',
        'routing_status',
    )
    ordering = ('-submitted_at',)

    @admin.display(description='Source')
    def source_name(self, obj):
        return obj.website_source.name if obj.website_source else (obj.source or '—')

    @admin.display(description='Service')
    def service_code(self, obj):
        return obj.service_type.code if obj.service_type else (obj.service or '—')

    @admin.display(description='Lead')
    def lead_link(self, obj):
        if obj.lead:
            url = reverse('admin:leads_lead_change', args=[obj.lead.pk])
            return format_html('<a href="{}">{} (ID: {})</a>', url, obj.lead.name, obj.lead.pk)
        return '—'

    @admin.display(description='Routing Status')
    def routing_status(self, obj):
        return obj.lead.routing_status if obj.lead else '—'


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


class CounsellingInline(admin.TabularInline):
    model = Counselling
    extra = 0
    readonly_fields = ('created_at', 'updated_at')
    ordering = ('-conducted_at',)


@admin.register(Lead)
class LeadAdmin(admin.ModelAdmin):
    autocomplete_fields = ('service_type',)
    inlines = [WebsiteLeadSubmissionInline, CounsellingInline]

    list_display = (
        "name",
        "phone",
        "service_type",
        "status",
        "routing_status_display",
        "assigned_caller",
        "source",
        "campaign",
        "created_at",
    )

    list_filter = (
        "status",
        "service_type",
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
        "campaign",
    )

    readonly_fields = (
        "created_at",
        "updated_at",
        "assigned_at",
        "routing_status_display",
    )

    ordering = (
        "-created_at",
    )

    @admin.display(description="Routing Status")
    def routing_status_display(self, obj):
        return obj.routing_status

    def save_model(self, request, obj, form, change):
        previous_caller_id = None
        if change and obj.pk:
            previous_caller_id = Lead.objects.filter(pk=obj.pk).values_list('assigned_caller_id', flat=True).first()
        super().save_model(request, obj, form, change)
        if obj.assigned_caller_id and obj.assigned_caller_id != previous_caller_id:
            LeadAssignmentHistory.objects.create(
                lead=obj,
                previous_caller_id=previous_caller_id,
                new_caller=obj.assigned_caller,
                assigned_by=request.user,
                reason="Assigned via Django admin",
            )
            caller_pk = obj.assigned_caller_id
            lead_id = obj.pk
            print(f"[Push Assignment] on_commit registered (admin) for caller_id={caller_pk} lead_id={lead_id}")
            logger.info("[Push Assignment] on_commit registered (admin) for caller_id=%s lead_id=%s", caller_pk, lead_id)
            from apps.accounts.push_notifications import notify_caller_about_assigned_leads
            transaction.on_commit(lambda: notify_caller_about_assigned_leads(caller_pk, [lead_id]))


@admin.register(Admission)
class AdmissionAdmin(admin.ModelAdmin):
    list_display = (
        "student_display",
        "candidate_type",
        "country",
        "college",
        "course",
        "caller",
        "admission_date",
        "fees",
        "created_at",
    )
    list_filter = (
        "candidate_type",
        "country",
        "admission_date",
        "caller",
        "created_at",
    )
    search_fields = (
        "lead__name",
        "lead__phone",
        "walk_in_name",
        "walk_in_phone",
        "country",
        "college",
        "course",
    )
    readonly_fields = ("created_at", "updated_at")

    @admin.display(description="Student / Candidate")
    def student_display(self, obj):
        if obj.lead:
            return obj.lead.name
        return obj.walk_in_name or "Walk-in Candidate"


@admin.register(Counselling)
class CounsellingAdmin(admin.ModelAdmin):
    list_display = (
        "lead",
        "visitor_name",
        "visitor_phone",
        "counselling_type",
        "caller",
        "college",
        "course",
        "conducted_at",
        "created_at",
    )
    list_filter = (
        "counselling_type",
        "caller",
        "conducted_at",
        "created_at",
    )
    search_fields = (
        "lead__name",
        "lead__phone",
        "caller__username",
        "college",
        "course",
        "notes",
    )
    readonly_fields = ("created_at", "updated_at")

    def get_search_fields(self, request):
        return (*super().get_search_fields(request), 'visitor_name', 'visitor_phone', 'visitor_email')


@admin.register(WhatsAppTemplate)
class WhatsAppTemplateAdmin(admin.ModelAdmin):
    list_display = ("title", "is_active", "created_by", "updated_at")
    list_filter = ("is_active", "created_at")
    search_fields = ("title", "message")
    readonly_fields = ("created_at", "updated_at")

    def save_model(self, request, obj, form, change):
        if not change:
            obj.created_by = request.user
        obj.updated_by = request.user
        super().save_model(request, obj, form, change)


@admin.register(WhatsAppActivity)
class WhatsAppActivityAdmin(admin.ModelAdmin):
    list_display = ("lead", "user", "template_name", "source", "created_at")
    list_filter = ("source", "created_at")
    search_fields = ("lead__name", "lead__phone", "user__username", "template_name", "message_snippet")
    readonly_fields = ("lead", "user", "template", "template_name", "message_snippet", "source", "created_at")



