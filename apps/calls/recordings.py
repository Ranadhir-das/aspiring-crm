import re

from django.core.exceptions import PermissionDenied
from django.http import FileResponse, Http404, HttpResponse, StreamingHttpResponse
from django.shortcuts import get_object_or_404

from .audio_processing import infer_content_type
from .models import Call, CallRecording

_RANGE_RE = re.compile(r'^bytes=(\d*)-(\d*)$')


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


def _iter_file_slice(file_obj, start: int, length: int, chunk_size: int = 64 * 1024):
    try:
        file_obj.seek(start)
        remaining = length
        while remaining > 0:
            chunk = file_obj.read(min(chunk_size, remaining))
            if not chunk:
                break
            remaining -= len(chunk)
            yield chunk
    finally:
        file_obj.close()


def playback(call, request=None):
    recording = get_object_or_404(CallRecording, call=call)
    content_type = recording.content_type or infer_content_type(recording.file.name)
    try:
        file_obj = recording.file.open('rb')
    except FileNotFoundError:
        raise Http404('Recording file is unavailable.')

    # Determine total byte length safely across disk & InMemoryStorage
    total_size = recording.file_size
    if not total_size:
        try:
            total_size = recording.file.size
        except Exception:
            file_obj.seek(0, 2)
            total_size = file_obj.tell()
            file_obj.seek(0)

    range_header = request.META.get('HTTP_RANGE', '').strip() if request is not None else ''
    if range_header and total_size > 0:
        match = _RANGE_RE.match(range_header)
        if match:
            start_s, end_s = match.group(1), match.group(2)
            if start_s == '' and end_s == '':
                file_obj.close()
                resp = HttpResponse(status=416)
                resp['Content-Range'] = f'bytes */{total_size}'
                return resp
            if start_s == '':
                # Suffix range: bytes=-N
                suffix_len = int(end_s)
                if suffix_len <= 0:
                    file_obj.close()
                    resp = HttpResponse(status=416)
                    resp['Content-Range'] = f'bytes */{total_size}'
                    return resp
                start = max(0, total_size - suffix_len)
                end = total_size - 1
            else:
                start = int(start_s)
                end = int(end_s) if end_s else total_size - 1
            if start >= total_size or start > end:
                file_obj.close()
                resp = HttpResponse(status=416)
                resp['Content-Range'] = f'bytes */{total_size}'
                return resp
            end = min(end, total_size - 1)
            length = end - start + 1
            response = StreamingHttpResponse(
                _iter_file_slice(file_obj, start, length),
                status=206,
                content_type=content_type,
            )
            response['Content-Length'] = str(length)
            response['Content-Range'] = f'bytes {start}-{end}/{total_size}'
            response['Accept-Ranges'] = 'bytes'
            response['Cache-Control'] = 'private, no-store'
            response['X-Content-Type-Options'] = 'nosniff'
            response['Content-Disposition'] = 'inline'
            return response

    response = FileResponse(file_obj, content_type=content_type)
    if total_size:
        response['Content-Length'] = str(total_size)
    response['Accept-Ranges'] = 'bytes'
    response['Cache-Control'] = 'private, no-store'
    response['X-Content-Type-Options'] = 'nosniff'
    response['Content-Disposition'] = 'inline'
    return response
