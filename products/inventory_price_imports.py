"""Parsing and authoritative staging for inventory/pricing import previews.

This module intentionally has no Apply operation.  It never writes ProductVariant
inventory fields or VariantPrice rows; it only creates import batch/row records.
"""

import csv
import hashlib
import io
import re
import zipfile
from collections import Counter, defaultdict
from datetime import timedelta
from decimal import Decimal, InvalidOperation
from pathlib import PurePosixPath

from django.core.exceptions import PermissionDenied
from django.core.files.base import ContentFile
from django.db import transaction
from django.db.models.functions import Trim, Upper
from django.utils import timezone
from django.utils.translation import gettext as _
from openpyxl import load_workbook
from openpyxl.utils.exceptions import InvalidFileException

from .currency import Currency
from .models import InventoryPriceImportBatch, InventoryPriceImportRow, ProductOperationalChange, ProductVariant


PARSER_VERSION = "1"
MAX_SOURCE_BYTES = 5 * 1024 * 1024
MAX_DATA_ROWS = 2000
MAX_COLUMNS = 20
MAX_XLSX_ARCHIVE_MEMBERS = 100
MAX_XLSX_UNCOMPRESSED_BYTES = 25 * 1024 * 1024
EDITABLE_HEADERS = (
    "sku", "stock", "low_stock_threshold", "irt_price", "irt_compare_at",
    "usd_price", "usd_compare_at",
)
INFORMATIONAL_HEADERS = ("product_name", "variant_label", "price_unit", "active_status")
ALLOWED_HEADERS = frozenset((*EDITABLE_HEADERS, *INFORMATIONAL_HEADERS))
INTEGER_FIELDS = frozenset(("stock", "low_stock_threshold"))
PRICE_FIELDS = frozenset(("irt_price", "irt_compare_at", "usd_price", "usd_compare_at"))
COMPARE_FIELDS = frozenset(("irt_compare_at", "usd_compare_at"))
ASCII_INTEGER = re.compile(r"^\+?[0-9]+$")
ASCII_DECIMAL = re.compile(r"^\+?[0-9]+(?:\.[0-9]{1,2})?$")


class ImportSourceError(Exception):
    def __init__(self, code, message):
        self.code = code
        self.message = str(message)
        super().__init__(self.message)


def _issue(code, message, field=None):
    value = {"code": code, "message": str(message)}
    if field:
        value["field"] = field
    return value


def _safe_filename(name):
    return PurePosixPath((name or "upload").replace("\\", "/")).name[:255] or "upload"


def _source_format(filename, payload):
    suffix = PurePosixPath(filename.lower()).suffix
    is_zip = zipfile.is_zipfile(io.BytesIO(payload))
    if suffix == ".csv":
        if is_zip:
            raise ImportSourceError("unsupported_format", _("A CSV upload must be text, not a workbook."))
        return InventoryPriceImportBatch.SourceFormat.CSV
    if suffix == ".xlsx":
        if not is_zip:
            raise ImportSourceError("unsupported_format", _("The XLSX upload is not a valid workbook."))
        return InventoryPriceImportBatch.SourceFormat.XLSX
    raise ImportSourceError("unsupported_format", _("Only CSV and XLSX files are supported."))


def _validate_headers(headers):
    if not headers:
        raise ImportSourceError("invalid_headers", _("The file must contain a header row."))
    normalized = ["" if value is None else str(value) for value in headers]
    duplicates = {header for header, count in Counter(normalized).items() if count > 1}
    if duplicates:
        raise ImportSourceError("invalid_headers", _("The file contains duplicate headers."))
    if any(header not in ALLOWED_HEADERS for header in normalized):
        raise ImportSourceError("invalid_headers", _("The file contains unsupported headers."))
    if any(header not in normalized for header in EDITABLE_HEADERS):
        raise ImportSourceError("invalid_headers", _("The file is missing one or more required headers."))
    return normalized


def _parse_csv(payload):
    try:
        text = payload.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise ImportSourceError("malformed_csv", _("CSV must be UTF-8 encoded.")) from error
    try:
        rows = list(csv.reader(io.StringIO(text, newline=""), delimiter=",", strict=True))
    except csv.Error as error:
        raise ImportSourceError("malformed_csv", _("The CSV file is malformed.")) from error
    if not rows:
        raise ImportSourceError("invalid_headers", _("The file must contain a header row."))
    headers = _validate_headers(rows[0])
    data = rows[1:]
    if len(data) > MAX_DATA_ROWS:
        raise ImportSourceError("too_many_rows", _("The file exceeds the maximum of %(limit)s data rows.") % {"limit": MAX_DATA_ROWS})
    if any(len(row) > MAX_COLUMNS for row in rows):
        raise ImportSourceError("too_many_columns", _("The file exceeds the maximum number of columns."))
    return _rows_from_values(headers, data, first_row_number=2)


def _parse_xlsx(payload):
    _validate_xlsx_container(payload)
    try:
        workbook = load_workbook(io.BytesIO(payload), read_only=True, data_only=False, keep_links=True)
    except (InvalidFileException, OSError, ValueError, KeyError, zipfile.BadZipFile) as error:
        raise ImportSourceError("malformed_xlsx", _("The XLSX file is malformed.")) from error
    sheet_names = workbook.sheetnames
    if "Data" not in sheet_names:
        raise ImportSourceError("missing_sheet", _("The workbook must contain a visible Data sheet."))
    if len(sheet_names) > 2 or any(name not in {"Data", "Instructions"} for name in sheet_names):
        raise ImportSourceError("unexpected_sheet", _("The workbook contains unsupported sheets."))
    sheet = workbook["Data"]
    if sheet.sheet_state != "visible":
        raise ImportSourceError("missing_sheet", _("The Data sheet must be visible."))
    if getattr(workbook, "_external_links", ()):
        raise ImportSourceError("external_links", _("External workbook links are not allowed."))
    if sheet.max_column > MAX_COLUMNS or sheet.max_row > MAX_DATA_ROWS + 1:
        raise ImportSourceError("too_many_rows", _("The workbook exceeds the supported size."))
    rows = []
    for row_number, cells in enumerate(sheet.iter_rows(), start=1):
        if row_number > MAX_DATA_ROWS + 1:
            raise ImportSourceError("too_many_rows", _("The workbook exceeds the maximum data row limit."))
        if len(cells) > MAX_COLUMNS:
            raise ImportSourceError("too_many_columns", _("The workbook exceeds the maximum number of columns."))
        if any(cell.data_type == "f" for cell in cells):
            raise ImportSourceError("formula_not_allowed", _("Formulas are not allowed in the Data sheet."))
        rows.append([cell.value for cell in cells])
    if not rows:
        raise ImportSourceError("invalid_headers", _("The workbook must contain a header row."))
    headers = _validate_headers(rows[0])
    return _rows_from_values(headers, rows[1:], first_row_number=2)


def _validate_xlsx_container(payload):
    """Bound the ZIP container before handing it to the workbook parser."""
    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            members = archive.infolist()
            if len(members) > MAX_XLSX_ARCHIVE_MEMBERS:
                raise ImportSourceError("xlsx_archive_size", _("The XLSX archive contains too many files."))
            if sum(member.file_size for member in members) > MAX_XLSX_UNCOMPRESSED_BYTES:
                raise ImportSourceError("xlsx_archive_size", _("The XLSX archive expands beyond the supported size."))
            for member in members:
                path = PurePosixPath(member.filename)
                if path.is_absolute() or ".." in path.parts:
                    raise ImportSourceError("malformed_xlsx", _("The XLSX archive contains an invalid path."))
    except ImportSourceError:
        raise
    except (OSError, ValueError, zipfile.BadZipFile) as error:
        raise ImportSourceError("malformed_xlsx", _("The XLSX file is malformed.")) from error


def _rows_from_values(headers, raw_rows, *, first_row_number):
    records = []
    for offset, values in enumerate(raw_rows):
        values = list(values)
        if len(values) > len(headers) and any(value not in (None, "") for value in values[len(headers):]):
            raise ImportSourceError("too_many_columns", _("A row contains data outside the supported columns."))
        padded = values + [None] * max(0, len(headers) - len(values))
        if not any(value not in (None, "") for value in padded[:len(headers)]):
            continue
        records.append((first_row_number + offset, dict(zip(headers, padded))))
    return records


def _text(value):
    text = "" if value is None else str(value).strip()
    # XLSX exports quote formula-looking literal text for spreadsheet safety.  The
    # quote is display protection, not part of the SKU identity on re-upload.
    return text[1:] if len(text) > 1 and text[0] == "'" and text[1] in "=+-@" else text


def _parse_integer(value, field, errors):
    text = _text(value)
    if not text:
        return None
    if text.upper() == "CLEAR":
        errors.append(_issue("clear_not_allowed", _("CLEAR is not valid for this field."), field))
        return None
    if not ASCII_INTEGER.fullmatch(text):
        errors.append(_issue("invalid_integer", _("Enter a non-negative integer using ASCII digits."), field))
        return None
    number = int(text)
    return number


def _parse_price(value, field, errors):
    text = _text(value)
    if not text:
        return None
    if field in COMPARE_FIELDS and text.upper() == "CLEAR":
        return "__CLEAR__"
    if text.upper() == "CLEAR":
        errors.append(_issue("clear_not_allowed", _("CLEAR is only valid for compare-at prices."), field))
        return None
    if not ASCII_DECIMAL.fullmatch(text):
        errors.append(_issue("invalid_price", _("Enter a positive price using ASCII numeric notation."), field))
        return None
    try:
        number = Decimal(text)
    except InvalidOperation:
        errors.append(_issue("invalid_price", _("Enter a valid price."), field))
        return None
    if number <= 0:
        errors.append(_issue("price_zero", _("Selling and compare-at prices must be greater than zero."), field))
        return None
    if field.startswith("irt_") and number != number.to_integral_value():
        errors.append(_issue("irt_fractional", _("Toman prices must be whole Toman amounts."), field))
        return None
    return number


def _price_snapshot(price):
    def decimal_string(value):
        return format(Decimal(value).quantize(Decimal("0.01")), "f")
    return {
        "exists": price is not None,
        "amount": decimal_string(price.amount) if price is not None else None,
        "compare_at_amount": decimal_string(price.compare_at_amount) if price and price.compare_at_amount is not None else None,
    }


def _snapshot(variant):
    prices = {price.currency: price for price in variant.prices.all()}
    return {
        "variant_id": variant.pk,
        "sku": variant.sku,
        "stock": variant.stock,
        "low_stock_threshold": variant.low_stock_threshold,
        "IRT": _price_snapshot(prices.get(Currency.TOMAN)),
        "USD": _price_snapshot(prices.get(Currency.USD)),
    }


snapshot_variant_inventory_prices = _snapshot


def operational_changed_fields(before, after):
    fields = [field for field in ("stock", "low_stock_threshold") if before.get(field) != after.get(field)]
    for currency in ("IRT", "USD"):
        for key in ("amount", "compare_at_amount"):
            if before[currency].get(key) != after[currency].get(key):
                fields.append(f"{currency}.{key}")
    return fields


def _audit(actor, variant, *, source, before, after, changed_fields, batch=None):
    if not changed_fields:
        return None
    return ProductOperationalChange.objects.create(
        actor=actor, actor_username=actor.get_username(), actor_email=actor.email or "",
        variant=variant, sku_snapshot=variant.sku, product_name_snapshot=variant.product.name,
        source=source, import_batch=batch, changed_fields=changed_fields,
        before_snapshot=before, after_snapshot=after,
    )


def record_product_operational_change(*, actor, variant, source, before, after, batch=None):
    return _audit(actor, variant, source=source, before=before, after=after,
                  changed_fields=operational_changed_fields(before, after), batch=batch)


def _stale_fields(row, current):
    before = row.before_snapshot
    stale = []
    for field in row.changed_fields:
        if field in ("stock", "low_stock_threshold"):
            if current.get(field) != before.get(field):
                stale.append({"sku": row.sku_snapshot, "field": field, "expected": before.get(field), "current": current.get(field)})
        elif field.startswith(("IRT.", "USD.")):
            currency = field.split(".")[0]
            # Both price components are dependencies for a valid price operation.
            for key in ("exists", "amount", "compare_at_amount"):
                if current[currency].get(key) != before[currency].get(key):
                    stale.append({"sku": row.sku_snapshot, "field": f"{currency}.{key}", "expected": before[currency].get(key), "current": current[currency].get(key)})
    return stale


def _validate_apply_row(row):
    allowed_patch = {*INTEGER_FIELDS, *PRICE_FIELDS}
    allowed_changed = {"stock", "low_stock_threshold", "IRT.amount", "IRT.compare_at_amount", "USD.amount", "USD.compare_at_amount"}
    if row.row_status != InventoryPriceImportRow.Status.CHANGED or not row.variant_id:
        raise ValueError("Invalid staged import row.")
    if set(row.normalized_patch) - allowed_patch or set(row.changed_fields) - allowed_changed:
        raise ValueError("Invalid staged import data.")
    expected_changes = operational_changed_fields(row.before_snapshot, row.proposed_after_snapshot)
    if row.changed_fields != expected_changes:
        raise ValueError("Staged import fields do not match its canonical snapshots.")
    for field in INTEGER_FIELDS:
        if field in row.changed_fields:
            value = row.proposed_after_snapshot.get(field)
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise ValueError("Invalid staged import inventory value.")
    patch_to_change = {
        "stock": "stock", "low_stock_threshold": "low_stock_threshold",
        "irt_price": "IRT.amount", "irt_compare_at": "IRT.compare_at_amount",
        "usd_price": "USD.amount", "usd_compare_at": "USD.compare_at_amount",
    }
    for patch_key, changed_key in patch_to_change.items():
        if changed_key in row.changed_fields and patch_key not in row.normalized_patch:
            raise ValueError("Invalid staged import data.")
    # Reuse the preview constructor's canonical relation validation by checking after snapshot.
    for currency in ("IRT", "USD"):
        price = row.proposed_after_snapshot.get(currency, {})
        if price.get("amount") is not None and Decimal(price["amount"]) <= 0:
            raise ValueError("Invalid staged import pricing.")
        if price.get("compare_at_amount") is not None and (
            price.get("amount") is None or Decimal(price["compare_at_amount"]) <= Decimal(price["amount"])
        ):
            raise ValueError("Invalid staged import pricing.")


def apply_inventory_price_import(*, actor, batch_id):
    """Apply one validated staged batch; no caller supplied commerce values are accepted."""
    if not actor.has_perm("products.apply_inventory_price_import"):
        raise PermissionDenied
    try:
        return _apply_inventory_price_import(actor=actor, batch_id=batch_id)
    except Exception:
        # The commerce transaction has rolled back.  Persist a safe terminal state
        # independently so an operator cannot mistake a failed apply for success.
        with transaction.atomic():
            batch = InventoryPriceImportBatch.objects.select_for_update().get(pk=batch_id)
            if batch.status in (InventoryPriceImportBatch.Status.VALIDATED, InventoryPriceImportBatch.Status.APPLYING):
                batch.status = InventoryPriceImportBatch.Status.FAILED
                batch.failure_summary = {"error": [{"code": "apply_failed", "message": "The import could not be applied safely."}]}
                batch.save(update_fields=["status", "failure_summary"])
        raise


@transaction.atomic
def _apply_inventory_price_import(*, actor, batch_id):
    batch = InventoryPriceImportBatch.objects.select_for_update().get(pk=batch_id)
    if batch.status == InventoryPriceImportBatch.Status.APPLIED:
        return {"batch_id": batch.pk, "status": batch.status, "already_applied": True, "changed_variants": 0}
    if batch.status != InventoryPriceImportBatch.Status.VALIDATED:
        raise ValueError("This import batch cannot be applied.")
    if batch.changed_rows == 0:
        return {"batch_id": batch.pk, "status": batch.status, "already_applied": False, "changed_variants": 0, "no_changes": True}
    rows = list(batch.rows.select_related("variant__product").filter(row_status=InventoryPriceImportRow.Status.CHANGED).order_by("variant_id", "row_number"))
    if len(rows) != batch.changed_rows:
        raise ValueError("Import batch staging is inconsistent.")
    for row in rows:
        _validate_apply_row(row)
    variants = {variant.pk: variant for variant in ProductVariant.objects.select_for_update().select_related("product").prefetch_related("prices").filter(pk__in=[row.variant_id for row in rows]).order_by("pk")}
    if len(variants) != len({row.variant_id for row in rows}):
        raise ValueError("A staged Variant no longer exists.")
    stale = []
    for row in rows:
        stale.extend(_stale_fields(row, _snapshot(variants[row.variant_id])))
    if stale:
        batch.status = InventoryPriceImportBatch.Status.STALE
        batch.failure_summary = {"stale": stale}
        batch.save(update_fields=["status", "failure_summary"])
        return {"batch_id": batch.pk, "status": batch.status, "already_applied": False, "stale": stale, "changed_variants": 0}

    from .services import set_variant_price
    batch.status = InventoryPriceImportBatch.Status.APPLYING
    batch.save(update_fields=["status"])
    for row in rows:
        variant = variants[row.variant_id]
        before = _snapshot(variant)
        inventory_fields = [field for field in ("stock", "low_stock_threshold") if field in row.changed_fields]
        for field in inventory_fields:
            setattr(variant, field, row.proposed_after_snapshot[field])
        if inventory_fields:
            variant.full_clean()
            variant.save(update_fields=inventory_fields)
        for currency in ("IRT", "USD"):
            price_fields = {field for field in row.changed_fields if field.startswith(f"{currency}.")}
            if not price_fields:
                continue
            proposed = row.proposed_after_snapshot[currency]
            set_variant_price(variant=variant, currency=currency, amount=Decimal(proposed["amount"]), compare_at_amount=Decimal(proposed["compare_at_amount"]) if proposed["compare_at_amount"] is not None else None)
        variant = ProductVariant.objects.select_related("product").prefetch_related("prices").get(pk=variant.pk)
        after = _snapshot(variant)
        _audit(actor, variant, source=ProductOperationalChange.Source.IMPORT, batch=batch, before=before, after=after, changed_fields=row.changed_fields)
    batch.status = InventoryPriceImportBatch.Status.APPLIED
    batch.applied_at = timezone.now(); batch.applied_by = actor
    batch.save(update_fields=["status", "applied_at", "applied_by"])
    return {"batch_id": batch.pk, "status": batch.status, "already_applied": False, "changed_variants": len(rows)}


def _serialize_patch(patch):
    result = {}
    for key, value in patch.items():
        result[key] = str(value) if isinstance(value, Decimal) else value
    return result


def _row_for(batch, row_number, values, matches, duplicates):
    errors, warnings, patch = [], [], {}
    normalized_sku = _text(values.get("sku")).upper()
    if not normalized_sku:
        errors.append(_issue("blank_sku", _("SKU is required."), "sku"))
    elif normalized_sku in duplicates:
        errors.append(_issue("duplicate_sku", _("This SKU appears more than once in the upload."), "sku"))
    candidates = matches.get(normalized_sku, [])
    variant = None
    if normalized_sku and not candidates:
        errors.append(_issue("unknown_sku", _("No Variant matches this SKU."), "sku"))
    elif len(candidates) > 1:
        errors.append(_issue("ambiguous_sku", _("Multiple legacy Variants match this normalized SKU."), "sku"))
    elif candidates:
        variant = candidates[0]

    for field in INTEGER_FIELDS:
        value = _parse_integer(values.get(field), field, errors)
        if _text(values.get(field)):
            if value is not None:
                patch[field] = value
    for field in PRICE_FIELDS:
        value = _parse_price(values.get(field), field, errors)
        if _text(values.get(field)) and value is not None:
            patch[field] = value

    before = _snapshot(variant) if variant else {}
    after = {**before}
    if variant and not errors:
        for field in INTEGER_FIELDS:
            if field in patch:
                after[field] = patch[field]
        for prefix, currency in (("irt", "IRT"), ("usd", "USD")):
            amount_key, compare_key = f"{prefix}_price", f"{prefix}_compare_at"
            amount, compare = after[currency]["amount"], after[currency]["compare_at_amount"]
            if amount_key in patch:
                amount = str(patch[amount_key])
            if compare_key in patch:
                compare = None if patch[compare_key] == "__CLEAR__" else str(patch[compare_key])
            if compare is not None and amount is None:
                errors.append(_issue("compare_at_requires_price", _("A selling price is required when compare-at price is set."), compare_key))
            elif compare is not None and Decimal(compare) <= Decimal(amount):
                errors.append(_issue("compare_at_not_above_price", _("Compare-at price must be greater than the selling price."), compare_key))
            after[currency] = {"exists": amount is not None, "amount": amount, "compare_at_amount": compare}
        for prefix, currency in (("irt", "IRT"), ("usd", "USD")):
            compare_key = f"{prefix}_compare_at"
            if patch.get(compare_key) == "__CLEAR__" and not before[currency]["exists"]:
                warnings.append(_issue("clear_missing_compare_at", _("There is no compare-at price to clear."), compare_key))

    changed = []
    if variant and not errors:
        for field in ("stock", "low_stock_threshold"):
            if after[field] != before[field]:
                changed.append(field)
        for currency in ("IRT", "USD"):
            for key, label in (("amount", f"{currency}.amount"), ("compare_at_amount", f"{currency}.compare_at_amount")):
                if after[currency][key] != before[currency][key]:
                    changed.append(label)
    status = InventoryPriceImportRow.Status.ERROR if errors else (InventoryPriceImportRow.Status.CHANGED if changed else InventoryPriceImportRow.Status.UNCHANGED)
    return InventoryPriceImportRow(
        batch=batch, row_number=row_number, variant=variant, sku_snapshot=normalized_sku,
        row_status=status, normalized_patch=_serialize_patch(patch), before_snapshot=before,
        proposed_after_snapshot=after if variant else {}, changed_fields=changed,
        warnings=warnings, errors=errors,
    )


def _summary(rows):
    changed = [row for row in rows if row.row_status == InventoryPriceImportRow.Status.CHANGED]
    return {
        "total_rows": len(rows),
        "valid_rows": sum(row.row_status != InventoryPriceImportRow.Status.ERROR for row in rows),
        "changed_rows": len(changed),
        "unchanged_rows": sum(row.row_status == InventoryPriceImportRow.Status.UNCHANGED for row in rows),
        "error_rows": sum(row.row_status == InventoryPriceImportRow.Status.ERROR for row in rows),
        "warning_rows": sum(bool(row.warnings) for row in rows),
        "stock_changes": sum("stock" in row.changed_fields for row in changed),
        "threshold_changes": sum("low_stock_threshold" in row.changed_fields for row in changed),
        "irt_changes": sum(any(field.startswith("IRT.") for field in row.changed_fields) for row in changed),
        "usd_changes": sum(any(field.startswith("USD.") for field in row.changed_fields) for row in changed),
    }


@transaction.atomic
def create_inventory_price_import_preview(*, actor, uploaded_file):
    """Create persistent staging records only; never mutate commerce state."""
    if not actor.has_perm("products.run_inventory_price_import"):
        raise PermissionDenied
    if uploaded_file.size > MAX_SOURCE_BYTES:
        raise ImportSourceError("file_too_large", _("The upload exceeds the 5 MiB limit."))
    payload = uploaded_file.read()
    if len(payload) > MAX_SOURCE_BYTES:
        raise ImportSourceError("file_too_large", _("The upload exceeds the 5 MiB limit."))
    filename = _safe_filename(uploaded_file.name)
    source_format = _source_format(filename, payload)
    now = timezone.now()
    batch = InventoryPriceImportBatch.objects.create(
        actor=actor, original_filename=filename, source_format=source_format,
        source_sha256=hashlib.sha256(payload).hexdigest(), parser_version=PARSER_VERSION,
        source_expires_at=now + timedelta(days=90),
    )
    batch.source_file.save(filename, ContentFile(payload), save=True)
    try:
        parsed_rows = _parse_csv(payload) if source_format == InventoryPriceImportBatch.SourceFormat.CSV else _parse_xlsx(payload)
    except ImportSourceError as error:
        batch.status = InventoryPriceImportBatch.Status.INVALID
        batch.failure_summary = {"errors": [_issue(error.code, error.message)]}
        batch.save(update_fields=["status", "failure_summary"])
        return batch

    normalized_skus = [_text(values.get("sku")).upper() for _, values in parsed_rows if _text(values.get("sku"))]
    duplicates = {sku for sku, count in Counter(normalized_skus).items() if count > 1}
    candidates = list(
        ProductVariant.objects.annotate(normalized_import_sku=Upper(Trim("sku")))
        .filter(normalized_import_sku__in=set(normalized_skus))
        .select_related("product").prefetch_related("prices")
    )
    matches = defaultdict(list)
    for variant in candidates:
        matches[variant.sku.strip().upper()].append(variant)
    rows = [_row_for(batch, row_number, values, matches, duplicates) for row_number, values in parsed_rows]
    InventoryPriceImportRow.objects.bulk_create(rows)
    summary = _summary(rows)
    batch.status = InventoryPriceImportBatch.Status.INVALID if summary["error_rows"] else InventoryPriceImportBatch.Status.VALIDATED
    for field, value in summary.items():
        setattr(batch, field, value)
    batch.save(update_fields=["status", *summary.keys()])
    return batch


def get_inventory_price_import_preview(batch):
    """Return template/API-friendly staged data without exposing storage details."""
    rows = list(batch.rows.select_related("variant__product").order_by("row_number", "pk"))
    return {
        "batch": batch,
        "summary": {key: getattr(batch, key) for key in (
            "total_rows", "valid_rows", "changed_rows", "unchanged_rows", "error_rows",
            "warning_rows", "stock_changes", "threshold_changes", "irt_changes", "usd_changes",
        )},
        "rows": [{
            "row_number": row.row_number, "sku": row.sku_snapshot,
            "product": row.variant.product.name if row.variant_id else "",
            "variant": str(row.variant) if row.variant_id else "",
            "status": row.row_status, "errors": row.errors, "warnings": row.warnings,
            "before": row.before_snapshot, "after": row.proposed_after_snapshot,
            "changed_fields": row.changed_fields,
        } for row in rows],
    }
