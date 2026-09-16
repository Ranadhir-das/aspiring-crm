from datetime import datetime

from django.conf import settings
from django.http import FileResponse, Http404
from django.shortcuts import render
from django.utils import timezone


def _apk_info():
    """Best-effort file stats for the current build; None fields render as 'coming soon'."""
    path = settings.CALLER_APK_PATH
    if not path.is_file():
        return {'available': False, 'size_mb': None, 'built_at': None}
    stat = path.stat()
    return {
        'available': True,
        'size_mb': round(stat.st_size / (1024 * 1024), 1),
        'built_at': timezone.make_aware(datetime.fromtimestamp(stat.st_mtime)),
    }


def app_intro(request):
    """Public app-introduction / download page — no login required, since a brand-new
    employee needs the app before they have (or can create) a CRM account."""
    return render(request, 'web/app_intro.html', {'apk': _apk_info()})


def download_app(request):
    path = settings.CALLER_APK_PATH
    if not path.is_file():
        raise Http404('The app build is not available yet. Check back soon.')
    response = FileResponse(open(path, 'rb'), content_type='application/vnd.android.package-archive')
    response['Content-Disposition'] = 'attachment; filename="AspiringCaller.apk"'
    return response
