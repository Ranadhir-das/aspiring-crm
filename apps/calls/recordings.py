from django.core.exceptions import PermissionDenied
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404

from .models import Call, CallRecording


def permitted_call(user, call_id, lock=False):
    query = Call.objects.select_for_update() if lock else Call.objects.all()
    call = get_object_or_404(query, pk=call_id)
    if user.role not in {'ADMIN', 'MANAGER', 'SUPER_ADMIN'} and not (
        user.role == 'CALLER' and call.caller_id == user.pk
    ):
        raise PermissionDenied('You cannot access this call recording.')
    return call


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
