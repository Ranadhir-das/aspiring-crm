from datetime import date
from django.conf import settings
from django.contrib import messages
from django.db.models import Avg, Count, Q, Sum
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from apps.calls.api.recordings import transcript_payload
from apps.calls.models import Call, CallRecording
from apps.calls.recordings import permitted_call, playback
from .views import page, paginate, workspace


@require_GET
@workspace()
def recording_playback(request, call_id):
    return playback(permitted_call(request.user, call_id), request=request)


@require_POST
@workspace()
def recording_ai_analyze(request, call_id):
    call = permitted_call(request.user, call_id)
    recording = get_object_or_404(CallRecording, call=call)
    force = str(request.POST.get('force') or request.GET.get('force') or '').lower() in {'1', 'true', 'yes'}
    if recording.transcript_status != CallRecording.TranscriptStatus.PROCESSING:
        if recording.transcript_status != CallRecording.TranscriptStatus.COMPLETED or force:
            recording.transcript_status = CallRecording.TranscriptStatus.PENDING
            recording.transcript_attempts = 0
            recording.transcript_priority = 2
            recording.transcript_error = ''
            recording.save(update_fields=[
                'transcript_status',
                'transcript_attempts',
                'transcript_priority',
                'transcript_error',
            ])

    wants_json = (
        request.headers.get('X-Requested-With') == 'XMLHttpRequest'
        or 'application/json' in (request.headers.get('Accept') or '')
    )
    if wants_json:
        return JsonResponse(transcript_payload(recording), status=202)
    messages.success(request, f'AI transcription & summary prioritized for Call #{call.pk}.')
    return redirect(request.META.get('HTTP_REFERER') or 'web:call-recordings')


@require_GET
@workspace()
def recording_ai_status(request, call_id):
    call = permitted_call(request.user, call_id)
    recording = get_object_or_404(CallRecording.objects.select_related('call', 'call__lead', 'call__caller'), call=call)
    return JsonResponse(transcript_payload(recording), status=200)


@require_POST
@workspace(management=True)
def recordings_analyze_pending(request):
    min_dur = int(getattr(settings, 'AI_TRANSCRIBE_MIN_DURATION_SECONDS', 5))
    queued = CallRecording.objects.filter(
        transcript_status__in=[
            CallRecording.TranscriptStatus.NONE,
            CallRecording.TranscriptStatus.FAILED,
        ]
    ).filter(
        Q(duration_seconds__gte=min_dur) | Q(duration_seconds=0, file_size__gte=16_000)
    ).update(
        transcript_status=CallRecording.TranscriptStatus.PENDING,
        transcript_attempts=0,
        transcript_priority=1,
        transcript_error='',
    )
    wants_json = (
        request.headers.get('X-Requested-With') == 'XMLHttpRequest'
        or 'application/json' in (request.headers.get('Accept') or '')
    )
    if wants_json:
        return JsonResponse({'queued': queued, 'message': f'Queued {queued} recording(s) for fast AI analysis.'})
    messages.success(request, f'Queued {queued} recording(s) for background AI transcription & summarization.')
    return redirect('web:call-recordings')


@require_GET
@workspace()
def call_recordings_list(request):
    from apps.accounts.capabilities import eligible_callers_qs, is_management_user
    is_management = is_management_user(request.user)

    # Base queryset: only recordings belonging to visible calls
    queryset = CallRecording.objects.select_related(
        'call',
        'call__lead',
        'call__lead__import_batch',
        'call__caller',
    ).order_by('-call__started_at', '-created_at')

    # Security & Role Isolation: callers and dual-role callers only see their own recordings
    # on currently assigned leads or direct external calls.
    if not is_management:
        queryset = queryset.filter(call__caller=request.user).filter(
            Q(call__lead__isnull=True) | Q(call__lead__assigned_caller=request.user)
        )

    # Search filter: lead name, phone, call notes, AI summary, next step, or full transcript
    search_query = request.GET.get('q', '').strip()
    if search_query:
        queryset = queryset.filter(
            Q(call__lead__name__icontains=search_query) |
            Q(call__lead__phone__icontains=search_query) |
            Q(call__phone_number__icontains=search_query) |
            Q(call__notes__icontains=search_query) |
            Q(ai_summary__icontains=search_query) |
            Q(ai_next_action__icontains=search_query) |
            Q(transcript_text__icontains=search_query)
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
            queryset = queryset.filter(call__started_at__date__gte=date.fromisoformat(date_from))
        except (ValueError, TypeError):
            pass

    date_to = request.GET.get('date_to', '').strip()
    if date_to:
        try:
            queryset = queryset.filter(call__started_at__date__lte=date.fromisoformat(date_to))
        except (ValueError, TypeError):
            pass

    # Compute smart-filter pill counts BEFORE applying direction / ai_status pills
    pill_base_qs = queryset
    direction_counts = dict(
        pill_base_qs.order_by().values('call__direction').annotate(n=Count('id')).values_list('call__direction', 'n')
    )
    outbound_count = direction_counts.get(Call.Direction.OUTBOUND, 0)
    inbound_count = direction_counts.get(Call.Direction.INBOUND, 0)
    all_directions_count = outbound_count + inbound_count

    pill_analyzed_count = pill_base_qs.filter(transcript_status=CallRecording.TranscriptStatus.COMPLETED).count()
    pill_objections_count = (
        pill_base_qs.filter(transcript_status=CallRecording.TranscriptStatus.COMPLETED)
        .exclude(ai_objections=[])
        .exclude(ai_objections__isnull=True)
        .count()
    )
    pill_positive_count = pill_base_qs.filter(
        transcript_status=CallRecording.TranscriptStatus.COMPLETED,
        ai_sentiment=CallRecording.Sentiment.POSITIVE,
    ).count()
    pill_pending_count = pill_base_qs.filter(
        transcript_status__in=[
            CallRecording.TranscriptStatus.PENDING,
            CallRecording.TranscriptStatus.PROCESSING,
        ]
    ).count()

    # AI analysis / objection / sentiment filter
    ai_status = request.GET.get('ai_status', '').strip().lower()
    if ai_status == 'analyzed':
        queryset = queryset.filter(transcript_status=CallRecording.TranscriptStatus.COMPLETED)
    elif ai_status == 'objections':
        queryset = queryset.filter(
            transcript_status=CallRecording.TranscriptStatus.COMPLETED,
        ).exclude(ai_objections=[]).exclude(ai_objections__isnull=True)
    elif ai_status == 'positive':
        queryset = queryset.filter(
            transcript_status=CallRecording.TranscriptStatus.COMPLETED,
            ai_sentiment=CallRecording.Sentiment.POSITIVE,
        )
    elif ai_status == 'pending':
        queryset = queryset.filter(
            transcript_status__in=[
                CallRecording.TranscriptStatus.PENDING,
                CallRecording.TranscriptStatus.PROCESSING,
            ]
        )
    elif ai_status == 'failed':
        queryset = queryset.filter(transcript_status=CallRecording.TranscriptStatus.FAILED)
    elif ai_status == 'none':
        queryset = queryset.filter(transcript_status=CallRecording.TranscriptStatus.NONE)
    else:
        ai_status = ''

    # Direction filter (OUTBOUND / INBOUND)
    direction = request.GET.get('direction', '').strip().upper()
    if direction in Call.Direction.values:
        queryset = queryset.filter(call__direction=direction)
    else:
        direction = ''

    # Aggregations & Metrics for dashboard header
    today = timezone.localtime().date()
    stats = queryset.aggregate(
        total_count=Count('id'),
        total_seconds=Sum('duration_seconds'),
        avg_seconds=Avg('duration_seconds'),
        total_bytes=Sum('file_size'),
        analyzed_count=Count('id', filter=Q(transcript_status=CallRecording.TranscriptStatus.COMPLETED)),
        pending_ai_count=Count(
            'id',
            filter=Q(
                transcript_status__in=[
                    CallRecording.TranscriptStatus.PENDING,
                    CallRecording.TranscriptStatus.PROCESSING,
                ]
            ),
        ),
        unanalyzed_count=Count(
            'id',
            filter=Q(
                transcript_status__in=[
                    CallRecording.TranscriptStatus.NONE,
                    CallRecording.TranscriptStatus.FAILED,
                ]
            ),
        ),
    )

    total_count = stats['total_count'] or 0
    total_seconds = stats['total_seconds'] or 0
    avg_seconds = round(stats['avg_seconds'] or 0)
    total_bytes = stats['total_bytes'] or 0
    analyzed_count = stats['analyzed_count'] or 0
    pending_ai_count = stats['pending_ai_count'] or 0
    unanalyzed_count = stats['unanalyzed_count'] or 0
    objections_flagged_count = (
        queryset.filter(transcript_status=CallRecording.TranscriptStatus.COMPLETED)
        .exclude(ai_objections=[])
        .exclude(ai_objections__isnull=True)
        .count()
    )

    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    duration_formatted = f"{hours}h {minutes}m" if hours > 0 else f"{minutes}m {total_seconds % 60}s"

    mb_size = round(total_bytes / (1024 * 1024), 1)
    size_formatted = f"{mb_size} MB" if mb_size < 1024 else f"{round(mb_size / 1024, 2)} GB"
    today_count = queryset.filter(call__started_at__date=today).count()

    metrics = [
        {
            'label': 'Total Recordings',
            'value': f"{total_count:,}",
            'pill': f"{outbound_count} out · {inbound_count} in · {size_formatted}",
            'theme': 'indigo',
            'filter_query': '',
        },
        {
            'label': 'AI Analyzed',
            'value': f"{analyzed_count:,}",
            'pill': (
                f"{pending_ai_count} processing"
                if pending_ai_count
                else f"{round((analyzed_count / total_count) * 100) if total_count else 0}% coverage"
            ),
            'theme': 'emerald',
            'filter_query': '?ai_status=analyzed',
        },
        {
            'label': 'Objections Flagged',
            'value': f"{objections_flagged_count:,}",
            'pill': 'Click to inspect objections',
            'theme': 'rose',
            'filter_query': '?ai_status=objections',
        },
        {
            'label': 'Total Talk Time',
            'value': duration_formatted,
            'pill': f"Avg {avg_seconds // 60:02d}:{avg_seconds % 60:02d} · {today_count} today",
            'theme': 'violet',
            'filter_query': '',
        },
    ]

    callers = eligible_callers_qs().order_by('first_name', 'username') if is_management else []
    advanced_filters_count = sum(1 for v in [caller_id, outcome, date_from, date_to] if v)
    active_filters_count = sum(1 for v in [search_query, caller_id, outcome, direction, ai_status, date_from, date_to] if v)

    page_obj = paginate(request, queryset)

    return page(
        request,
        'recordings',
        'recordings',
        records=page_obj,
        metrics=metrics,
        total_count=total_count,
        all_directions_count=all_directions_count,
        outbound_count=outbound_count,
        inbound_count=inbound_count,
        analyzed_count=analyzed_count,
        objections_flagged_count=objections_flagged_count,
        pending_ai_count=pending_ai_count,
        unanalyzed_count=unanalyzed_count,
        pill_analyzed_count=pill_analyzed_count,
        pill_objections_count=pill_objections_count,
        pill_positive_count=pill_positive_count,
        pill_pending_count=pill_pending_count,
        search=search_query,
        selected_caller=caller_id,
        selected_outcome=outcome,
        selected_direction=direction,
        selected_ai_status=ai_status,
        selected_date_from=date_from,
        selected_date_to=date_to,
        callers=callers,
        outcomes=Call.Outcome.choices,
        directions=Call.Direction.choices,
        is_management=is_management,
        advanced_filters_count=advanced_filters_count,
        active_filters_count=active_filters_count,
    )
