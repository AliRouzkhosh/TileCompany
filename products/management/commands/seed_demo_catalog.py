"""Seed the controlled 50-product generic demonstration catalogue.

It is intentionally opt-in: no invocation writes unless ``--apply`` is given.
``--dry-run`` follows the complete path inside a transaction and rolls it back.
"""

from decimal import Decimal

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.utils.text import slugify

from products.catalog_domain import calculate_variant_combination_signature
from products.catalog_integrity import active_products_with_invalid_type
from products.currency import Currency
from products.demo_catalog import (
    ADHESIVE_APPLICATION_SOURCE_DERIVED, BRANDS, CATEGORIES,
    CERAMIC_APPLICATION_AREA_CONTROLLED_DEMO, CERAMIC_PORCELAIN_SOURCE_DERIVED, CERAMIC_USAGE_CONTROLLED_DEMO,
    INSTALLATION, OPTION_FA, PRODUCT_TYPES, SCHEMA, TILES, UNKNOWN,
    WALL_FLOOR_CATEGORY_CONTROLLED_DEMO,
)
from products.models import (
    AttributeDefinition, AttributeOption, Brand, Category, Product,
    ProductAttributeValue, ProductType, ProductTypeAttribute,
    ProductTypeAttributeOption, ProductVariant,
    VariantAttributeValue,
)
from products.publication_services import publish_product, publish_variant
from products.services import set_variant_price
from products.storefront_currency_policy import required_storefront_currencies


class Command(BaseCommand):
    help = "Preview or explicitly apply the deterministic generic demo catalogue."

    def add_arguments(self, parser):
        mode = parser.add_mutually_exclusive_group()
        mode.add_argument("--dry-run", action="store_true", help="Validate the full seed transaction then roll it back (default).")
        mode.add_argument("--apply", action="store_true", help="Persist the seed transaction.")

    def handle(self, *args, **options):
        apply = bool(options["apply"])
        self._guard_environment(apply=apply)
        self.stdout.write(
            f"seed_demo_catalog: APP_ENV={settings.APP_ENV}; database={connection.vendor}; "
            f"name={connection.settings_dict.get('NAME')}; mode={'apply' if apply else 'dry-run'}"
        )
        self._preflight()
        with transaction.atomic():
            summary = self._seed()
            if not apply:
                transaction.set_rollback(True)
        verb = "Applied" if apply else "Dry-run validated (rolled back)"
        self.stdout.write(self.style.SUCCESS(f"{verb}: {summary}"))

    def _guard_environment(self, *, apply):
        if settings.APP_ENV == "production":
            raise CommandError("seed_demo_catalog is forbidden when APP_ENV=production.")
        if settings.APP_ENV == "local_production" and connection.vendor != "postgresql":
            raise CommandError("APP_ENV=local_production requires a PostgreSQL target.")
        if not apply:
            self.stdout.write("No --apply supplied; running non-mutating dry-run.")

    def _preflight(self):
        invalid = list(active_products_with_invalid_type().values_list("sku", flat=True)[:10])
        if invalid:
            raise CommandError("Catalog integrity preflight failed: active Products have missing/inactive ProductType: " + ", ".join(invalid))
        # A legacy Tile compatibility definition may still be named ``brand``.
        # It must never leak into any new generic ProductType as a competing
        # authority; Product.brand is the seed's sole brand relation.
        if ProductTypeAttribute.objects.exclude(product_type__code="tile").filter(definition__code="brand").exists():
            raise CommandError("Catalog preflight failed: a non-Tile ProductType binds generic 'brand'; Product.brand is canonical.")
        if set(required_storefront_currencies()) != {Currency.USD, Currency.TOMAN}:
            raise CommandError("Catalog preflight failed: USD and IRT storefront currencies are required.")
        if not {"bag", "bucket", "pack"}.issubset({value for value, _ in ProductVariant.PriceUnit.choices}):
            raise CommandError("Catalog preflight failed: expected selling units are unavailable.")
        executor = MigrationExecutor(connection)
        if executor.migration_plan(executor.loader.graph.leaf_nodes()):
            raise CommandError("Catalog preflight failed: unapplied migrations are present.")

    @staticmethod
    def _owned(existing, expected, identity):
        for field, value in expected.items():
            if getattr(existing, field) != value:
                raise CommandError(f"Seed identity conflict for {identity}: {field} differs; refusing to overwrite unrelated data.")
        return existing

    def _get(self, model, lookup, defaults, identity):
        obj = model.objects.filter(**lookup).first()
        if obj is None:
            return model.objects.create(**lookup, **defaults), True
        return self._owned(obj, defaults, identity), False

    def _seed(self):
        counts = {"brands": 0, "categories": 0, "types": 0, "definitions": 0, "products": 0, "variants": 0, "prices": 0}
        brands = {}
        for slug, name, name_fa in BRANDS:
            brands[slug], made = self._get(Brand, {"slug": slug}, {"name": name, "name_fa": name_fa}, f"Brand {slug}")
            counts["brands"] += made
        categories = {}
        for order, (slug, name, name_fa, parent_slug) in enumerate(CATEGORIES):
            parent = categories.get(parent_slug)
            categories[slug], made = self._get(Category, {"slug": slug}, {"name": name, "name_fa": name_fa, "parent": parent, "sort_order": order, "is_active": True, "show_in_navigation": True}, f"Category {slug}")
            counts["categories"] += made
        types = {}
        for order, (code, name, name_fa) in enumerate(PRODUCT_TYPES):
            types[code], made = self._get(ProductType, {"code": code}, {"name": name, "name_fa": name_fa, "is_active": True, "sort_order": order}, f"ProductType {code}")
            counts["types"] += made
        bindings, options, restriction_specs = self._seed_schema(types, counts)
        for index, (brand_code, name, name_fa, variants) in enumerate(TILES, 1):
            usage = next(
                code for code, names in CERAMIC_USAGE_CONTROLLED_DEMO.items()
                if name in names
            )
            primary_category = (
                categories["porcelain-tiles"]
                if name in CERAMIC_PORCELAIN_SOURCE_DERIVED
                else categories["wall-floor-tiles"]
                if name in WALL_FLOOR_CATEGORY_CONTROLLED_DEMO
                else categories["tiles-ceramics"]
            )
            category_rows = [primary_category]
            if name in WALL_FLOOR_CATEGORY_CONTROLLED_DEMO and primary_category != categories["wall-floor-tiles"]:
                category_rows.append(categories["wall-floor-tiles"])
            product = self._seed_product(
                sku=f"DEMO-{brand_code.split('-')[0][:3].upper()}-{slugify(name).upper()}", name=name, name_fa=name_fa,
                brand=brands[brand_code], product_type=types["ceramic-tile"], categories=category_rows, primary_category=primary_category, index=index, counts=counts,
            )
            material = "porcelain" if name in CERAMIC_PORCELAIN_SOURCE_DERIVED else "ceramic"
            self._product_option(product, bindings[("ceramic-tile", "demo-material-body")], options["demo-material-body"][material])
            self._product_option(product, bindings[("ceramic-tile", "demo-usage")], options["demo-usage"][usage])
            self._product_options(
                product,
                bindings[("ceramic-tile", "demo-application-area")],
                [options["demo-application-area"][code]
                 for code, names in CERAMIC_APPLICATION_AREA_CONTROLLED_DEMO.items()
                 if name in names],
            )
            for position, (suffix, size, color, finish) in enumerate(variants, 1):
                vals = {"demo-size": size or UNKNOWN, "demo-color": color or UNKNOWN, "demo-finish": finish or UNKNOWN}
                self._seed_variant(product, f"{product.sku}-{suffix}", "box", vals, bindings, options, position, index, counts)
            self._publish(product)
        for index, (brand_code, type_code, name, name_fa, unit, form, variants) in enumerate(INSTALLATION, 31):
            category = {"tile-adhesive": "tile-adhesives", "grout": "grout-joint-fillers", "installation-additive": "installation-additives"}[type_code]
            product = self._seed_product(
                sku=f"DEMO-{brand_code.split('-')[0][:3].upper()}-{slugify(name).upper()}", name=name, name_fa=name_fa,
                brand=brands[brand_code], product_type=types[type_code], categories=[categories[category]], primary_category=categories[category], index=index, counts=counts,
            )
            if type_code == "tile-adhesive":
                self._product_option(product, bindings[(type_code, "demo-adhesive-form")], options["demo-adhesive-form"][form])
                if application := ADHESIVE_APPLICATION_SOURCE_DERIVED.get(name):
                    self._product_option(product, bindings[(type_code, "demo-application")], options["demo-application"][application])
                if "C1E" in name: self._product_option(product, bindings[(type_code, "demo-standard-class")], options["demo-standard-class"]["c1e"])
                if "C2E" in name: self._product_option(product, bindings[(type_code, "demo-standard-class")], options["demo-standard-class"]["c2e"])
            elif type_code == "grout":
                self._product_option(product, bindings[(type_code, "demo-grout-base")], options["demo-grout-base"][form])
                self._product_option(product, bindings[(type_code, "demo-water-resistance")], options["demo-water-resistance"]["water-resistant"])
            else:
                self._product_option(product, bindings[(type_code, "demo-installation-form")], options["demo-installation-form"][form])
            for position, (suffix, weight, color) in enumerate(variants, 1):
                vals = {"demo-package-weight": weight or UNKNOWN}
                if type_code == "tile-adhesive": vals["demo-adhesive-color"] = color or UNKNOWN
                if type_code == "grout": vals["demo-grout-color"] = color or UNKNOWN
                self._seed_variant(product, f"{product.sku}-{suffix}", unit, vals, bindings, options, position, index, counts)
            self._publish(product)
        self._apply_option_restrictions(restriction_specs)
        self._retire_legacy_standard_options({
            code: binding.definition for (_, code), binding in bindings.items()
        })
        return ", ".join(f"{key}={value}" for key, value in counts.items())

    def _seed_schema(self, types, counts):
        definitions, options, bindings, restriction_specs = {}, {}, {}, []
        for type_code, attrs in SCHEMA.items():
            for order, (code, name, fa, scope, dimension, facet, option_codes) in enumerate(attrs):
                definition = definitions.get(code)
                if definition is None:
                    definition, made = self._get_seed_schema(AttributeDefinition, {"code": code}, {"name": name, "name_fa": fa, "data_type": "option", "is_active": True}, f"AttributeDefinition {code}")
                    definitions[code] = definition; options[code] = {}; counts["definitions"] += made
                allowed_codes = set(option_codes) | ({UNKNOWN} if dimension else set())
                for option_code in allowed_codes:
                    metadata = {"seed_fidelity": UNKNOWN, "internal": True} if option_code == UNKNOWN else {}
                    option, _ = self._get_seed_schema(AttributeOption, {"definition": definition, "code": option_code}, {"name": option_code.replace("-", " ").title(), "name_fa": OPTION_FA[option_code], "is_active": True, "presentation_metadata": metadata}, f"AttributeOption {code}/{option_code}")
                    options[code][option_code] = option
                defaults = {"scope": scope, "cardinality": "multiple" if code == "demo-application-area" else "single", "is_required": dimension, "is_variant_dimension": dimension, "is_filterable": facet, "facet_mode": "terms" if facet else None, "is_searchable": facet, "show_on_storefront": True, "show_in_specifications": True, "include_in_purchase_snapshot": dimension, "sort_order": order, "is_active": True}
                binding, _ = self._get_seed_schema(ProductTypeAttribute, {"product_type": types[type_code], "definition": definition}, defaults, f"binding {type_code}/{code}")
                bindings[(type_code, code)] = binding
                restriction_specs.append((binding, tuple(sorted(allowed_codes)), options[code]))
        return bindings, options, restriction_specs

    def _seed_product(self, *, sku, name, name_fa, brand, product_type, categories, primary_category, index, counts):
        product = Product.objects.filter(sku=sku).first()
        expected = {"name": name, "name_fa": name_fa, "slug": slugify(sku.lower()), "description": f"Controlled demo catalogue item: {name}.", "description_fa": f"کالای نمایشی کنترل‌شده: {name_fa}.", "brand": brand, "product_type": product_type, "is_featured": index % 9 == 0}
        if product is None:
            product = Product.objects.create(sku=sku, is_active=False, **expected); counts["products"] += 1
        else:
            self._owned(product, expected, f"Product {sku}")
        product.categories.set(categories); product.primary_category = primary_category; product.save(update_fields=["primary_category", "updated_at"])
        return product

    @staticmethod
    def _product_option(product, binding, option):
        rows = list(ProductAttributeValue.objects.filter(product=product, binding=binding))
        if len(rows) > 1:
            raise CommandError(f"Seed-owned Product {product.sku} has multiple values for {binding.definition.code}.")
        row = rows[0] if rows else ProductAttributeValue(product=product, binding=binding)
        row.option_value = option
        row.full_clean()
        row.save()

    @staticmethod
    def _product_options(product, binding, options):
        """Synchronize a seed-owned multi-value generic product attribute."""
        if binding.cardinality != ProductTypeAttribute.Cardinality.MULTIPLE:
            raise CommandError(f"{binding.definition.code} is not a multiple-value binding.")
        ProductAttributeValue.objects.filter(product=product, binding=binding).exclude(
            option_value__in=options
        ).delete()
        for position, option in enumerate(options):
            row, _ = ProductAttributeValue.objects.get_or_create(
                product=product, binding=binding, option_value=option,
                defaults={"sort_order": position},
            )
            row.sort_order = position
            row.full_clean()
            row.save(update_fields=["sort_order"])

    @staticmethod
    def _get_seed_schema(model, lookup, defaults, identity):
        """Update only the command's stable ``demo-*`` schema identities."""
        obj = model.objects.filter(**lookup).first()
        if obj is None:
            return model.objects.create(**lookup, **defaults), True
        for field, value in defaults.items():
            setattr(obj, field, value)
        obj.full_clean()
        obj.save(update_fields=[*defaults, "updated_at"])
        return obj, False

    def _seed_variant(self, product, sku, unit, values, bindings, options, position, product_index, counts):
        variant = ProductVariant.objects.filter(sku=sku).first()
        expected = {"product": product, "price_unit": unit, "stock": 0 if product_index % 17 == 0 else (2 if product_index % 11 == 0 else 8 + product_index % 23), "low_stock_threshold": 5, "sort_order": position}
        if variant is None:
            variant = ProductVariant.objects.create(sku=sku, is_active=False, **expected); counts["variants"] += 1
        else:
            self._owned(variant, expected, f"Variant {sku}")
        for code, option_code in values.items():
            binding = bindings[(product.product_type.code, code)]
            rows = list(VariantAttributeValue.objects.filter(variant=variant, binding=binding))
            if len(rows) > 1:
                raise CommandError(f"Seed-owned Variant {sku} has multiple values for {code}.")
            row = rows[0] if rows else VariantAttributeValue(variant=variant, binding=binding)
            row.option_value = options[code][option_code]
            row.full_clean()
            row.save()
        variant.combination_signature = calculate_variant_combination_signature(variant)
        variant.full_clean(); variant.save(update_fields=["combination_signature"])
        amount = Decimal("18.00") + Decimal(product_index) + Decimal(position) / Decimal("10")
        sale = product_index % 5 == 0
        toman = Decimal("900000") + Decimal(product_index * 55000 + position * 5000)
        for currency, price, compare in ((Currency.USD, amount, (amount * Decimal("1.15")).quantize(Decimal("0.01")) if sale else None), (Currency.TOMAN, toman, (toman * Decimal("1.15")).quantize(Decimal("1")) if sale else None)):
            before = variant.prices.filter(currency=currency).exists()
            set_variant_price(variant=variant, currency=currency, amount=price, compare_at_amount=compare, enforce_active_readiness=False)
            counts["prices"] += not before
        publish_variant(variant)

    @staticmethod
    def _publish(product):
        publish_product(product)

    @staticmethod
    def _apply_option_restrictions(restriction_specs):
        """Apply only after old seed values have been upgraded from standard."""
        for binding, codes, options in restriction_specs:
            for code in codes:
                restriction, _ = ProductTypeAttributeOption.objects.get_or_create(
                    binding=binding, option=options[code],
                )
                restriction.full_clean()

    @staticmethod
    def _retire_legacy_standard_options(definitions):
        """Retire the old seed-only placeholder after all values are upgraded."""
        AttributeOption.objects.filter(
            definition__in=definitions.values(), code="standard",
        ).update(is_active=False)
