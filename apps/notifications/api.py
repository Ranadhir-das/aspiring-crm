from django.shortcuts import get_object_or_404
from django.urls import path
from django.utils import timezone
from rest_framework import serializers
from rest_framework.generics import ListAPIView
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.api.authentication import VerifiedSessionAuthentication
from .models import Notification


class NotificationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Notification
        fields = ('id', 'type', 'title', 'body', 'data', 'is_read', 'read_at', 'created_at')
        read_only_fields = fields


class NotificationPagination(PageNumberPagination):
    page_size = 25


class OwnedNotifications:
    authentication_classes = [VerifiedSessionAuthentication]
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return Notification.objects.filter(recipient=self.request.user)


class NotificationListView(OwnedNotifications, ListAPIView):
    serializer_class = NotificationSerializer
    pagination_class = NotificationPagination


class UnreadCountView(OwnedNotifications, APIView):
    def get(self, request):
        return Response({'unread_count': self.get_queryset().filter(is_read=False).count()})


class MarkReadView(OwnedNotifications, APIView):
    def post(self, request, pk):
        # Conditional UPDATE is atomic and preserves the first read timestamp.
        get_object_or_404(self.get_queryset(), pk=pk)
        self.get_queryset().filter(pk=pk, is_read=False).update(is_read=True, read_at=timezone.now())
        return Response(NotificationSerializer(get_object_or_404(self.get_queryset(), pk=pk)).data)


class MarkAllReadView(OwnedNotifications, APIView):
    def post(self, request):
        updated = self.get_queryset().filter(is_read=False).update(is_read=True, read_at=timezone.now())
        return Response({'updated': updated})


urlpatterns = [
    path('', NotificationListView.as_view()),
    path('unread-count/', UnreadCountView.as_view()),
    path('read-all/', MarkAllReadView.as_view()),
    path('<int:pk>/read/', MarkReadView.as_view()),
]
