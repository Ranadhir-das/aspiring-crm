"""Canonical, permission-aware presentation of employee identities."""
from django.urls import reverse
from django.utils.html import format_html
from .forms import MANAGEMENT


def can_view_employee(viewer, employee):
    return bool(viewer and viewer.is_authenticated and employee and
                (viewer.role in MANAGEMENT or viewer.pk == employee.pk))


def employee_link_html(viewer, employee):
    if not employee:
        return 'Unassigned'
    name = employee.get_full_name() or employee.username
    if can_view_employee(viewer, employee):
        return format_html('<a class="employee-link" href="{}">{}</a>',
                           reverse('web:caller-detail', args=[employee.pk]), name)
    return name
