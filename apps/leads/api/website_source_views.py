from rest_framework import serializers, status
from rest_framework.authentication import SessionAuthentication
from rest_framework.generics import ListCreateAPIView, RetrieveUpdateAPIView
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.api.authentication import VerifiedSessionAuthentication
from apps.leads.api.service_views import IsManagerOrAdmin
from apps.leads.models import Service, WebsiteSource, generate_api_key


class WebsiteSourceAdminSerializer(serializers.ModelSerializer):
    api_key_masked = serializers.CharField(read_only=True)
    api_key = serializers.CharField(read_only=True, required=False)
    warning = serializers.CharField(read_only=True, required=False)
    default_service_code = serializers.CharField(source="default_service.code", read_only=True)
    allowed_service_codes = serializers.SerializerMethodField()

    class Meta:
        model = WebsiteSource
        fields = [
            "id",
            "name",
            "code",
            "default_service",
            "default_service_code",
            "allowed_services",
            "allowed_service_codes",
            "allowed_origins",
            "is_active",
            "api_key_masked",
            "api_key",
            "warning",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["created_at", "updated_at", "api_key_masked", "api_key", "warning"]

    def get_allowed_service_codes(self, obj):
        return list(obj.allowed_services.values_list("code", flat=True))

    def create(self, validated_data):
        allowed_services = validated_data.pop("allowed_services", [])
        if "api_key" not in validated_data or not validated_data["api_key"]:
            validated_data["api_key"] = generate_api_key()
        source = WebsiteSource.objects.create(**validated_data)
        if allowed_services:
            source.allowed_services.set(allowed_services)
        source._raw_api_key = source.api_key
        return source

    def to_representation(self, instance):
        data = super().to_representation(instance)
        # Only expose raw api_key if explicitly set on creation or regeneration
        if hasattr(instance, "_raw_api_key"):
            data["api_key"] = instance._raw_api_key
            data["warning"] = "Store this API key securely. It will not be shown again."
        else:
            data.pop("api_key", None)
            data.pop("warning", None)
        return data


class WebsiteSourceListCreateView(ListCreateAPIView):
    authentication_classes = (SessionAuthentication, VerifiedSessionAuthentication)
    permission_classes = (IsManagerOrAdmin,)
    serializer_class = WebsiteSourceAdminSerializer
    pagination_class = None
    queryset = WebsiteSource.objects.all().select_related("default_service").prefetch_related("allowed_services")


class WebsiteSourceDetailView(RetrieveUpdateAPIView):
    authentication_classes = (SessionAuthentication, VerifiedSessionAuthentication)
    permission_classes = (IsManagerOrAdmin,)
    serializer_class = WebsiteSourceAdminSerializer
    queryset = WebsiteSource.objects.all().select_related("default_service").prefetch_related("allowed_services")


class WebsiteSourceToggleView(APIView):
    authentication_classes = (SessionAuthentication, VerifiedSessionAuthentication)
    permission_classes = (IsManagerOrAdmin,)

    def post(self, request, pk):
        try:
            source = WebsiteSource.objects.get(pk=pk)
        except WebsiteSource.DoesNotExist:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
        source.is_active = not source.is_active
        source.save(update_fields=["is_active", "updated_at"])
        return Response({
            "id": source.pk,
            "is_active": source.is_active,
            "status": "active" if source.is_active else "inactive",
        })


class WebsiteSourceRegenerateKeyView(APIView):
    authentication_classes = (SessionAuthentication, VerifiedSessionAuthentication)
    permission_classes = (IsManagerOrAdmin,)

    def post(self, request, pk):
        try:
            source = WebsiteSource.objects.get(pk=pk)
        except WebsiteSource.DoesNotExist:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
        new_key = source.regenerate_api_key()
        return Response({
            "id": source.pk,
            "api_key": new_key,
            "api_key_masked": source.api_key_masked,
            "warning": "Store this API key securely. It will not be shown again.",
        })
