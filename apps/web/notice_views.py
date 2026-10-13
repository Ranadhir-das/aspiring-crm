import logging
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

logger = logging.getLogger(__name__)

ADMIN_ROLES = {'ADMIN', 'SUPER_ADMIN'}


@workspace(employee=True)
def notices(request):
    can_create = request.user.role in ADMIN_ROLES
    query = Notice.objects.select_related('created_by').prefetch_related('attachments', 'recipients')
    visible = list(query) if can_create else [n for n in query if n.is_visible_to(request.user)]
    eligible_employees = []
    if can_create:
        eligible_employees = list(
            User.objects.filter(is_active=True, registration_pending=False)
            .order_by('first_name', 'last_name', 'username')
        )
    return page(request, 'notices', 'notices', notices=visible, can_create=can_create,
                role_choices=User.Role.choices, eligible_employees=eligible_employees)


@require_POST
@workspace(management=True)
@transaction.atomic
def notice_new(request):
    if request.user.role not in ADMIN_ROLES:
        raise PermissionDenied
    title = (request.POST.get('title') or '').strip()[:200]
    body = (request.POST.get('body') or '').strip()[:4000]
    recipient_type = (request.POST.get('recipient_type') or 'role').strip().lower()

    if not title or not body:
        messages.error(request, 'Notice title and message are required.')
        return redirect('web:notices')

    target_roles = []
    target_employees = []

    if recipient_type == 'employees':
        raw_ids = request.POST.getlist('recipients')
        valid_ids = [int(raw_id) for raw_id in raw_ids if str(raw_id).isdigit()]
        if valid_ids:
            target_employees = list(
                User.objects.filter(
                    pk__in=valid_ids,
                    is_active=True,
                    registration_pending=False,
                ).distinct()
            )

        if not target_employees:
            messages.error(request, 'Please select at least one active, valid employee recipient.')
            return redirect('web:notices')
    else:
        target_roles = [r for r in request.POST.getlist('roles') if r in dict(User.Role.choices)]

    notice = Notice.objects.create(title=title, body=body, roles=target_roles, created_by=request.user)
    if target_employees:
        notice.recipients.set(target_employees)

    uploaded_files = request.FILES.getlist('attachments')
    for f in uploaded_files:
        clean_name = getattr(f, 'name', 'attachment')
        try:
            clean_name, mime_type, file_size = validate_attachment(f)
            # Bound filename stem so storage path never exceeds FileField's 100-char limit
            stem, ext = os.path.splitext(clean_name)
            safe_name = f"{stem[:50]}{ext}"
            f.name = safe_name

            with transaction.atomic():
                NoticeAttachment.objects.create(
                    notice=notice,
                    uploaded_by=request.user,
                    file=f,
                    original_filename=clean_name,
                    mime_type=mime_type,
                    file_size=file_size,
                )
        except ValidationError as e:
            msg = e.message if hasattr(e, 'message') else (', '.join(e.messages) if hasattr(e, 'messages') else str(e))
            messages.warning(request, f'Attachment "{clean_name}" could not be saved: {msg}')
        except (OSError, PermissionError) as e:
            logger.exception('Storage error saving attachment "%s" for notice #%s: %s', clean_name, notice.pk, e)
            messages.warning(request, f'Attachment "{clean_name}" could not be saved due to a server storage permission issue.')
        except Exception as e:
            logger.exception('Unexpected error saving attachment "%s" for notice #%s: %s', clean_name, notice.pk, e)
            messages.warning(request, f'Attachment "{clean_name}" could not be saved due to an internal server error.')

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
            try:
                att.file.delete(save=False)
            except OSError:
                pass
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
        try:
            attachment.file.delete(save=False)
        except OSError:
            pass
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

    try:
        f = open(attachment.file.path, 'rb')
    except OSError:
        raise Http404('File not accessible.')

    response = FileResponse(f, content_type=attachment.mime_type)
    disposition = 'attachment' if request.GET.get('download') == '1' else 'inline'
    response['Content-Disposition'] = f'{disposition}; filename="{attachment.original_filename}"'
    response['X-Content-Type-Options'] = 'nosniff'
    return response
