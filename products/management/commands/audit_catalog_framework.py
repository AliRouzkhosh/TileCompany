from django.core.management.base import BaseCommand, CommandError

from products.catalog_framework_audit import (
    BLOCKING,
    WARNING,
    run_catalog_framework_audit,
)


class Command(BaseCommand):
    help = "Read-only integrity audit for every generic catalog ProductType and schema."

    def handle(self, *args, **options):
        report = run_catalog_framework_audit()
        self.stdout.write("GLOBAL CATALOG FRAMEWORK AUDIT")
        self.stdout.write(f"ProductTypes examined: {report.product_types_examined}")
        self.stdout.write(f"Definitions examined: {report.definitions_examined}")
        self.stdout.write(f"Bindings examined: {report.bindings_examined}")
        self.stdout.write(f"Products examined: {report.products_examined}")
        self.stdout.write(f"Variants examined: {report.variants_examined}")
        for issue in report.issues:
            line = f"[{issue.level}] {issue.code}: {issue.message}"
            if issue.level == BLOCKING:
                self.stdout.write(self.style.ERROR(line))
            elif issue.level == WARNING:
                self.stdout.write(self.style.WARNING(line))
            else:
                self.stdout.write(line)
        self.stdout.write(
            f"Integrity summary: {len(report.blocking_issues)} BLOCKING, "
            f"{len(report.warning_issues)} WARNING."
        )
        if report.blocking_issues:
            raise CommandError("Generic catalog framework integrity is not usable.")
        self.stdout.write(self.style.SUCCESS("CATALOG FRAMEWORK USABLE: zero blocking issues."))

