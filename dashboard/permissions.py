############### dashboard/permissions.py ################

"""Dashboard authorization primitives built on Django auth permissions."""

from functools import wraps

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied

from dashboard.role_definitions import DASHBOARD_CAPABILITY_PERMISSIONS


def user_can_access_dashboard(user):
    """Return whether a user has a maintained Dashboard capability."""
    if not getattr(user, "is_authenticated", False):
        return False
    if user.is_superuser:
        return True
    return any(user.has_perm(permission) for permission in DASHBOARD_CAPABILITY_PERMISSIONS)


def is_owner(user):
    return bool(getattr(user, "is_authenticated", False) and user.is_superuser)


def dashboard_permission_required(*permissions, any_of=False):
    """Require permissions, redirecting anonymous users and 403ing denials."""
    if not permissions:
        raise ValueError("At least one permission is required.")

    def decorator(view_func):
        @login_required
        @wraps(view_func)
        def wrapper(request, *args, **kwargs):
            allowed = (
                any(request.user.has_perm(permission) for permission in permissions)
                if any_of
                else request.user.has_perms(permissions)
            )
            if not allowed:
                raise PermissionDenied
            return view_func(request, *args, **kwargs)
        return wrapper
    return decorator


def dashboard_access_required(view_func):
    """Admit a user to the safe Dashboard entry page by capability."""
    @login_required
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if not user_can_access_dashboard(request.user):
            raise PermissionDenied
        return view_func(request, *args, **kwargs)
    return wrapper


def owner_required(view_func):
    """Require the Django Owner contract: an authenticated superuser."""
    @login_required
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_superuser:
            raise PermissionDenied
        return view_func(request, *args, **kwargs)
    return wrapper
