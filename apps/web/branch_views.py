"""
Web views for Branch Management and Employee-to-Branch Assignments in Vaani CRM.
Accessible exclusively to authorized Administrators (SUPER_ADMIN, ADMIN, or superuser).
"""
from functools import wraps
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.accounts.models import User
from apps.activity.models import EmployeeOfficeAssignment, OfficeLocation
from apps.activity.branch_services import (
    assign_employee_branches,
    get_branch_summary_kpis,
    get_unassigned_employees_query,
    toggle_branch_status,
)
from apps.web.models import AuditEvent
from apps.web.views import page, paginate
from .branch_forms import BranchForm, EmployeeBranchAssignmentForm

ADMIN_ROLES = {User.Role.SUPER_ADMIN, User.Role.ADMIN}


def admin_only(view_func):
    """Restricts access to SUPER_ADMIN, ADMIN, or superusers."""
    @login_required
    @wraps(view_func)
    def wrapped(request, *args, **kwargs):
        if request.user.role not in ADMIN_ROLES and not request.user.is_superuser:
            raise PermissionDenied("Only authorized administrators can access branch management.")
        return view_func(request, *args, **kwargs)
    return wrapped


# ---------------------------------------------------------------------------
# Branch Management Views
# ---------------------------------------------------------------------------

@admin_only
def branch_list(request):
    """View and search all branches/offices with assigned employee counts and metrics."""
    query = OfficeLocation.objects.all().annotate(
        primary_emp_count=Count(
            "employee_assignments",
            filter=Q(employee_assignments__is_primary=True, employee_assignments__is_active=True, employee_assignments__user__is_active=True),
            distinct=True,
        ),
        total_emp_count=Count(
            "employee_assignments",
            filter=Q(employee_assignments__is_active=True, employee_assignments__user__is_active=True),
            distinct=True,
        ),
    ).order_by("-is_active", "name")

    search_query = request.GET.get("q", "").strip()
    if search_query:
        query = query.filter(
            Q(name__icontains=search_query) |
            Q(code__icontains=search_query) |
            Q(address__icontains=search_query)
        )

    selected_status = request.GET.get("status", "").strip()
    if selected_status == "active":
        query = query.filter(is_active=True)
    elif selected_status == "inactive":
        query = query.filter(is_active=False)

    records = paginate(request, query)
    kpis = get_branch_summary_kpis()

    context = {
        "records": records,
        "branches": records,
        "kpis": kpis,
        "search_query": search_query,
        "selected_status": selected_status,
        "active_filters_count": (1 if search_query else 0) + (1 if selected_status else 0),
    }
    return page(request, "branches", "branches", **context)


@admin_only
def branch_create(request):
    """Create a new branch office location."""
    form = BranchForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        branch = form.save()
        messages.success(request, f"Branch '{branch.name}' ({branch.code}) created successfully.")
        return redirect("web:branches")

    context = {
        "form": form,
        "is_create": True,
        "title": "Create Branch",
    }
    return page(request, "branch_form", "branches", **context)


@admin_only
def branch_edit(request, pk):
    """Edit an existing branch office location."""
    branch = get_object_or_404(OfficeLocation, pk=pk)
    form = BranchForm(request.POST or None, instance=branch)

    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, f"Branch '{branch.name}' updated successfully.")
        return redirect("web:branches")

    # Fetch currently assigned active employees for this branch
    assignments = EmployeeOfficeAssignment.objects.filter(
        office=branch,
        is_active=True,
        user__is_active=True,
    ).select_related("user").order_by("-is_primary", "user__first_name", "user__username")

    context = {
        "form": form,
        "branch": branch,
        "is_create": False,
        "title": f"Edit Branch: {branch.name}",
        "assignments": assignments,
    }
    return page(request, "branch_form", "branches", **context)


@require_POST
@admin_only
def branch_toggle_status(request, pk):
    """Activate or deactivate a branch office location safely."""
    branch = get_object_or_404(OfficeLocation, pk=pk)
    new_status = not branch.is_active
    toggle_branch_status(branch, is_active=new_status, actor=request.user)

    status_str = "activated" if new_status else "deactivated"
    messages.success(request, f"Branch '{branch.name}' has been {status_str}.")
    return redirect("web:branches")


# ---------------------------------------------------------------------------
# Employee-to-Branch Assignment Views
# ---------------------------------------------------------------------------

@admin_only
def employee_branch_assignments_list(request):
    """List and filter all employees and their branch assignments."""
    employees_qs = User.objects.filter(is_active=True).prefetch_related(
        "office_assignments__office"
    ).order_by("first_name", "username")

    search_query = request.GET.get("q", "").strip()
    if search_query:
        employees_qs = employees_qs.filter(
            Q(username__icontains=search_query) |
            Q(first_name__icontains=search_query) |
            Q(last_name__icontains=search_query) |
            Q(email__icontains=search_query)
        )

    branch_filter = request.GET.get("branch", "").strip()
    if branch_filter:
        employees_qs = employees_qs.filter(
            office_assignments__office_id=branch_filter,
            office_assignments__is_active=True,
        ).distinct()

    status_filter = request.GET.get("assignment_status", "").strip()
    if status_filter == "unassigned":
        unassigned_ids = get_unassigned_employees_query().values_list("pk", flat=True)
        employees_qs = employees_qs.filter(pk__in=unassigned_ids)
    elif status_filter == "assigned":
        assigned_ids = EmployeeOfficeAssignment.objects.filter(
            is_active=True,
            is_primary=True,
            office__is_active=True,
        ).values_list("user_id", flat=True)
        employees_qs = employees_qs.filter(pk__in=assigned_ids)
    elif status_filter == "multi_branch":
        multi_ids = EmployeeOfficeAssignment.objects.filter(
            is_active=True,
            office__is_active=True,
        ).values("user_id").annotate(cnt=Count("office_id")).filter(cnt__gt=1).values_list("user_id", flat=True)
        employees_qs = employees_qs.filter(pk__in=multi_ids)

    role_filter = request.GET.get("role", "").strip()
    if role_filter in User.Role.values:
        employees_qs = employees_qs.filter(role=role_filter)

    records = paginate(request, employees_qs)

    # Attach primary and authorized offices to each employee in page
    employee_rows = []
    for emp in records:
        active_assignments = [a for a in emp.office_assignments.all() if a.is_active and a.office.is_active]
        primary_assignment = next((a for a in active_assignments if a.is_primary), None)
        additional_assignments = [a for a in active_assignments if not a.is_primary]
        employee_rows.append({
            "employee": emp,
            "primary": primary_assignment.office if primary_assignment else None,
            "additional": [a.office for a in additional_assignments],
            "is_unassigned": primary_assignment is None,
        })

    kpis = get_branch_summary_kpis()
    all_branches = OfficeLocation.objects.filter(is_active=True).order_by("name")

    context = {
        "records": records,
        "employee_rows": employee_rows,
        "kpis": kpis,
        "all_branches": all_branches,
        "search_query": search_query,
        "branch_filter": branch_filter,
        "status_filter": status_filter,
        "role_filter": role_filter,
        "roles": User.Role.choices,
        "active_filters_count": (
            (1 if search_query else 0) +
            (1 if branch_filter else 0) +
            (1 if status_filter else 0) +
            (1 if role_filter else 0)
        ),
    }
    return page(request, "employee_branch_assignments", "branches", **context)


@admin_only
def employee_branch_assign_edit(request, user_id):
    """Assign or modify branch locations and shift policies for an individual employee."""
    employee = get_object_or_404(User, pk=user_id, is_active=True)

    current_assignments = EmployeeOfficeAssignment.objects.filter(
        user=employee,
        is_active=True,
    ).select_related("office")

    current_primary = next((a.office for a in current_assignments if a.is_primary), None)
    current_additional = [a.office for a in current_assignments if not a.is_primary]

    old_shift_workflow_enabled = employee.shift_workflow_enabled
    old_geofence_enforced = employee.shift_geofence_enforced

    if request.method == "POST":
        form = EmployeeBranchAssignmentForm(request.POST, user=employee, actor=request.user)
        if form.is_valid():
            primary = form.cleaned_data["primary_branch"]
            additional = form.cleaned_data.get("additional_branches") or []

            assign_employee_branches(
                user=employee,
                primary_office=primary,
                additional_offices=additional,
                actor=request.user,
            )

            # Update shift policy if fields were in form and actor is authorized
            update_user_fields = []
            if "shift_workflow_enabled" in form.cleaned_data:
                new_wf = form.cleaned_data["shift_workflow_enabled"]
                if old_shift_workflow_enabled != new_wf:
                    employee.shift_workflow_enabled = new_wf
                    update_user_fields.append("shift_workflow_enabled")
                    AuditEvent.objects.create(
                        actor=request.user,
                        category="POLICY",
                        description=f"Admin {request.user.username} changed shift_workflow_enabled for {employee.username} (id={employee.pk}) from {old_shift_workflow_enabled} to {new_wf}",
                    )

            if "shift_geofence_enforced" in form.cleaned_data:
                new_geo = form.cleaned_data["shift_geofence_enforced"]
                if old_geofence_enforced != new_geo:
                    employee.shift_geofence_enforced = new_geo
                    update_user_fields.append("shift_geofence_enforced")
                    AuditEvent.objects.create(
                        actor=request.user,
                        category="POLICY",
                        description=f"Admin {request.user.username} changed shift_geofence_enforced for {employee.username} (id={employee.pk}) from {old_geofence_enforced} to {new_geo}",
                    )

            if update_user_fields:
                employee.save(update_fields=update_user_fields)

            messages.success(request, f"Workplace assignments and policies updated for {employee.get_full_name() or employee.username}.")
            return redirect("web:employee-branch-assignments")
    else:
        initial_data = {
            "shift_workflow_enabled": employee.shift_workflow_enabled,
            "shift_geofence_enforced": employee.shift_geofence_enforced,
        }
        if current_primary:
            initial_data["primary_branch"] = current_primary.pk
        if current_additional:
            initial_data["additional_branches"] = [o.pk for o in current_additional]

        form = EmployeeBranchAssignmentForm(initial=initial_data, user=employee, actor=request.user)

    all_active_branches = OfficeLocation.objects.filter(is_active=True).order_by("name")

    context = {
        "employee": employee,
        "form": form,
        "current_primary": current_primary,
        "current_additional": current_additional,
        "all_active_branches": all_active_branches,
        "title": f"Assign Branches: {employee.get_full_name() or employee.username}",
    }
    return page(request, "employee_branch_assign_form", "branches", **context)
