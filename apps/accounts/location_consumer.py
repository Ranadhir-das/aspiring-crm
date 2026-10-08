from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer
from django.contrib.sessions.models import Session
from django.utils import timezone

from .location_service import LOCATION_GROUP
from .models import User


class EmployeeLocationConsumer(AsyncJsonWebsocketConsumer):
    @database_sync_to_async
    def allowed(self):
        user = self.scope.get('user')
        session = self.scope.get('session')
        # This admin stream uses CRM session cookies only, never URL credentials.
        return bool(user and user.is_authenticated and session and session.session_key
                    and not self.scope.get('query_string')
                    and Session.objects.filter(session_key=session.session_key, expire_date__gt=timezone.now()).exists()
                    and User.objects.filter(pk=user.pk, is_active=True, role__in=['ADMIN', 'SUPER_ADMIN']).exists())

    async def connect(self):
        if not await self.allowed():
            await self.close(code=4003)
            return
        await self.channel_layer.group_add(LOCATION_GROUP, self.channel_name)
        await self.accept()

    async def disconnect(self, code):
        await self.channel_layer.group_discard(LOCATION_GROUP, self.channel_name)

    async def location_changed(self, event):
        if not await self.allowed():
            await self.close(code=4003)
            return
        await self.send_json({'type': 'location.changed'})
