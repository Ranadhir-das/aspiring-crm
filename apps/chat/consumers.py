from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer

from .models import ChatChannel


class ChatConsumer(AsyncJsonWebsocketConsumer):
    """Broadcast-only: clients never send over this socket. Sending a message goes
    through the normal authenticated REST endpoint (apps.chat.views), which persists
    it and pushes it to this channel's group — this socket just delivers that push
    live. Keeps writes on the same auditable, validated path for web and mobile."""

    async def connect(self):
        user = self.scope.get('user')
        if not user or not user.is_authenticated:
            await self.close(code=4001)
            return

        self.channel_id = self.scope['url_route']['kwargs']['channel_id']
        if not await self.has_access(user, self.channel_id):
            await self.close(code=4003)
            return

        self.group_name = f'chat_{self.channel_id}'
        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()

    async def disconnect(self, close_code):
        if hasattr(self, 'group_name'):
            await self.channel_layer.group_discard(self.group_name, self.channel_name)

    @database_sync_to_async
    def has_access(self, user, channel_id):
        try:
            channel = ChatChannel.objects.get(pk=channel_id)
        except ChatChannel.DoesNotExist:
            return False
        return channel.is_member(user)

    async def chat_message(self, event):
        await self.send_json(event['message'])
