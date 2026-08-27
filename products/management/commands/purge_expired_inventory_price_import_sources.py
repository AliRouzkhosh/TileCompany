from django.core.management.base import BaseCommand
from django.utils import timezone

from products.models import InventoryPriceImportBatch


class Command(BaseCommand):
    help = "Purge expired private source files while retaining import and audit records."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        found = deleted = missing = errors = 0
        for batch in InventoryPriceImportBatch.objects.exclude(source_file="").filter(source_expires_at__lte=timezone.now()):
            found += 1
            storage, name = batch.source_file.storage, batch.source_file.name
            if not storage.exists(name):
                missing += 1
                if not dry_run:
                    batch.source_file = ""
                    batch.save(update_fields=["source_file"])
                continue
            if dry_run:
                continue
            try:
                storage.delete(name)
            except Exception as error:  # storage failures must retain evidence
                errors += 1
                self.stderr.write(f"Batch {batch.pk}: {error}")
                continue
            batch.source_file = ""
            batch.save(update_fields=["source_file"])
            deleted += 1
        self.stdout.write(f"found={found} deleted={deleted} missing={missing} errors={errors} dry_run={dry_run}")
