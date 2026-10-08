from django.conf import settings
from django.utils import timezone
from django.views.decorators.http import require_GET
from .views import page
from .workforce_views import roles, ADMINS


@roles(ADMINS)
@require_GET
def employee_locations(request):
    response = page(request, 'employee_locations', 'employee-locations', today=timezone.localdate(),
                    map_config={'tiles': settings.EMPLOYEE_LOCATION_TILE_URL,
                                'attribution': settings.EMPLOYEE_LOCATION_TILE_ATTRIBUTION})
    response['Cache-Control'] = 'private, no-store'
    return response
