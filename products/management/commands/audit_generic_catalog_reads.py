from django.core.management.base import BaseCommand, CommandError

from products.catalog_read_audit import run_generic_catalog_read_audit


class Command(BaseCommand):
    help = "Audit generic catalog shadow reads against authoritative Tile legacy values."

    def handle(self, *args, **options):
        report = run_generic_catalog_read_audit()
        self.stdout.write("GENERIC CATALOG READ AUDIT")
        rows = (
            ("Products examined", report.products_examined),
            ("Variants examined", report.variants_examined),
            ("Product attribute parity failures", report.product_attribute_parity_failures),
            ("Variant attribute parity failures", report.variant_attribute_parity_failures),
            ("Signature consistency failures", report.signature_consistency_failures),
            ("Missing generic bindings/values", report.missing_generic_bindings_values),
            ("Unexpected generic values", report.unexpected_generic_values),
            ("Retired/disallowed stored values", report.retired_disallowed_stored_values),
            ("Known operational Size warnings", report.known_operational_size_warnings),
        )
        for label, value in rows:
            self.stdout.write(f"{label}: {value}")
        for issue in report.issues:
            line = f"[{issue.level}] {issue.code}: {issue.message}"
            if issue.level == "BLOCKING":
                self.stdout.write(self.style.ERROR(line))
            else:
                self.stdout.write(self.style.WARNING(line))
        if report.blocking_issues:
            raise CommandError(
                f"Generic catalog reads are BLOCKED by {len(report.blocking_issues)} issue(s)."
            )
        self.stdout.write(
            self.style.SUCCESS(
                "GENERIC READS USABLE: zero attribute/signature parity failures."
            )
        )
