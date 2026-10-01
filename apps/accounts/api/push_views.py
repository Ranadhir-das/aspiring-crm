from django.db import transaction
from django.utils import timezone
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.api.authentication import VerifiedSessionAuthentication
from apps.accounts.models import PushDevice
from .push_serializers import PushDeviceRegistrationSerializer, PushDeviceSerializer


class PushDeviceView(APIView):
    authentication_classes = [VerifiedSessionAuthentication]
    permission_classes = [IsAuthenticated]

    def get(self, request):
        devices = PushDevice.objects.filter(user=request.user)
        return Response(PushDeviceSerializer(devices, many=True).data)

    @transaction.atomic
    def post(self, request):
        serializer = PushDeviceRegistrationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        values = dict(serializer.validated_data)
        token = values.pop('expo_push_token')
        # Unique token + get_or_create handles concurrent inserts; the row lock
        # serializes updates, including the loser of a concurrent insertion.
        device, created = PushDevice.objects.select_for_update().get_or_create(
            expo_push_token=token,
            defaults={**values, 'user': request.user, 'active': True, 'last_seen': timezone.now()},
        )
        if device.user_id != request.user.pk:
            return Response({'detail': 'This push token is already registered to another account.'}, status=409)
        if not created:
            device.platform = values['platform']
            device.device_name = values['device_name']
            device.active = True
            device.last_seen = timezone.now()
            device.save(update_fields=['platform', 'device_name', 'active', 'last_seen', 'updated_at'])
        return Response(PushDeviceSerializer(device).data, status=201 if created else 200)


class PushDeviceDetailView(APIView):
    authentication_classes = [VerifiedSessionAuthentication]
    permission_classes = [IsAuthenticated]

    def delete(self, request, pk):
        updated = PushDevice.objects.filter(pk=pk, user=request.user).update(active=False, updated_at=timezone.now())
        if not updated:
            return Response({'detail': 'Not found.'}, status=404)
        return Response(status=204)
