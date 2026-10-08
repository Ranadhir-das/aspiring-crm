"""Configured release metadata only; APK bytes are served by Nginx."""
from urllib.parse import urlsplit

from django.conf import settings
from rest_framework import serializers
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .authentication import VerifiedSessionAuthentication


class AppReleaseSerializer(serializers.Serializer):
    latest_version = serializers.CharField(max_length=100)
    version_code = serializers.IntegerField(min_value=1)
    minimum_version_code = serializers.IntegerField(min_value=1)
    mandatory = serializers.BooleanField()
    download_url = serializers.URLField(max_length=2048)
    release_notes = serializers.ListField(child=serializers.CharField(max_length=2000), required=False, default=list, max_length=50)

    def validate(self, data):
        url = urlsplit(data['download_url'])
        if url.scheme != 'https' or url.username or url.password or url.query or url.fragment:
            raise serializers.ValidationError('Use a public HTTPS APK URL without credentials or query parameters.')
        if data['minimum_version_code'] > data['version_code']:
            raise serializers.ValidationError('Minimum version exceeds the published release.')
        return data


class AppUpdateView(APIView):
    authentication_classes = [VerifiedSessionAuthentication]
    permission_classes = [IsAuthenticated]

    def get(self, request):
        release = AppReleaseSerializer(data=settings.CALLER_APP_UPDATE)
        if not release.is_valid():
            return Response({'detail': 'App update information is not available yet. Contact your administrator.'}, status=503,
                            headers={'Cache-Control': 'no-store'})
        return Response(release.validated_data, headers={'Cache-Control': 'no-store'})
