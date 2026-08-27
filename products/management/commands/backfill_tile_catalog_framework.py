from django.core.management.base import BaseCommand, CommandError

from products.catalog_backfill import (
    BLOCKING,
    WARNING,
    TileReconciliationBlocked,
    apply_tile_catalog,
    inspect_tile_catalog,
)


class Command(BaseCommand):
    help = "Preflight or transactionally apply the deterministic Tile legacy catalog backfill."

    def add_arguments(self, parser):
        mode = parser.add_mutually_exclusive_group()
        mode.add_argument(
            "--dry-run",
            action="store_true",
            help="Inspect and report only (the default).",
        )
        mode.add_argument(
            "--apply",
            action="store_true",
            help="Apply the backfill transactionally when preflight has no blocking issues.",
        )

    def handle(self, *args, **options):
        report = inspect_tile_catalog()
        self._write_report("PRE-APPLY RECONCILIATION", report)
        if report.blocking_issues:
            raise CommandError(
                f"Apply prohibited: {len(report.blocking_issues)} BLOCKING issue(s) found."
            )

        if not options["apply"]:
            self.stdout.write(self.style.SUCCESS("DRY RUN COMPLETE: zero writes performed; apply is permitted."))
            return

        try:
            postflight = apply_tile_catalog()
        except TileReconciliationBlocked as exc:
            self._write_report("ROLLED-BACK RECONCILIATION", exc.report)
            raise CommandError("F1B.2 apply rolled back because reconciliation failed.")
        self._write_report("POST-APPLY RECONCILIATION", postflight)
        self.stdout.write(self.style.SUCCESS("APPLY COMPLETE: Tile generic parity established transactionally."))

    def _write_report(self, title, report):
        self.stdout.write(f"\n{title}")
        self.stdout.write("Legacy source counts:")
        for key, value in report.legacy_counts.items():
            self.stdout.write(f"  {key}: {value}")
        self.stdout.write("Expected generic counts:")
        for key, value in report.expected_counts.items():
            self.stdout.write(f"  {key}: {value}")
        self.stdout.write("Current generic counts:")
        for key, value in report.generic_counts.items():
            self.stdout.write(f"  {key}: {value}")

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
