from django.urls import reverse

from apps.accounts.models import User
from .forms import MANAGEMENT
from .models import AttendancePhotoRequest, LeaveRequest


def employee_name(user):
    return user.get_full_name() or user.username


def notifications(request):
    """Aggregates everything a manager/admin needs to act on into one bell menu:
    pending leave requests, pending enrollment/signup photo reviews, and inactive
    employee accounts awaiting activation. This is the ONLY place these show up —
    they are deliberately not duplicated onto the Overview dashboard or anywhere
    else. Nothing is queried for roles that can't act on any of it, or for
    logged-out requests."""
    user = getattr(request, 'user', None)
    if not user or not user.is_authenticated or user.role not in MANAGEMENT:
        return {}

    items = []

    leave_url = reverse('web:leaves') + '?status=PENDING'
    for leave in (LeaveRequest.objects.filter(status=LeaveRequest.Status.PENDING)
                  .select_related('employee').order_by('-created_at')[:20]):
        items.append({
            'icon': '🗓',
            'title': f'{employee_name(leave.employee)} requested leave',
            'detail': f'{leave.start_date:%d %b} – {leave.end_date:%d %b}',
            'url': leave_url,
            'created_at': leave.created_at,
        })

    # Photo review (enrollment/signup) and account activation are restricted to
    # ADMIN/SUPER_ADMIN elsewhere (see photo_views.queue, employees()) — match
    # that here so MANAGERs don't see a notification they'd hit a 403 clicking into.
    if user.role in {'ADMIN', 'SUPER_ADMIN'}:
        photo_url = reverse('web:photo-attendance')
        for req in (AttendancePhotoRequest.objects.filter(action='ENROLL', status='PENDING')
                    .select_related('employee').order_by('-created_at')[:20]):
            is_signup = req.employee.registration_pending
            items.append({
                'icon': '🆕' if is_signup else '📷',
                'title': f'{employee_name(req.employee)} {"signed up" if is_signup else "submitted an enrollment photo"}',
                'detail': 'Awaiting photo approval',
                'url': photo_url,
                'created_at': req.created_at,
            })

        accounts_url = reverse('web:employees') + '?status=inactive'
        account_requests = User.objects.filter(is_active=False).exclude(pk=user.pk)
        if user.role != 'SUPER_ADMIN':
            account_requests = account_requests.exclude(role__in=['SUPER_ADMIN', 'ADMIN'])
        for account in account_requests.order_by('-date_joined')[:20]:
            items.append({
                'icon': '👤',
                'title': f'{employee_name(account)} needs an account review',
                'detail': 'New signup or deactivated account awaiting activation',
                'url': accounts_url,
                'created_at': account.date_joined,
            })

    items.sort(key=lambda item: item['created_at'], reverse=True)

    return {
        'show_notifications': True,
        'header_notifications': items[:30],
        'header_notifications_count': len(items),
    }
