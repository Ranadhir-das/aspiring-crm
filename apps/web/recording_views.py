from datetime import timedelta
from django.db.models import Avg, Count, Q, Sum
from django.utils import timezone
from django.views.decorators.http import require_GET

from apps.accounts.models import User
from apps.calls.models import Call, CallRecording
from apps.calls.recordings import permitted_call, playback
from .views import MANAGEMENT, page, paginate, workspace


@require_GET
@workspace()
def recording_playback(request, call_id):
    return playback(permitted_call(request.user, call_id))


@require_GET
@workspace()
def call_recordings_list(request):
    is_management = request.user.role in MANAGEMENT

    # Base queryset: only recordings belonging to visible calls
    queryset = CallRecording.objects.select_related(
        'call',
        'call__lead',
        'call__lead__import_batch',
        'call__caller',
    ).order_by('-created_at')

    # Security & Role Isolation: callers only see their own recordings
    if not is_management:
        queryset = queryset.filter(call__caller=request.user)

    # Search filter: lead name, lead phone, or call phone_number
    search_query = request.GET.get('q', '').strip()
    if search_query:
        queryset = queryset.filter(
            Q(call__lead__name__icontains=search_query) |
            Q(call__lead__phone__icontains=search_query) |
            Q(call__phone_number__icontains=search_query)
        )

    # Caller filter (for management)
    caller_id = request.GET.get('caller', '').strip()
    if is_management and caller_id:
        if caller_id.isdigit():
            queryset = queryset.filter(call__caller_id=int(caller_id))

    # Outcome filter
    outcome = request.GET.get('outcome', '').strip()
    if outcome:
        queryset = queryset.filter(call__outcome=outcome)

    # Date filters (call started_at)
    date_from = request.GET.get('date_from', '').strip()
    if date_from:
        try:
            queryset = queryset.filter(call__started_at__date__gte=date_from)
        except (ValueError, TypeError):
            pass

    date_to = request.GET.get('date_to', '').strip()
    if date_to:
        try:
            queryset = queryset.filter(call__started_at__date__lte=date_to)
        except (ValueError, TypeError):
            pass

    # Aggregations & Metrics for dashboard header
    today = timezone.localtime().date()
    stats = queryset.aggregate(
        total_count=Count('id'),
        total_seconds=Sum('duration_seconds'),
        avg_seconds=Avg('duration_seconds'),
        total_bytes=Sum('file_size'),
    )

    total_count = stats['total_count'] or 0
    total_seconds = stats['total_seconds'] or 0
    avg_seconds = round(stats['avg_seconds'] or 0)
    total_bytes = stats['total_bytes'] or 0

    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    duration_formatted = f"{hours}h {minutes}m" if hours > 0 else f"{minutes}m {total_seconds % 60}s"

    mb_size = round(total_bytes / (1024 * 1024), 1)
    size_formatted = f"{mb_size} MB" if mb_size < 1024 else f"{round(mb_size / 1024, 2)} GB"

    today_count = queryset.filter(call__started_at__date=today).count()

    metrics = [
        {'label': 'Total Recordings', 'value': f"{total_count:,}", 'pill': f"{size_formatted} stored"},
        {'label': 'Total Audio Time', 'value': duration_formatted, 'pill': 'Accumulated call duration'},
        {'label': 'Average Duration', 'value': f"{avg_seconds // 60:02d}:{avg_seconds % 60:02d}", 'pill': 'Per recorded call'},
        {'label': "Today's Recordings", 'value': f"{today_count:,}", 'pill': 'Recorded today'},
    ]

    callers = User.objects.filter(role=User.Role.CALLER).order_by('first_name', 'username') if is_management else []

    page_obj = paginate(request, queryset)

    return page(
        request,
        'recordings',
        'recordings',
        records=page_obj,
        metrics=metrics,
        total_count=total_count,
        search=search_query,
        selected_caller=caller_id,
        selected_outcome=outcome,
        selected_date_from=date_from,
        selected_date_to=date_to,
        callers=callers,
        outcomes=Call.Outcome.choices,
        is_management=is_management,
    )
