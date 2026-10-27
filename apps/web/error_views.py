import uuid
from django.utils import timezone
from django.http import (
    JsonResponse,
    HttpResponseBadRequest,
    HttpResponseForbidden,
    HttpResponseNotFound,
    HttpResponseServerError,
)
from django.shortcuts import render


def _is_api_request(request):
    """Detect if the incoming request expects a JSON API payload."""
    path = getattr(request, 'path', '') or ''
    accept = ''
    if hasattr(request, 'headers'):
        accept = request.headers.get('Accept', '')
    elif hasattr(request, 'META'):
        accept = request.META.get('HTTP_ACCEPT', '')
    return path.startswith('/api/') or 'application/json' in accept


def _get_common_context(request, code, title, default_msg, detail=None):
    incident_id = uuid.uuid4().hex[:10].upper()
    return {
        'error_code': code,
        'error_title': title,
        'error_message': detail or default_msg,
        'incident_id': incident_id,
        'request_path': getattr(request, 'path', '/'),
        'timestamp': timezone.now(),
        'is_authenticated': getattr(request, 'user', None) and request.user.is_authenticated,
    }


def custom_400_view(request, exception=None):
    """400 Bad Request handler."""
    detail = str(exception) if exception else None
    if _is_api_request(request):
        return JsonResponse({
            'error': 'Bad Request',
            'status_code': 400,
            'detail': detail or 'The request could not be processed due to invalid syntax or parameters.',
            'path': getattr(request, 'path', ''),
        }, status=400)
    ctx = _get_common_context(
        request, 400, 'Bad Request',
        'We could not understand this request. Please verify the URL parameters or form submission and try again.',
        detail=detail
    )
    return HttpResponseBadRequest(render(request, '400.html', ctx).content)


def custom_403_view(request, exception=None):
    """403 Forbidden / Access Denied handler."""
    detail = str(exception) if exception else None
    if _is_api_request(request):
        return JsonResponse({
            'error': 'Forbidden',
            'status_code': 403,
            'detail': detail or 'You do not have permission to access this resource.',
            'path': getattr(request, 'path', ''),
        }, status=403)
    ctx = _get_common_context(
        request, 403, 'Access Restricted',
        'You do not have permission to view this page or perform this action in Vaani CRM.',
        detail=detail
    )
    return HttpResponseForbidden(render(request, '403.html', ctx).content)


def custom_404_view(request, exception=None):
    """404 Not Found handler."""
    detail = str(exception) if exception else None
    if _is_api_request(request):
        return JsonResponse({
            'error': 'Not Found',
            'status_code': 404,
            'detail': detail or 'The requested resource or endpoint was not found.',
            'path': getattr(request, 'path', ''),
        }, status=404)
    ctx = _get_common_context(
        request, 404, 'Page Not Found',
        'The page, lead record, or destination you are looking for has moved, been archived, or does not exist.',
        detail=detail
    )
    return HttpResponseNotFound(render(request, '404.html', ctx).content)


def custom_500_view(request):
    """500 Internal Server Error handler."""
    if _is_api_request(request):
        return JsonResponse({
            'error': 'Internal Server Error',
            'status_code': 500,
            'detail': 'An unexpected server error occurred. Our engineering team has been notified.',
            'path': getattr(request, 'path', ''),
        }, status=500)
    ctx = _get_common_context(
        request, 500, 'Internal Server Error',
        'Our CRM servers encountered an unexpected issue while processing your request. Please try again shortly.'
    )
    return HttpResponseServerError(render(request, '500.html', ctx).content)


def custom_502_view(request, exception=None):
    """502 Bad Gateway handler."""
    if _is_api_request(request):
        return JsonResponse({
            'error': 'Bad Gateway',
            'status_code': 502,
            'detail': 'The CRM upstream server or proxy received an invalid response.',
            'path': getattr(request, 'path', ''),
        }, status=502)
    ctx = _get_common_context(
        request, 502, 'Bad Gateway',
        'The CRM backend service is temporarily unreachable or undergoing a deployment restart. Please refresh in a moment.'
    )
    response = render(request, '502.html', ctx)
    response.status_code = 502
    return response


def custom_503_view(request, exception=None):
    """503 Service Unavailable / Maintenance handler."""
    if _is_api_request(request):
        return JsonResponse({
            'error': 'Service Unavailable',
            'status_code': 503,
            'detail': 'The CRM service is temporarily offline for maintenance or experiencing high traffic.',
            'path': getattr(request, 'path', ''),
        }, status=503)
    ctx = _get_common_context(
        request, 503, 'System Maintenance',
        'Vaani CRM is currently undergoing scheduled platform upgrades or database maintenance. We will be back online shortly.'
    )
    response = render(request, '503.html', ctx)
    response.status_code = 503
    return response


def error_preview_view(request, code):
    """
    Development/management preview route for testing all error templates:
    /errors/400/, /errors/403/, /errors/404/, /errors/500/, /errors/502/, /errors/503/
    """
    code_int = int(code)
    dispatch = {
        400: custom_400_view,
        403: custom_403_view,
        404: custom_404_view,
        500: custom_500_view,
        502: custom_502_view,
        503: custom_503_view,
    }
    handler = dispatch.get(code_int, custom_404_view)
    if code_int == 500:
        return handler(request)
    return handler(request)

