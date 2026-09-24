from django.contrib import admin
from django.db import transaction
from .models import LeadMilestone, PointsAdjustment, PointsEntry
from .services import can_manage


class ImmutableAdmin(admin.ModelAdmin):
    def has_module_permission(self, request):
        return can_manage(request.user)

    def has_view_permission(self, request, obj=None):
        return can_manage(request.user)

    def has_add_permission(self, request):
        return can_manage(request.user)

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def get_queryset(self, request):
        query = super().get_queryset(request)
        return query if can_manage(request.user) else query.none()

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == 'caller':
            from apps.accounts.models import User
            kwargs['queryset'] = User.objects.filter(role='CALLER')
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

    @transaction.atomic
    def save_model(self, request, obj, form, change):
        obj.recorded_by = request.user
        obj.save()


@admin.register(PointsEntry)
class PointsEntryAdmin(ImmutableAdmin):
    list_display = ['caller', 'event', 'points', 'lead', 'occurred_at', 'recorded_by']
    list_filter = ['event', 'occurred_at', 'caller']
    search_fields = ['caller__username', 'lead__name', 'reason', 'event_key']
    readonly_fields = [field.name for field in PointsEntry._meta.fields]

    def has_add_permission(self, request):
        return False


@admin.register(PointsAdjustment)
class PointsAdjustmentAdmin(ImmutableAdmin):
    list_display = ['caller', 'points', 'reason', 'recorded_by', 'created_at']
    readonly_fields = ['recorded_by', 'created_at']


@admin.register(LeadMilestone)
class LeadMilestoneAdmin(ImmutableAdmin):
    list_display = ['caller', 'lead', 'event', 'recorded_by', 'occurred_at']
    list_filter = ['event', 'occurred_at']
    readonly_fields = ['recorded_by', 'occurred_at']
