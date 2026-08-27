"""Safe, deterministic export helpers for the Dashboard import workflow."""

from io import BytesIO
from decimal import Decimal

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment

from .currency import Currency
from .inventory_price_imports import EDITABLE_HEADERS, INFORMATIONAL_HEADERS
from .models import ProductVariant


def _literal(value):
    """Write human text as literal spreadsheet text, never as a formula."""
    value = "" if value is None else str(value)
    return "'" + value if value.startswith(("=", "+", "-", "@")) else value


def _workbook(*, language, include_current):
    workbook = Workbook()
    data = workbook.active
    data.title = "Data"
    headers = (*EDITABLE_HEADERS, *INFORMATIONAL_HEADERS)
    data.append(headers)
    for cell in data[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="1F2937")
        cell.alignment = Alignment(horizontal="center")
    data.freeze_panes = "A2"
    data.auto_filter.ref = f"A1:{chr(64 + len(headers))}1"
    widths = (20, 12, 22, 16, 20, 16, 20, 32, 28, 14, 14)
    for index, width in enumerate(widths, 1):
        data.column_dimensions[chr(64 + index)].width = width
    if include_current:
        variants = ProductVariant.objects.select_related("product").prefetch_related("prices").order_by("product__name", "pk", "sku")
        for variant in variants:
            prices = {price.currency: price for price in variant.prices.all()}
            irt, usd = prices.get(Currency.TOMAN), prices.get(Currency.USD)
            row = (
                variant.sku, variant.stock, variant.low_stock_threshold,
                format(irt.amount.quantize(Decimal("1")), ".2f") if irt else "",
                format(irt.compare_at_amount.quantize(Decimal("1")), ".2f") if irt and irt.compare_at_amount is not None else "",
                str(usd.amount.quantize(Decimal("0.01"))) if usd else "",
                str(usd.compare_at_amount.quantize(Decimal("0.01"))) if usd and usd.compare_at_amount is not None else "",
                variant.product.name, variant.sku, variant.price_unit, "active" if variant.is_active else "inactive",
            )
            data.append(row)
            # SKU is identity, so preserve it explicitly as literal text.
            sku_cell = data.cell(data.max_row, 1)
            sku_cell.value = _literal(variant.sku)
            sku_cell.number_format = "@"
            for column in range(8, 12):
                cell = data.cell(data.max_row, column)
                cell.value = _literal(cell.value)
                cell.alignment = Alignment(wrap_text=True, vertical="top")
    instructions = workbook.create_sheet("Instructions")
    lines = (
        ("راهنمای ورود گروهی موجودی و قیمت" if language == "fa" else "Bulk inventory and pricing import"),
        ("SKU فقط برای شناسایی است و نباید تغییر کند." if language == "fa" else "SKU is identity-only; do not change it."),
        ("خانه خالی یعنی بدون تغییر؛ مقدار موجودی صفر مجاز است." if language == "fa" else "Blank means unchanged; stock zero is allowed."),
        ("قیمت فروش صفر نامعتبر است؛ CLEAR فقط قیمت مرجع را پاک می‌کند." if language == "fa" else "Selling price zero is invalid; CLEAR only clears compare-at."),
        ("اعداد باید ASCII باشند؛ تومان صحیح و USD حداکثر دو رقم اعشار است." if language == "fa" else "Use ASCII numbers; Toman is whole and USD has at most two decimals."),
        ("حداکثر ۲۰۰۰ ردیف و ۵ MiB؛ همیشه ابتدا پیش‌نمایش را بررسی کنید." if language == "fa" else "Maximum 2,000 rows and 5 MiB; always review Preview before Apply."),
        ("تغییر هم‌زمان می‌تواند پیش‌نمایش را منسوخ کند." if language == "fa" else "Concurrent changes can make a Preview stale."),
    )
    for line in lines:
        instructions.append([line])
    instructions.column_dimensions["A"].width = 105
    for cell in instructions["A"]:
        cell.alignment = Alignment(wrap_text=True, vertical="top")
    return workbook


def build_inventory_price_xlsx(*, language, include_current):
    stream = BytesIO()
    _workbook(language=language, include_current=include_current).save(stream)
    return stream.getvalue()


def build_inventory_price_csv_template():
    return (",".join(EDITABLE_HEADERS) + "\r\n").encode("utf-8-sig")
