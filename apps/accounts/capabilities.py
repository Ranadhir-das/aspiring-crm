"""Centralized capability helpers for dual-role employees (Caller & Counselor).

Allows a single employee account to have approved Caller and Counselor capabilities
simultaneously, while preserving the legacy User.role scalar and existing single-role
workflows.
"""
from django.db.models import Q

FACE_VERIFICATION_EXEMPT_ROLES = {'ADMIN', 'SUPER_ADMIN', 'COUNSELOR'}


def is_caller_capable(user):
    """Check if a user is authorized for Caller workflows (assigned leads, calls, dialer, follow-ups).

    True for:
    - Active users with primary role CALLER.
    - Active users with approved can_act_as_caller capability.
    False for:
    - Inactive users, registration-pending users, anonymous users, or users without caller capability.
    """
    if not user or not getattr(user, 'is_authenticated', False) or not getattr(user, 'is_active', False):
        return False
    if getattr(user, 'registration_pending', False):
        return False
    return getattr(user, 'role', None) == 'CALLER' or bool(getattr(user, 'can_act_as_caller', False))


def is_counselor_capable(user):
    """Check if a user is authorized for Counselor workflows (counselor workspace, notes, admission requests).

    True for:
    - Active users with primary role COUNSELOR.
    - Active users with approved can_act_as_counselor capability.
    False for:
    - Inactive users, registration-pending users, anonymous users, or users without counselor capability.
    """
    if not user or not getattr(user, 'is_authenticated', False) or not getattr(user, 'is_active', False):
        return False
    if getattr(user, 'registration_pending', False):
        return False
    return getattr(user, 'role', None) == 'COUNSELOR' or bool(getattr(user, 'can_act_as_counselor', False))


def is_dual_role(user):
    """Check if an employee possesses both Caller and Counselor capabilities."""
    return is_caller_capable(user) and is_counselor_capable(user)


def is_face_verification_exempt(user):
    """Check if an employee is exempt from daily face verification at login.

    Business Rule 2: Dual-role employees must complete daily face verification, like callers.
    Therefore, ANY user with Caller capability (primary role CALLER or approved can_act_as_caller)
    MUST complete daily face verification.
    Only pure Counselors, Admins, and Super Admins (without caller capability) are exempt.
    """
    if not user or not getattr(user, 'is_authenticated', False):
        return False
    if is_caller_capable(user):
        return False
    return getattr(user, 'role', None) in FACE_VERIFICATION_EXEMPT_ROLES


def eligible_callers_qs():
    """Return a queryset of active, approved users who have Caller capability."""
    from apps.accounts.models import User
    return User.objects.filter(
        Q(role=User.Role.CALLER) | Q(can_act_as_caller=True),
        is_active=True,
        registration_pending=False,
    )


def eligible_counselors_qs():
    """Return a queryset of active, approved users who have Counselor capability."""
    from apps.accounts.models import User
    return User.objects.filter(
        Q(role=User.Role.COUNSELOR) | Q(can_act_as_counselor=True),
        is_active=True,
        registration_pending=False,
    )
