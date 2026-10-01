import os
from django.core.exceptions import ValidationError
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.api.authentication import VerifiedSessionAuthentication
from apps.chat.models import ChatAttachment, ChatChannel, ChatMessage
from apps.chat.services import broadcast_message
from apps.web.attachment_utils import validate_attachment


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
        query = channel.messages.select_related('sender').prefetch_related('attachments').order_by('-created_at')
        if before and before.isdigit():
            query = query.filter(pk__lt=before)
        page = list(query[:50])
        page.reverse()
        return Response([m.as_payload() for m in page])

    def post(self, request, channel_id):
        channel = self._channel(request, channel_id)
        text = (request.data.get('text') or '').strip()

        # Handle file attachments from multipart request
        files = request.FILES.getlist('file') or request.FILES.getlist('attachment')
        if not files:
            single = request.FILES.get('file') or request.FILES.get('attachment')
            if single:
                files = [single]

        if not text and not files:
            return Response({'detail': 'Message cannot be empty.'}, status=status.HTTP_400_BAD_REQUEST)
        if len(text) > 4000:
            return Response({'detail': 'Message is too long.'}, status=status.HTTP_400_BAD_REQUEST)

        validated_files = []
        for f in files:
            try:
                clean_name, mime_type, file_size = validate_attachment(f)
                validated_files.append((f, clean_name, mime_type, file_size))
            except ValidationError as e:
                return Response({'detail': str(e.message if hasattr(e, 'message') else e)}, status=status.HTTP_400_BAD_REQUEST)

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
        return Response(message.as_payload(), status=status.HTTP_201_CREATED)


class MobileChatAttachmentDownloadView(MobileChatView):
    def get(self, request, pk):
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
