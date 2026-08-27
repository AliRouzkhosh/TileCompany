from collections import Counter

from django.core.management.base import BaseCommand, CommandError

from products.models import Product
from products.publication_readiness import evaluate_publication_readiness


class Command(BaseCommand):
    help = "Audit catalog publication readiness without changing catalog state."

    def handle(self, *args, **options):
        batch = evaluate_publication_readiness()
        products = batch.product_results
        variants = batch.variant_results
        type_codes = set(Product.objects.filter(
            pk__in=[row.product_id for row in products], product_type__isnull=False,
        ).values_list("product_type__code", flat=True))
        active_invalid_products = [row for row in products if row.is_active and not row.ready]
        active_invalid_variants = [row for row in variants if row.is_active and not row.ready]
        draft_ready_products = [row for row in products if not row.is_active and row.ready]
        draft_ready_variants = [row for row in variants if not row.is_active and row.ready]
        draft_blocked_products = [row for row in products if not row.is_active and not row.ready]
        draft_blocked_variants = [row for row in variants if not row.is_active and not row.ready]
        warnings = sum(len(row.warnings) for row in products) + sum(len(row.warnings) for row in variants)
        codes = Counter(issue.code for row in (*products, *variants) for issue in row.issues)

        self.stdout.write("CATALOG PUBLICATION READINESS AUDIT")
        self.stdout.write(f"ProductTypes represented: {len(type_codes)}")
        self.stdout.write(f"Products evaluated: {len(products)}")
        self.stdout.write(f"Variants evaluated: {len(variants)}")
        self.stdout.write(f"ACTIVE INVALID Products: {len(active_invalid_products)}")
        self.stdout.write(f"ACTIVE INVALID Variants: {len(active_invalid_variants)}")
        self.stdout.write(f"DRAFT READY Products: {len(draft_ready_products)}")
        self.stdout.write(f"DRAFT READY Variants: {len(draft_ready_variants)}")
        self.stdout.write(f"DRAFT BLOCKED Products: {len(draft_blocked_products)}")
        self.stdout.write(f"DRAFT BLOCKED Variants: {len(draft_blocked_variants)}")
        self.stdout.write(f"Warnings: {warnings}")
        for code, count in sorted(codes.items()):
            self.stdout.write(f"  {code}: {count}")
        for row in (*active_invalid_products, *active_invalid_variants):
            for issue in row.blockers:
                self.stdout.write(self.style.ERROR(f"[BLOCK] {row.sku}: {issue.code}: {issue.message}"))
        if active_invalid_products or active_invalid_variants:
            raise CommandError("ACTIVE catalog rows fail publication readiness.")
        self.stdout.write(self.style.SUCCESS("PUBLICATION READINESS AUDIT USABLE: no active invalid catalog rows."))
