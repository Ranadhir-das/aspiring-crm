import os
from django.db import transaction
from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect
from django.views.decorators.http import require_POST

from apps.accounts.models import User
from .attachment_utils import validate_attachment
from .models import Notice, NoticeAttachment
from .views import page, workspace

ADMIN_ROLES = {'ADMIN', 'SUPER_ADMIN'}


@workspace(employee=True)
def notices(request):
    can_create = request.user.role in ADMIN_ROLES
    query = Notice.objects.select_related('created_by').prefetch_related('attachments')
    visible = list(query) if can_create else [n for n in query if n.is_visible_to(request.user)]
    return page(request, 'notices', 'notices', notices=visible, can_create=can_create,
                role_choices=User.Role.choices)


@require_POST
@workspace(management=True)
@transaction.atomic
def notice_new(request):
    if request.user.role not in ADMIN_ROLES:
        raise PermissionDenied
    title = (request.POST.get('title') or '').strip()[:200]
    body = (request.POST.get('body') or '').strip()[:4000]
    roles = [r for r in request.POST.getlist('roles') if r in dict(User.Role.choices)]

    if not title or not body:
        messages.error(request, 'Notice title and message are required.')
        return redirect('web:notices')

    notice = Notice.objects.create(title=title, body=body, roles=roles, created_by=request.user)

    uploaded_files = request.FILES.getlist('attachments')
    for f in uploaded_files:
        try:
            clean_name, mime_type, file_size = validate_attachment(f)
            NoticeAttachment.objects.create(
                notice=notice,
                uploaded_by=request.user,
                file=f,
                original_filename=clean_name,
                mime_type=mime_type,
                file_size=file_size,
            )
        except ValidationError as e:
            messages.warning(request, f'Attachment "{f.name}" could not be saved: {e.message}')

    messages.success(request, 'Notice posted successfully.')
    return redirect('web:notices')


@require_POST
@workspace(management=True)
def notice_delete(request, pk):
    if request.user.role not in ADMIN_ROLES:
        raise PermissionDenied
    notice = get_object_or_404(Notice, pk=pk)
    # Delete associated files from storage
    for att in notice.attachments.all():
        if att.file:
            att.file.delete(save=False)
    notice.delete()
    messages.success(request, 'Notice deleted.')
    return redirect('web:notices')


@require_POST
@workspace(management=True)
def notice_attachment_delete(request, notice_pk, pk):
    if request.user.role not in ADMIN_ROLES:
        raise PermissionDenied
    notice = get_object_or_404(Notice, pk=notice_pk)
    attachment = get_object_or_404(NoticeAttachment, pk=pk, notice=notice)
    if attachment.file:
        attachment.file.delete(save=False)
    attachment.delete()
    messages.success(request, f'Attachment {attachment.original_filename} removed.')
    return redirect('web:notices')


@workspace(employee=True)
def notice_attachment_download(request, notice_pk, pk):
    notice = get_object_or_404(Notice, pk=notice_pk)
    if not notice.is_visible_to(request.user) and request.user.role not in ADMIN_ROLES:
        raise PermissionDenied('You do not have permission to view this notice.')
    attachment = get_object_or_404(NoticeAttachment, pk=pk, notice=notice)
    if not attachment.file or not os.path.exists(attachment.file.path):
        raise Http404('File not found.')

    response = FileResponse(open(attachment.file.path, 'rb'), content_type=attachment.mime_type)
    disposition = 'attachment' if request.GET.get('download') == '1' else 'inline'
    response['Content-Disposition'] = f'{disposition}; filename="{attachment.original_filename}"'
    response['X-Content-Type-Options'] = 'nosniff'
    return response
