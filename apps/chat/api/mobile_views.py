from django.shortcuts import get_object_or_404
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.api.authentication import VerifiedSessionAuthentication
from apps.chat.models import ChatChannel, ChatMessage
from apps.chat.services import broadcast_message


class MobileChatView(APIView):
    authentication_classes = [VerifiedSessionAuthentication]
    permission_classes = [IsAuthenticated]


class MobileChatChannelListView(MobileChatView):
    def get(self, request):
        channels = ChatChannel.visible_to(request.user).order_by('name')
        return Response([
            {'id': c.pk, 'name': c.name, 'kind': c.kind, 'description': c.description}
            for c in channels
        ])


class MobileChatMessageView(MobileChatView):
    def _channel(self, request, channel_id):
        channel = get_object_or_404(ChatChannel, pk=channel_id)
        if not channel.is_member(request.user):
            raise PermissionDenied('You are not a member of this channel.')
        return channel

    def get(self, request, channel_id):
        channel = self._channel(request, channel_id)
        before = request.GET.get('before')
        query = channel.messages.select_related('sender').order_by('-created_at')
        if before and before.isdigit():
            query = query.filter(pk__lt=before)
        page = list(query[:50])
        page.reverse()
        return Response([m.as_payload() for m in page])

    def post(self, request, channel_id):
        channel = self._channel(request, channel_id)
        text = (request.data.get('text') or '').strip()
        if not text:
            return Response({'detail': 'Message cannot be empty.'}, status=400)
        if len(text) > 4000:
            return Response({'detail': 'Message is too long.'}, status=400)
        message = ChatMessage.objects.create(channel=channel, sender=request.user, text=text)
        broadcast_message(message)
        return Response(message.as_payload(), status=201)
