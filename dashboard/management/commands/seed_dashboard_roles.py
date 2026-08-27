from django.contrib.auth.models import Group, Permission
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from dashboard.role_definitions import ROLE_DEFINITIONS


class Command(BaseCommand):
    help = "Create and synchronize the project-managed Dashboard role Groups."

    def handle(self, *args, **options):
        with transaction.atomic():
            for group_name, permission_names in ROLE_DEFINITIONS.items():
                permissions = self._resolve_permissions(group_name, permission_names)
                group, created = Group.objects.get_or_create(name=group_name)
                group.permissions.set(permissions)
                state = "created" if created else "synchronized"
                self.stdout.write(f"{group_name}: {state} ({len(permissions)} permissions)")

    @staticmethod
    def _resolve_permissions(group_name, permission_names):
        permissions = list(
            Permission.objects.filter(
                content_type__app_label__in={name.split(".", 1)[0] for name in permission_names},
            ).select_related("content_type")
        )
        by_name = {
            f"{permission.content_type.app_label}.{permission.codename}": permission
            for permission in permissions
        }
        missing = sorted(set(permission_names) - set(by_name))
        if missing:
            raise CommandError(
                f"{group_name} cannot be synchronized; missing permissions: {', '.join(missing)}. "
                "Run migrate before seeding roles."
            )
        return [by_name[name] for name in sorted(permission_names)]
