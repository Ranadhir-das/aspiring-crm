from rest_framework.permissions import BasePermission

from apps.accounts.models import User


class CanManageLeads(BasePermission):
    """
    Allows Super Admin, Admin and Manager to manage leads.
    """

    allowed_roles = {
        User.Role.SUPER_ADMIN,
        User.Role.ADMIN,
        User.Role.MANAGER,
    }

    def has_permission(self, request, view):
        from apps.accounts.capabilities import is_management_user
        return is_management_user(request.user)


class CanAccessLeads(BasePermission):
    allowed_roles = {
        User.Role.SUPER_ADMIN,
        User.Role.ADMIN,
        User.Role.MANAGER,
        User.Role.CALLER,
    }

    def has_permission(self, request, view):
        from apps.accounts.capabilities import can_access_calling_workspace
        return can_access_calling_workspace(request.user)