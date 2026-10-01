import os
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import FileResponse, Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.views.decorators.http import require_POST

from apps.accounts.models import User
from apps.chat.models import ChatAttachment, ChatChannel, ChatMessage
from apps.chat.services import broadcast_message
from .attachment_utils import validate_attachment
from .forms import MANAGEMENT
from .views import page, workspace


def visible_channels(user):
    return ChatChannel.visible_to(user).order_by('name')


def chat_context(request, channel):
    return {
        'channels': list(visible_channels(request.user)),
        'can_create_channel': request.user.role in MANAGEMENT,
        'members': User.objects.filter(is_active=True).exclude(pk=request.user.pk).order_by('first_name', 'username'),
        'channel': channel,
    }


@workspace(employee=True)
def chat_home(request):
    channels = list(visible_channels(request.user))
    if channels:
        return redirect('web:chat-channel', channel_id=channels[0].pk)
    return page(request, 'chat', 'chat', **chat_context(request, None), thread=[])


@workspace(employee=True)
def chat_channel(request, channel_id):
    channel = get_object_or_404(ChatChannel, pk=channel_id)
    if not channel.is_member(request.user):
        raise PermissionDenied
    thread = list(channel.messages.select_related('sender').prefetch_related('attachments').order_by('-created_at')[:50])
    thread.reverse()
    return page(request, 'chat', 'chat', **chat_context(request, channel), thread=thread)


@require_POST
@workspace(employee=True)
def chat_send(request, channel_id):
    channel = get_object_or_404(ChatChannel, pk=channel_id)
    if not channel.is_member(request.user):
        raise PermissionDenied
    text = (request.POST.get('text') or '').strip()

    files = request.FILES.getlist('attachments') or request.FILES.getlist('file')
    if not files:
        single = request.FILES.get('attachment') or request.FILES.get('file')
        if single:
            files = [single]

    if not text and not files:
        return JsonResponse({'detail': 'Message cannot be empty.'}, status=400)
    if len(text) > 4000:
        return JsonResponse({'detail': 'Message is too long.'}, status=400)

    validated_files = []
    for f in files:
        try:
            clean_name, mime_type, file_size = validate_attachment(f)
            validated_files.append((f, clean_name, mime_type, file_size))
        except ValidationError as e:
            return JsonResponse({'detail': str(e.message if hasattr(e, 'message') else e)}, status=400)

    message = ChatMessage.objects.create(channel=channel, sender=request.user, text=text)
    for f, clean_name, mime_type, file_size in validated_files:
        ChatAttachment.objects.create(
            message=message,
            file=f,
            original_name=clean_name,
            mime_type=mime_type,
            file_size=file_size,
        )

    broadcast_message(message)
    return JsonResponse(message.as_payload(), status=201)


@workspace(employee=True)
def chat_attachment_download(request, pk):
    attachment = get_object_or_404(ChatAttachment.objects.select_related('message__channel'), pk=pk)
    if not attachment.message.channel.is_member(request.user) and request.user.role not in {'SUPER_ADMIN', 'ADMIN'}:
        raise PermissionDenied('You do not have access to this attachment.')
    if not attachment.file or not os.path.exists(attachment.file.path):
        raise Http404('File not found.')

    response = FileResponse(open(attachment.file.path, 'rb'), content_type=attachment.mime_type)
    disposition = 'attachment' if request.GET.get('download') == '1' else 'inline'
    response['Content-Disposition'] = f'{disposition}; filename="{attachment.original_name}"'
    response['X-Content-Type-Options'] = 'nosniff'
    return response


@require_POST
@workspace(management=True)
def chat_channel_new(request):
    name = (request.POST.get('name') or '').strip()
    description = (request.POST.get('description') or '').strip()[:255]
    member_ids = request.POST.getlist('members')
    if name:
        channel, created = ChatChannel.objects.get_or_create(name=name, defaults={
            'kind': ChatChannel.Kind.GROUP,
            'description': description,
            'created_by': request.user,
        })
        if created:
            if member_ids:
                channel.members.set(member_ids)
            channel.members.add(request.user)
        return redirect('web:chat-channel', channel_id=channel.pk)
    return redirect('web:chat-home')
