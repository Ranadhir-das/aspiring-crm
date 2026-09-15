from django.core.exceptions import PermissionDenied
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.views.decorators.http import require_POST

from apps.accounts.models import User
from apps.chat.models import ChatChannel, ChatMessage
from apps.chat.services import broadcast_message
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
    thread = list(channel.messages.select_related('sender').order_by('-created_at')[:50])
    thread.reverse()
    return page(request, 'chat', 'chat', **chat_context(request, channel), thread=thread)


@require_POST
@workspace(employee=True)
def chat_send(request, channel_id):
    channel = get_object_or_404(ChatChannel, pk=channel_id)
    if not channel.is_member(request.user):
        raise PermissionDenied
    text = (request.POST.get('text') or '').strip()
    if not text:
        return JsonResponse({'detail': 'Message cannot be empty.'}, status=400)
    if len(text) > 4000:
        return JsonResponse({'detail': 'Message is too long.'}, status=400)
    message = ChatMessage.objects.create(channel=channel, sender=request.user, text=text)
    broadcast_message(message)
    return JsonResponse(message.as_payload(), status=201)


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
