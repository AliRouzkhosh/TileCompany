"""Emit privacy-safe database/file recovery sentinels for an operator drill."""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Iterable
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "TileCompany.settings")

import django

django.setup()

from blog.models import Article, ArticleAttachment
from django.contrib.auth import get_user_model
from django.db import connection
from orders.models import Order, RefundAttachment
from products.models import (
    Category,
    HomepageHero,
    HomepageMerchandisingMedia,
    InventoryPriceImportBatch,
    Product,
    ProductImage,
    VariantImage,
)
from support.models import Ticket, TicketAttachment


def _existing(objects: Iterable, field_name: str) -> dict[str, int]:
    referenced = found = 0
    for item in objects:
        field_file = getattr(item, field_name)
        if not field_file:
            continue
        referenced += 1
        if field_file.storage.exists(field_file.name):
            found += 1
    return {"referenced": referenced, "found": found, "missing": referenced - found}


def snapshot() -> dict:
    storefront_references = _existing(Category.objects.exclude(storefront_image="").only("storefront_image"), "storefront_image")
    for model in (HomepageHero, HomepageMerchandisingMedia):
        current = _existing(model.objects.exclude(image="").only("image"), "image")
        storefront_references = {
            key: storefront_references[key] + current[key]
            for key in storefront_references
        }
    families = {
        "product_primary": _existing(Product.objects.exclude(image="").only("image"), "image"),
        "product_gallery": _existing(ProductImage.objects.only("image"), "image"),
        "variant_gallery": _existing(VariantImage.objects.only("image"), "image"),
        "storefront": storefront_references,
        "blog": {
            key: _existing(Article.objects.exclude(cover_image="").only("cover_image"), "cover_image")[key]
            + _existing(ArticleAttachment.objects.only("file"), "file")[key]
            for key in ("referenced", "found", "missing")
        },
        "support": _existing(TicketAttachment.objects.only("file"), "file"),
        "refund": _existing(RefundAttachment.objects.only("file"), "file"),
        "private_inventory": _existing(InventoryPriceImportBatch.objects.exclude(source_file="").only("source_file"), "source_file"),
    }
    return {
        "database_sentinels": {
            "migrations": _migration_count(),
            "accounts": get_user_model().objects.count(),
            "products": Product.objects.count(),
            "orders": Order.objects.count(),
            "support_tickets": Ticket.objects.count(),
            "refund_attachments": RefundAttachment.objects.count(),
        },
        "file_references": families,
    }


def _migration_count() -> int:
    with connection.cursor() as cursor:
        cursor.execute("SELECT count(*) FROM django_migrations")
        return cursor.fetchone()[0]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--expected-manifest", type=Path)
    arguments = parser.parse_args()
    result = snapshot()
    if arguments.expected_manifest:
        expected = json.loads(arguments.expected_manifest.read_text(encoding="utf-8"))
        if result != {
            "database_sentinels": expected["database_sentinels"],
            "file_references": expected["file_references"],
        }:
            raise SystemExit("Recovery sentinel mismatch.")
    if any(item["missing"] for item in result["file_references"].values()):
        raise SystemExit("Recovery file-reference check found missing files.")
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
