from io import BytesIO
import logging

from django.conf import settings
from rest_framework.exceptions import APIException, PermissionDenied
from rest_framework.parsers import JSONParser
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.throttling import SimpleRateThrottle
from rest_framework.views import APIView

from apps.leads.models import WebsiteSource
from apps.leads.public_intake import create_website_lead
from .public_serializers import PublicLeadSerializer

logger = logging.getLogger('apps.leads')


class PayloadTooLarge(APIException):
    status_code = 413
    default_detail = 'Submission exceeds 16 KiB.'


class PublicLeadJSONParser(JSONParser):
    def parse(self, stream, media_type=None, parser_context=None):
        body = stream.read(16 * 1024 + 1)
        if len(body) > 16 * 1024:
            raise PayloadTooLarge()
        return super().parse(BytesIO(body), media_type, parser_context)


class PublicLeadBurstThrottle(SimpleRateThrottle):
    scope = 'public_lead_burst'

    def get_rate(self):
        return settings.PUBLIC_LEAD_BURST_RATE

    def get_cache_key(self, request, view):
        # Do not trust a client-supplied X-Forwarded-For header.
        return self.cache_format % {'scope': self.scope, 'ident': request.META.get('REMOTE_ADDR', '')}


class PublicLeadDailyThrottle(PublicLeadBurstThrottle):
    scope = 'public_lead_daily'

    def get_rate(self):
        return settings.PUBLIC_LEAD_DAILY_RATE


class ApiKeyAuthenticationFailed(APIException):
    status_code = 401
    default_detail = 'Invalid API key.'


def authenticate_website_source(request, data_api_key=None, requested_source=''):
    """
    Authenticate against configured WebsiteSource records.
    Order of precedence:
    1. Header: X-Api-Key
    2. Header: Authorization: Api-Key <key> or Bearer <key>
    3. Serializer field: api_key
    """
    key = (
        request.headers.get('X-Api-Key')
        or request.META.get('HTTP_X_API_KEY')
    )
    if not key:
        auth_header = request.headers.get('Authorization') or request.META.get('HTTP_AUTHORIZATION', '')
        if auth_header.startswith('Api-Key '):
            key = auth_header[8:].strip()
        elif auth_header.startswith('Bearer '):
            key = auth_header[7:].strip()
    if not key and data_api_key:
        key = str(data_api_key).strip()

    if not key:
        raise ApiKeyAuthenticationFailed('API key is required for website lead submissions.')

    source = WebsiteSource.objects.filter(api_key=key).first()
    if not source:
        logger.warning(
            "website_lead_auth_failed source=%s ip=%s reason=invalid_key",
            requested_source or "unknown",
            request.META.get('REMOTE_ADDR', 'unknown'),
        )
        raise ApiKeyAuthenticationFailed('Invalid API key.')
    if not source.is_active:
        logger.warning(
            "website_lead_auth_failed source=%s ip=%s reason=inactive_source",
            source.code,
            request.META.get('REMOTE_ADDR', 'unknown'),
        )
        raise PermissionDenied('This website lead source is inactive.')
    return source


class PublicLeadCreateView(APIView):
    authentication_classes = ()
    permission_classes = (AllowAny,)
    parser_classes = (PublicLeadJSONParser,)
    throttle_classes = (PublicLeadBurstThrottle, PublicLeadDailyThrottle)
    http_method_names = ['post', 'options']

    def post(self, request):
        serializer = PublicLeadSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        values = dict(serializer.validated_data)
        api_key_val = values.pop('api_key', None)
        website_source = authenticate_website_source(request, api_key_val, values.get('source', ''))

        # Enforce allowed_origins for browser submissions
        origin = request.headers.get('Origin') or request.META.get('HTTP_ORIGIN')
        if origin and website_source and website_source.allowed_origins:
            clean_origin = origin.strip().rstrip('/')
            allowed = [str(o).strip().rstrip('/') for o in website_source.allowed_origins if str(o).strip()]
            if clean_origin not in allowed:
                logger.warning(
                    "website_lead_origin_rejected source=%s origin=%s ip=%s",
                    website_source.code,
                    origin,
                    request.META.get('REMOTE_ADDR', 'unknown'),
                )
                raise PermissionDenied(f"Origin '{origin}' is not permitted.")

        # Honeypot: silently drop bot submissions without storing
        if values.pop('website', ''):
            logger.info("website_lead_honeypot_dropped ip=%s", request.META.get('REMOTE_ADDR', 'unknown'))
            response = Response({'detail': 'Thank you. Your enquiry has been received.'}, status=202)
            response['Cache-Control'] = 'no-store'
            return response

        ip_addr = request.META.get('REMOTE_ADDR', '')
        lead, is_duplicate, submission = create_website_lead(
            values, website_source=website_source, ip_address=ip_addr
        )


        response_data = {'detail': 'Thank you. Your enquiry has been received.'}
        has_key = bool(website_source or api_key_val or getattr(settings, 'WEBSITE_LEAD_REQUIRE_API_KEY', False))
        if has_key:
            response_data['is_duplicate'] = is_duplicate
            response_data['lead_id'] = lead.pk if lead else None
            if is_duplicate:
                response_data['detail'] = 'Enquiry received for existing lead.'

        response = Response(response_data, status=202)
        response['Cache-Control'] = 'no-store'
        return response
