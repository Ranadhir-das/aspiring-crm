from rest_framework.permissions import BasePermission

from apps.accounts.models import User


class CanAccessFollowUps(BasePermission):
    allowed_roles = {
        User.Role.SUPER_ADMIN,
        User.Role.ADMIN,
        User.Role.MANAGER,
        User.Role.CALLER,
    }

    def has_permission(self, request, view):
        user = request.user
        if not user or not user.is_authenticated:
            return False
        if user.role in self.allowed_roles:
            return True
        from apps.accounts.capabilities import is_caller_capable
        return is_caller_capable(user)
