from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect
from django.views.decorators.http import require_POST

from apps.accounts.models import User
from .models import Notice
from .views import page, workspace

ADMIN_ROLES = {'ADMIN', 'SUPER_ADMIN'}


@workspace(employee=True)
def notices(request):
    can_create = request.user.role in ADMIN_ROLES
    query = Notice.objects.select_related('created_by')
    # Admins manage the whole board (including notices aimed at other roles);
    # everyone else only sees notices actually targeted at them.
    visible = list(query) if can_create else [n for n in query if n.is_visible_to(request.user)]
    return page(request, 'notices', 'notices', notices=visible, can_create=can_create,
                role_choices=User.Role.choices)


@require_POST
@workspace(management=True)
def notice_new(request):
    if request.user.role not in ADMIN_ROLES:
        raise PermissionDenied
    title = (request.POST.get('title') or '').strip()[:200]
    body = (request.POST.get('body') or '').strip()[:4000]
    roles = [r for r in request.POST.getlist('roles') if r in dict(User.Role.choices)]
    if title and body:
        Notice.objects.create(title=title, body=body, roles=roles, created_by=request.user)
    return redirect('web:notices')


@require_POST
@workspace(management=True)
def notice_delete(request, pk):
    if request.user.role not in ADMIN_ROLES:
        raise PermissionDenied
    Notice.objects.filter(pk=pk).delete()
    return redirect('web:notices')
