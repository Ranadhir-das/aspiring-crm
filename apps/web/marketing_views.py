import json
import logging
import re
from django.conf import settings
from django.http import HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_http_methods

from apps.leads.models import Lead
from .app_intro_views import _apk_info

logger = logging.getLogger(__name__)

EMAIL_REGEX = re.compile(r'^[^\s@]+@[^\s@]+\.[^\s@]+$')


@ensure_csrf_cookie
def landing_page(request):
    """
    Public SaaS Marketing Landing Page for Vaani.
    When visited by an anonymous user, returns the premium commercial marketing website.
    If an existing authenticated CRM staff member visits '/' without explicit '?landing=1',
    seamlessly routes them to their internal CRM dashboard.
    """
    if request.user.is_authenticated and not request.GET.get('landing'):
        from . import analytics_views
        return analytics_views.dashboard_view(request)

    context = {
        'page_title': 'Vaani CRM | Lead Management, Calling & Sales CRM',
        'meta_description': 'Vaani is an all-in-one CRM for lead management, calling, follow-ups, team performance, WhatsApp communication and admissions.',
        'canonical_url': request.build_absolute_uri('/'),
        'current_section': 'landing',
        'is_public_marketing': True,
        'user_is_authenticated': request.user.is_authenticated,
    }
    return render(request, 'web/landing.html', context)


@ensure_csrf_cookie
def product_app_page(request):
    """
    Dedicated Vaani Product & Mobile App Marketing Page at /app/.
    Highlights Vaani CRM + Vaani Caller App with live APK download stats and deep feature tours.
    """
    context = {
        'page_title': 'Vaani Product & Mobile Caller App | Vaani CRM',
        'meta_description': 'Explore the Vaani CRM platform and Android Caller App. Seamless lead management, automated dialing, call tracking, and performance analytics.',
        'canonical_url': request.build_absolute_uri('/app/'),
        'current_section': 'app',
        'is_public_marketing': True,
        'user_is_authenticated': request.user.is_authenticated,
        'apk': _apk_info(),
    }
    return render(request, 'web/product_app.html', context)


@require_http_methods(["POST"])
def book_demo_view(request):
    """
    Handles public "Book a Free Demo" submissions.
    Reuses the existing Lead model safely (without triggering student caller distribution),
    providing comprehensive input validation, CSRF verification, and honeypot spam protection.
    """
    # 1. Parse payload (JSON or Form POST)
    data = {}
    if request.content_type == 'application/json':
        try:
            data = json.loads(request.body.decode('utf-8'))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return JsonResponse({'success': False, 'message': 'Invalid JSON request payload.'}, status=400)
    else:
        data = request.POST.dict()

    # 2. Honeypot check for bots
    if data.get('website_url'):
        logger.warning("Spam bot detected in demo request via honeypot.")
        return JsonResponse({
            'success': True,
            'message': 'Thank you! Your demo request has been received. Our team will contact you shortly.'
        }, status=200)

    # 3. Validation
    errors = {}
    name = (data.get('name') or '').strip()
    company = (data.get('company') or '').strip()
    phone = (data.get('phone') or '').strip()
    email = (data.get('email') or '').strip()
    employees = (data.get('employees') or '').strip()
    message = (data.get('message') or '').strip()

    if not name or len(name) < 2:
        errors['name'] = 'Full Name is required (at least 2 characters).'

    if not company or len(company) < 2:
        errors['company'] = 'Company Name is required.'

    cleaned_digits = re.sub(r'[^0-9]', '', phone)
    if not phone or len(cleaned_digits) < 8:
        errors['phone'] = 'A valid phone number with at least 8 digits is required.'

    if not email or not EMAIL_REGEX.match(email):
        errors['email'] = 'A valid corporate or work email address is required.'

    if not employees:
        errors['employees'] = 'Please select your organization team size.'

    if errors:
        return JsonResponse({
            'success': False,
            'message': 'Please correct the errors in the form.',
            'errors': errors
        }, status=400)

    # 4. Persistence into existing Lead model (without caller availability)
    try:
        notes_content = f"Company: {company}\nTeam Size: {employees}\nMessage: {message}".strip()
        lead = Lead.objects.create(
            name=name,
            phone=phone,
            email=email,
            source='vaani_demo_request',
            campaign='SaaS Website Demo Booking',
            notes=notes_content,
            status=Lead.Status.PENDING,
        )
        logger.info(f"New Vaani demo request received: {name} ({company}) - Lead ID: {lead.pk}")
    except Exception as exc:
        logger.error(f"Failed to record demo request: {exc}", exc_info=True)
        return JsonResponse({
            'success': False,
            'message': 'Unable to process your request at this time. Please try again later.'
        }, status=500)

    return JsonResponse({
        'success': True,
        'message': 'Thank you! Your demo request has been received. Our team will contact you shortly.'
    }, status=201)


def robots_txt_view(request):
    """
    Standard robots.txt for search engines.
    Allows crawling public marketing pages, blocks internal CRM workspace paths.
    """
    lines = [
        "User-agent: *",
        "Allow: /",
        "Allow: /app/",
        "Allow: /login/",
        "Disallow: /api/",
        "Disallow: /leads/",
        "Disallow: /calls/",
        "Disallow: /team/",
        "Disallow: /attendance/",
        "Disallow: /payroll/",
        "Disallow: /reports/",
        "Disallow: /expenses/",
        "Disallow: /admin/",
        "",
        f"Sitemap: {request.build_absolute_uri('/sitemap.xml')}",
    ]
    return HttpResponse("\n".join(lines), content_type="text/plain")


def sitemap_xml_view(request):
    """
    Standard sitemap.xml for search engine indexing.
    """
    root = request.build_absolute_uri('/')
    app_url = request.build_absolute_uri('/app/')
    login_url = request.build_absolute_uri('/login/')

    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url>
    <loc>{root}</loc>
    <changefreq>weekly</changefreq>
    <priority>1.0</priority>
  </url>
  <url>
    <loc>{app_url}</loc>
    <changefreq>weekly</changefreq>
    <priority>0.9</priority>
  </url>
  <url>
    <loc>{login_url}</loc>
    <changefreq>monthly</changefreq>
    <priority>0.5</priority>
  </url>
</urlset>"""
    return HttpResponse(xml, content_type="application/xml")
