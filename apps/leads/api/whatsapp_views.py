from django.shortcuts import get_object_or_404
from rest_framework import serializers, status
from rest_framework.authentication import SessionAuthentication
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.api.authentication import VerifiedSessionAuthentication
from apps.leads.models import Lead, WhatsAppTemplate
from apps.leads.whatsapp_service import (
    normalize_phone_for_whatsapp,
    record_whatsapp_initiated,
    user_can_access_lead,
)


class WhatsAppTemplateSerializer(serializers.ModelSerializer):
    class Meta:
        model = WhatsAppTemplate
        fields = ['id', 'title', 'message']


class WhatsAppTemplateListView(APIView):
    authentication_classes = (SessionAuthentication, VerifiedSessionAuthentication)
    permission_classes = [IsAuthenticated]

    def get(self, request):
        templates = WhatsAppTemplate.objects.filter(is_active=True).order_by('title')
        return Response(WhatsAppTemplateSerializer(templates, many=True).data)


class WhatsAppInitiateSerializer(serializers.Serializer):
    template_id = serializers.IntegerField(required=False, allow_null=True)
    message = serializers.CharField(required=False, allow_blank=True, max_length=4000)
    source = serializers.ChoiceField(choices=['CALLER', 'CRM'], default='CALLER')


class WhatsAppInitiateView(APIView):
    authentication_classes = (SessionAuthentication, VerifiedSessionAuthentication)
    permission_classes = [IsAuthenticated]

    def post(self, request, lead_id):
        lead = get_object_or_404(Lead, pk=lead_id)
        if not user_can_access_lead(request.user, lead):
            raise PermissionDenied("You are not authorized to access this lead.")

        serializer = WhatsAppInitiateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        template_id = serializer.validated_data.get('template_id')
        template = None
        if template_id:
            try:
                template = WhatsAppTemplate.objects.get(pk=template_id, is_active=True)
            except WhatsAppTemplate.DoesNotExist:
                raise ValidationError({"template_id": "Invalid or inactive template."})

        custom_message = serializer.validated_data.get('message', '')
        source = serializer.validated_data.get('source', 'CALLER')

        result = record_whatsapp_initiated(
            lead=lead,
            user=request.user,
            template=template,
            custom_message=custom_message,
            source=source,
        )
        return Response(result, status=status.HTTP_200_OK)
