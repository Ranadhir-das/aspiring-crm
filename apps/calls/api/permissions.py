from rest_framework.permissions import BasePermission

from apps.accounts.models import User


class CanCreateCall(BasePermission):
    allowed_roles = {
        User.Role.SUPER_ADMIN,
        User.Role.ADMIN,
        User.Role.MANAGER,
        User.Role.CALLER,
    }

    def has_permission(self, request, view):
        return (
            request.user
            and request.user.is_authenticated
            and request.user.role in self.allowed_roles
        )