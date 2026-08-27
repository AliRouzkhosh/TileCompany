from datetime import timedelta
from django.core.management.base import BaseCommand
from django.db.models import Q
from django.utils import timezone
from accounts.models import OtpChallenge


class Command(BaseCommand):
    help = "Purge terminal or expired OTP challenges older than seven days."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        cutoff = timezone.now() - timedelta(days=7)
        qs = OtpChallenge.objects.filter(created_at__lt=cutoff).filter(Q(is_active=False) | Q(expires_at__lt=timezone.now()))
        count = qs.count()
        if not options["dry_run"]:
            qs.delete()
        self.stdout.write(f"{'Would purge' if options['dry_run'] else 'Purged'} {count} OTP challenges.")
