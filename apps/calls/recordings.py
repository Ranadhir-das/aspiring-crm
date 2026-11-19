from django.core.exceptions import PermissionDenied
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404

from .models import Call, CallRecording


def permitted_call(user, call_id, lock=False):
    from apps.accounts.capabilities import is_caller_capable, is_management_user
    query = (
        Call.objects.select_related('lead').select_for_update(of=('self',))
        if lock
        else Call.objects.select_related('lead')
    )
    call = get_object_or_404(query, pk=call_id)
    if is_management_user(user):
        return call
    if is_caller_capable(user) and call.caller_id == user.pk and (
        call.lead_id is None or call.lead.assigned_caller_id == user.pk
    ):
        return call
    raise PermissionDenied('You cannot access this call recording.')


def playback(call):
    recording = get_object_or_404(CallRecording, call=call)
    try:
        response = FileResponse(recording.file.open('rb'), content_type='audio/mp4')
    except FileNotFoundError:
        raise Http404('Recording file is unavailable.')
    response['Cache-Control'] = 'private, no-store'
    response['X-Content-Type-Options'] = 'nosniff'
    response['Content-Disposition'] = 'inline'
    return response
