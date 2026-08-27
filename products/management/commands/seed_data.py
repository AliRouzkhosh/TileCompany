from decimal import Decimal, ROUND_HALF_UP
from random import randint, choice, sample, random

from django.core.management.base import BaseCommand
from django.core.management.base import CommandError
from django.db import transaction
from django.utils.text import slugify
from products.catalog_compat import (
    lock_and_validate_tile_product,
    lock_and_validate_tile_variant,
    sync_legacy_catalog_option,
    sync_tile_product_catalog,
    sync_tile_variant_catalog,
)
from products.catalog_backfill import apply_tile_catalog

from products.models import (
    Brand,
    Category,
    Color,
    Design,
    Finish,
    Material,
    Product,
    ProductType,
    ProductVariant,
    VariantPrice,
    Size,
    Usage,
)


class Command(BaseCommand):
    help = "Generate 10 realistic tile products with 5 variants each"

    @transaction.atomic
    def handle(self, *args, **kwargs):
        if not ProductType.objects.filter(code="tile").exists():
            # F1B.2 remains the sole owner of schema establishment; the seed
            # command invokes it explicitly on a fresh development database.
            apply_tile_catalog()
        categories_data = [
            "Floor Tiles", "Wall Tiles", "Outdoor Tiles", "Mosaic Tiles",
            "Bathroom Tiles", "Kitchen Tiles", "Commercial Tiles",
            "Marble Look Tiles", "Wood Look Tiles", "Stone Look Tiles",
        ]
        persian_categories = {
            "Floor Tiles": "کاشی کف", "Wall Tiles": "کاشی دیوار", "Outdoor Tiles": "کاشی فضای باز",
            "Mosaic Tiles": "کاشی موزاییکی", "Bathroom Tiles": "کاشی حمام", "Kitchen Tiles": "کاشی آشپزخانه",
            "Commercial Tiles": "کاشی تجاری", "Marble Look Tiles": "کاشی طرح مرمر",
            "Wood Look Tiles": "کاشی طرح چوب", "Stone Look Tiles": "کاشی طرح سنگ",
        }

        categories = {}
        for name in categories_data:
            category, _ = Category.objects.get_or_create(
                name=name, defaults={"slug": slugify(name), "name_fa": persian_categories[name]}
            )
            categories[name] = category

        brand_names = ["Ariana", "Atlas", "Ceramica Nova", "StoneLux", "TilePro", "UrbanSurfaces"]
        persian_brands = {
            "Ariana": "آریانا", "Atlas": "اطلس", "Ceramica Nova": "سرامیکا نوا",
            "StoneLux": "استون لاکس", "TilePro": "تایل پرو", "UrbanSurfaces": "اوربن سرفیسز",
        }
        existing_brand_ids = set(Brand.objects.values_list("pk", flat=True))
        brands = {
            name: Brand.objects.get_or_create(
                name=name,
                defaults={"slug": slugify(name), "name_fa": persian_brands[name]},
            )[0]
            for name in brand_names
        }
        for item in brands.values():
            sync_legacy_catalog_option(item, allow_create=item.pk not in existing_brand_ids, defer_complete_resequence=True)

        collections = [
            "Carrara Marble", "Calacatta Gold", "Urban Concrete", "Nordic Stone",
            "Travertine Classic", "Oak Natural", "Walnut Premium", "Industrial Grey",
            "Venice Terrazzo", "Atlas Slate", "Desert Sand", "Arctic White",
            "Volcano Black", "Mediterranean Blue", "Pearl Beige",
        ][:10]

        color_names = ["White", "Grey", "Dark Grey", "Black", "Beige", "Brown", "Blue", "Cream"]
        material_names = ["Porcelain", "Ceramic", "Granite", "Glass"]
        finish_names = ["Matte", "Glossy", "Textured", "Natural", "Polished"]
        persian_colors = {"White": "سفید", "Grey": "خاکستری", "Dark Grey": "خاکستری تیره", "Black": "مشکی", "Beige": "بژ", "Brown": "قهوه‌ای", "Blue": "آبی", "Cream": "کرم"}
        persian_materials = {"Porcelain": "پرسلان", "Ceramic": "سرامیک", "Granite": "گرانیت", "Glass": "شیشه"}
        persian_finishes = {"Matte": "مات", "Glossy": "براق", "Textured": "بافت‌دار", "Natural": "طبیعی", "Polished": "پولیش‌شده"}

        dimension_options = [(300, 300), (600, 600), (600, 1200), (800, 800), (1200, 1200)]
        thickness_options = [Decimal("8.5"), Decimal("9.0"), Decimal("9.5"), Decimal("10.0"), Decimal("10.5"), Decimal("12.0")]

        existing_material_ids = set(Material.objects.values_list("pk", flat=True))
        materials = {
            name: Material.objects.get_or_create(
                name=name,
                defaults={"slug": slugify(name), "name_fa": persian_materials[name]},
            )[0]
            for name in material_names
        }
        for item in materials.values():
            sync_legacy_catalog_option(item, allow_create=item.pk not in existing_material_ids, defer_complete_resequence=True)

        existing_color_ids = set(Color.objects.values_list("pk", flat=True))
        colors = {
            name: Color.objects.get_or_create(
                name=name,
                defaults={"slug": slugify(name), "name_fa": persian_colors[name]},
            )[0]
            for name in color_names
        }
        for item in colors.values():
            sync_legacy_catalog_option(item, allow_create=item.pk not in existing_color_ids, defer_complete_resequence=True)

        existing_finish_ids = set(Finish.objects.values_list("pk", flat=True))
        finishes = {
            name: Finish.objects.get_or_create(
                name=name,
                defaults={"slug": slugify(name), "name_fa": persian_finishes[name]},
            )[0]
            for name in finish_names
        }
        for item in finishes.values():
            sync_legacy_catalog_option(item, allow_create=item.pk not in existing_finish_ids, defer_complete_resequence=True)
        usage_names = [
            "floor", "wall", "bathroom", "kitchen", "indoor",
            "outdoor", "commercial", "residential",
        ]
        persian_usages = {"floor": "کف", "wall": "دیوار", "bathroom": "حمام", "kitchen": "آشپزخانه", "indoor": "داخلی", "outdoor": "فضای باز", "commercial": "تجاری", "residential": "مسکونی"}
        existing_usage_ids = set(Usage.objects.values_list("pk", flat=True))
        usages = {
            name: Usage.objects.get_or_create(
                name=name.title(),
                defaults={"slug": slugify(name), "name_fa": persian_usages[name]},
            )[0]
            for name in usage_names
        }
        for item in usages.values():
            sync_legacy_catalog_option(item, allow_create=item.pk not in existing_usage_ids, defer_complete_resequence=True)

        usage_by_category = {
            "Floor Tiles": ["floor", "indoor"],
            "Wall Tiles": ["wall", "indoor"],
            "Outdoor Tiles": ["floor", "outdoor"],
            "Mosaic Tiles": ["wall", "bathroom", "kitchen", "indoor"],
            "Bathroom Tiles": ["floor", "wall", "bathroom", "indoor"],
            "Kitchen Tiles": ["floor", "wall", "kitchen", "indoor"],
            "Commercial Tiles": ["floor", "wall", "indoor", "commercial"],
            "Marble Look Tiles": ["floor", "wall", "indoor"],
            "Wood Look Tiles": ["floor", "indoor"],
            "Stone Look Tiles": ["floor", "wall", "indoor", "outdoor"],
        }

        def infer_design(collection_name: str) -> str:
            c = collection_name.lower()
            if "marble" in c or "calacatta" in c or "carrara" in c:
                return "Marble-look"
            if "stone" in c or "slate" in c or "travertine" in c:
                return "Stone-look"
            if "oak" in c or "walnut" in c or "wood" in c:
                return "Wood-look"
            if "concrete" in c or "industrial" in c or "urban" in c:
                return "Concrete-look"
            if "terrazzo" in c:
                return "Terrazzo-look"
            return "Plain"

        persian_designs = {
            "Marble-look": "طرح مرمر", "Stone-look": "طرح سنگ", "Wood-look": "طرح چوب",
            "Concrete-look": "طرح بتن", "Terrazzo-look": "طرح ترازو", "Plain": "ساده",
        }
        persian_collections = {
            "Carrara Marble": "مرمر کارارا", "Calacatta Gold": "کالاکاتا طلایی", "Urban Concrete": "بتن شهری",
            "Nordic Stone": "سنگ نوردیک", "Travertine Classic": "تراورتن کلاسیک", "Oak Natural": "بلوط طبیعی",
            "Walnut Premium": "گردوی ممتاز", "Industrial Grey": "خاکستری صنعتی", "Venice Terrazzo": "ترازو ونیز",
            "Atlas Slate": "اسلیت اطلس", "Desert Sand": "شن کویر", "Arctic White": "سفید قطبی",
            "Volcano Black": "مشکی آتشفشانی", "Mediterranean Blue": "آبی مدیترانه‌ای", "Pearl Beige": "بژ مرواریدی",
        }

        design_names = {
            infer_design(collection)
            for collection in collections
        }
        existing_design_ids = set(Design.objects.values_list("pk", flat=True))
        designs = {
            name: Design.objects.get_or_create(
                name=name,
                defaults={"slug": slugify(name), "name_fa": persian_designs[name]},
            )[0]
            for name in design_names
        }
        for item in designs.values():
            sync_legacy_catalog_option(item, allow_create=item.pk not in existing_design_ids, defer_complete_resequence=True)

        products_created = 0
        variants_created = 0
        sku_counter = 1

        for collection in collections:
            brand = choice(list(brands.values()))
            category_names = sample(list(categories.keys()), k=randint(1, 3))
            selected_categories = [
                categories[name]
                for name in category_names
            ]
            selected_usages = [
                usages[name]
                for name in usage_by_category.get(
                    category_names[0],
                    ["floor", "wall", "indoor"],
                )
            ]
            design = designs[infer_design(collection)]
            series_material = choice(list(materials.values()))
            series_colors = sample(list(colors.values()), k=randint(2, 4))
            series_finishes = sample(list(finishes.values()), k=randint(2, 3))

            # parent product (series)
            base_slug = slugify(f"{collection}-{brand.name}")
            seed_sku = f"SERIES-{sku_counter:04d}"

            product, p_created = Product.objects.get_or_create(
                sku=seed_sku,
                defaults={
                    "name": collection,
                    "name_fa": persian_collections[collection],
                    "slug": base_slug,
                    "description": f"{collection} series by {brand.name}. Multiple sizes/colors/finishes available.",
                    "description_fa": f"مجموعه {persian_collections[collection]} از {persian_brands[brand.name]} با اندازه، رنگ و پرداخت‌های قابل انتخاب.",
                    "brand": brand,
                    "series_name": collection,
                    "series_name_fa": persian_collections[collection],
                    "is_active": True,
                    "is_featured": randint(1, 100) <= 20,
                },
            )
            if not p_created:
                lock_and_validate_tile_product(product.pk)
            if p_created:
                products_created += 1
            sku_counter += 1

            product.categories.set(selected_categories)
            product.designs.set([design])
            product.materials.set([series_material])
            product.usages.set(selected_usages)
            product = sync_tile_product_catalog(product, legacy_write_authorized=not p_created)

            # generate five purchasable variants per series
            selected_sizes = sample(dimension_options, k=len(dimension_options))
            variant_index = 1

            for (length_mm, width_mm) in selected_sizes:
                size_display = f"{int(length_mm/10)}x{int(width_mm/10)}cm"
                size, size_created = Size.objects.get_or_create(
                    name=size_display,
                    defaults={
                        "slug": slugify(size_display),
                        "name_fa": size_display,
                        "length_mm": length_mm,
                        "width_mm": width_mm,
                    },
                )
                sync_legacy_catalog_option(size, allow_create=size_created)

                price = (Decimal(randint(1200, 7000)) / Decimal("100")).quantize(
                    Decimal("0.01"), rounding=ROUND_HALF_UP
                )
                original_price = None
                if randint(1, 100) <= 30:
                    original_price = (price * Decimal("1.20")).quantize(
                        Decimal("0.01"), rounding=ROUND_HALF_UP
                    )

                tile_area_m2 = Decimal(length_mm * width_mm) / Decimal("1000000")
                tiles_per_box = randint(2, 8)
                coverage_per_box_m2 = (tile_area_m2 * Decimal(tiles_per_box)).quantize(
                    Decimal("0.001"), rounding=ROUND_HALF_UP
                )

                stock_boxes = randint(5, 300)

                vsku = f"VAR-{product.id:04d}-{variant_index:03d}"
                existing_variant = ProductVariant.objects.filter(sku=vsku).first()
                if existing_variant and existing_variant.product_id != product.id:
                    raise CommandError(
                        f"Deterministic seed SKU {vsku} belongs to another product."
                    )

                variant, v_created = ProductVariant.objects.get_or_create(
                    sku=vsku,
                    defaults={
                        "product": product,
                        "finish": choice(series_finishes),
                        "color": choice(series_colors),
                        "size": size,
                        "length_mm": length_mm,
                        "width_mm": width_mm,
                        "thickness_mm": choice(thickness_options),
                        "tiles_per_box": tiles_per_box,
                        "coverage_per_box_m2": coverage_per_box_m2,
                        "price_unit": ProductVariant.PriceUnit.BOX,
                        "stock": stock_boxes,
                        "is_active": True,
                        "sort_order": variant_index,
                    },
                )
                if not v_created:
                    lock_and_validate_tile_variant(variant.pk)
                variant = sync_tile_variant_catalog(
                    variant, legacy_write_authorized=not v_created
                )
                if v_created:
                    variants_created += 1
                VariantPrice.objects.get_or_create(
                    variant=variant,
                    currency="USD",
                    defaults={"amount": price, "compare_at_amount": original_price},
                )
                toman_amount = Decimal(randint(2_000_000, 8_000_000))
                toman_compare_at = (
                    (toman_amount * Decimal("1.15")).quantize(Decimal("1"))
                    if randint(1, 100) <= 30 else None
                )
                VariantPrice.objects.get_or_create(
                    variant=variant,
                    currency="IRT",
                    defaults={"amount": toman_amount, "compare_at_amount": toman_compare_at},
                )
                variant_index += 1

        self.stdout.write(self.style.SUCCESS(
            f"Done. Created {products_created} products and {variants_created} variants."
        ))
