from django.contrib.auth.models import Group
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction

from dashboard.models import StaffAccessAuditEvent
from dashboard.role_definitions import ROLE_DEFINITIONS


MANAGED_ROLE_NAMES = tuple(ROLE_DEFINITIONS)


def managed_roles_for_user(user):
    return sorted(user.groups.filter(name__in=MANAGED_ROLE_NAMES).values_list("name", flat=True))


@transaction.atomic
def set_managed_dashboard_roles(*, actor, target_user, role_names):
    if not actor.is_superuser:
        raise PermissionDenied
    target = type(target_user).objects.select_for_update().get(pk=target_user.pk)
    if target.is_superuser:
        raise ValidationError("Superuser accounts cannot be changed through Staff & Access.")
    requested = set(role_names)
    unknown = requested - set(MANAGED_ROLE_NAMES)
    if unknown:
        raise ValidationError("One or more submitted roles are not managed Dashboard roles.")
    groups = {group.name: group for group in Group.objects.filter(name__in=MANAGED_ROLE_NAMES)}
    missing = set(MANAGED_ROLE_NAMES) - set(groups)
    if missing:
        raise ValidationError("Managed role setup is incomplete. Run seed_dashboard_roles.")
    before = managed_roles_for_user(target)
    after = sorted(requested)
    if before == after:
        return False, before, after
    target.groups.remove(*groups.values())
    target.groups.add(*(groups[name] for name in after))
    action = StaffAccessAuditEvent.Action.DASHBOARD_ACCESS_REVOKED if before and not after else StaffAccessAuditEvent.Action.ROLES_CHANGED
    StaffAccessAuditEvent.objects.create(actor=actor, target_user=target, actor_username=actor.username, actor_email=actor.email or "", target_username=target.username, target_email=target.email or "", action=action, roles_before=before, roles_after=after)
    return True, before, after
