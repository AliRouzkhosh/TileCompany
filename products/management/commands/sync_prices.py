from decimal import Decimal, InvalidOperation

import openpyxl
from django.core.management.base import BaseCommand, CommandError

from products.currency import Currency
from products.models import ProductVariant
from products.services import set_variant_price


class Command(BaseCommand):
    help = "Sync one explicit currency price by variant SKU. Required columns: SKU, Amount; optional Compare At Amount."

    def add_arguments(self, parser):
        parser.add_argument("filepath", type=str)
        parser.add_argument("--currency", required=True, choices=Currency.values)
        parser.add_argument("--sheet", default=None)

    def handle(self, *args, **options):
        try:
            workbook = openpyxl.load_workbook(options["filepath"], data_only=True)
        except FileNotFoundError as error:
            raise CommandError("File not found.") from error
        except Exception as error:
            raise CommandError(f"Could not open workbook: {error}") from error

        worksheet = workbook[options["sheet"]] if options["sheet"] else workbook.active
        headers = [str(cell.value).strip().lower() if cell.value else "" for cell in worksheet[1]]
        try:
            sku_column = headers.index("sku")
            amount_column = headers.index("amount")
        except ValueError as error:
            raise CommandError("The sheet must include SKU and Amount columns.") from error
        compare_column = headers.index("compare at amount") if "compare at amount" in headers else None

        updated = skipped = 0
        missing = []
        for row in worksheet.iter_rows(min_row=2):
            if not row[sku_column].value:
                continue
            sku = str(row[sku_column].value).strip().upper()
            try:
                amount = Decimal(str(row[amount_column].value))
                compare_at_amount = (
                    Decimal(str(row[compare_column].value))
                    if compare_column is not None and row[compare_column].value not in (None, "")
                    else None
                )
            except (InvalidOperation, TypeError):
                self.stdout.write(self.style.WARNING(f"Skipping {sku}: invalid monetary value."))
                skipped += 1
                continue
            try:
                variant = ProductVariant.objects.get(sku=sku)
            except ProductVariant.DoesNotExist:
                missing.append(sku)
                continue
            set_variant_price(
                variant=variant,
                currency=options["currency"],
                amount=amount,
                compare_at_amount=compare_at_amount,
            )
            updated += 1

        self.stdout.write(self.style.SUCCESS(f"Updated {updated} {options['currency']} variant price(s)."))
        if skipped:
            self.stdout.write(self.style.WARNING(f"Skipped {skipped} row(s)."))
        if missing:
            self.stdout.write(self.style.WARNING(f"Missing SKU(s): {', '.join(missing[:20])}"))
