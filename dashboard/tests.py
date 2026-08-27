from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from django.utils import translation

from dashboard.forms import DashboardProductForm, DashboardProductVariantForm
from dashboard.notifications import get_dashboard_notifications
from dashboard.selectors.products import get_products
from products.currency import Currency
from products.catalog_backfill import apply_tile_catalog
from products.models import Brand, Category, Color, Design, Finish, Material, Product, ProductOperationalChange, ProductVariant, Size, Usage
from orders.models import Order, OrderNote, OrderStatusHistory


class DashboardProductFormTests(TestCase):
    def test_product_form_saves_multiple_categories(self):
        floor = Category.objects.create(
            name="Floor Tiles",
            slug="floor-tiles",
        )
        wall = Category.objects.create(
            name="Wall Tiles",
            slug="wall-tiles",
        )
        porcelain = Material.objects.create(
            name="Porcelain",
            slug="porcelain",
        )
        atlas = Brand.objects.create(
            name="Atlas",
            slug="atlas",
        )
        stone_look = Design.objects.create(
            name="Stone Look",
            slug="stone-look",
        )
        indoor = Usage.objects.create(
            name="Indoor",
            slug="indoor",
        )
        floor_usage = Usage.objects.create(
            name="Floor",
            slug="floor",
        )

        form = DashboardProductForm(
            data={
                "name": "Atlas Stone",
                "slug": "atlas-stone",
                "sku": "ATLAS-STONE",
                "categories": [str(floor.pk), str(wall.pk)],
                "materials": [str(porcelain.pk)],
                "usages": [str(indoor.pk), str(floor_usage.pk)],
                "description": "",
                "brand": str(atlas.pk),
                "series_name": "",
                "designs": [str(stone_look.pk)],
                "low_stock_threshold": "5",
                "is_active": "on",
            }
        )

        self.assertTrue(form.is_valid(), form.errors)

        product = form.save()

        self.assertEqual(
            set(product.categories.values_list("pk", flat=True)),
            {floor.pk, wall.pk},
        )
        self.assertEqual(
            set(product.materials.values_list("pk", flat=True)),
            {porcelain.pk},
        )
        self.assertEqual(
            set(product.usages.values_list("pk", flat=True)),
            {indoor.pk, floor_usage.pk},
        )
        self.assertEqual(product.brand, atlas)
        self.assertEqual(
            set(product.designs.values_list("pk", flat=True)),
            {stone_look.pk},
        )
        self.assertFalse(product.is_featured)

    def test_product_model_has_no_category_field(self):
        field_names = {
            field.name
            for field in Product._meta.get_fields()
        }

        self.assertIn("categories", field_names)
        self.assertIn("materials", field_names)
        self.assertIn("usages", field_names)
        self.assertIn("brand", field_names)
        self.assertIn("designs", field_names)
        self.assertNotIn("category", field_names)
        self.assertNotIn("material", field_names)
        self.assertNotIn("usage", field_names)
        self.assertNotIn("design", field_names)

    def test_product_name_fa_is_optional(self):
        field = Product._meta.get_field("name_fa")
        self.assertTrue(field.blank)
        self.assertEqual(field.max_length, 200)


class DashboardCatalogDataLocalizationTests(TestCase):
    def setUp(self):
        call_command("seed_dashboard_roles", verbosity=0)
        self.user = get_user_model().objects.create_user(
            username="catalog-staff", password="secret"
        )
        self.user.groups.add(Group.objects.get(name="Product Manager"))
        self.client.force_login(self.user)
        apply_tile_catalog()

    def test_catalog_terms_keep_bilingual_content_and_canonical_slugs(self):
        with translation.override("en"):
            category_create_url = reverse(
                "dashboard:products:catalog_term_create", args=["categories"]
            )
        response = self.client.post(
            category_create_url,
            {"name": "Wall Tile", "name_fa": "کاشی دیوار", "slug": "wall-tile"},
        )
        category = Category.objects.get(slug="wall-tile")
        self.assertRedirects(
            response,
            reverse("dashboard:products:catalog_term_list", args=["categories"]),
        )
        self.assertEqual(category.name, "Wall Tile")
        self.assertEqual(category.name_fa, "کاشی دیوار")

        with translation.override("fa"):
            category_edit_url = reverse(
                "dashboard:products:catalog_term_edit", args=["categories", category.pk]
            )
            category_list_url = reverse(
                "dashboard:products:catalog_term_list", args=["categories"]
            )
            response = self.client.get(category_edit_url)
            self.assertContains(response, "نام انگلیسی")
            self.assertContains(response, "نام فارسی")
            self.assertContains(response, 'dir="ltr"')
            self.assertContains(response, 'dir="rtl"')
            response = self.client.post(
                category_edit_url,
                {"name": "Wall Tile", "name_fa": "کاشی دیواری", "slug": "wall-tile"},
            )
        self.assertRedirects(response, category_list_url)
        category.refresh_from_db()
        self.assertEqual(category.name, "Wall Tile")
        self.assertEqual(category.name_fa, "کاشی دیواری")
        self.assertEqual(category.slug, "wall-tile")

        for kind, model, data in (
            ("materials", Material, {"name": "Porcelain", "name_fa": "پرسلان", "slug": "porcelain"}),
            ("colors", Color, {"name": "Black", "name_fa": "مشکی", "slug": "black"}),
            ("finishes", Finish, {"name": "Matte", "name_fa": "مات", "slug": "matte"}),
            ("sizes", Size, {"name": "60 × 60 cm", "name_fa": "۶۰ × ۶۰ سانتی‌متر", "slug": "60x60", "length_mm": "600", "width_mm": "600"}),
        ):
            with translation.override("en"):
                create_url = reverse("dashboard:products:catalog_term_create", args=[kind])
            self.client.post(create_url, data)
            item = model.objects.get(slug=data["slug"])
            self.assertEqual(item.name, data["name"])
            self.assertEqual(item.name_fa, data["name_fa"])

    def test_referenced_catalog_term_cannot_be_deleted(self):
        category = Category.objects.create(
            name="Floor Tile", name_fa="کاشی کف", slug="floor-tile"
        )
        product = Product.objects.create(name="Atlas", slug="atlas", sku="ATLAS")
        product.categories.add(category)

        with translation.override("fa"):
            delete_url = reverse(
                "dashboard:products:catalog_term_delete", args=["categories", category.pk]
            )
            fa_list_url = reverse(
                "dashboard:products:catalog_term_list", args=["categories"]
            )
            response = self.client.get(delete_url)
            self.assertContains(response, "حذف")
            response = self.client.post(delete_url)

        self.assertTrue(Category.objects.filter(pk=category.pk).exists())
        self.assertRedirects(
            response,
            fa_list_url,
        )

    def test_catalog_and_product_gallery_render_in_both_locales(self):
        product = Product.objects.create(
            name="Gallery Tile", name_fa="کاشی گالری", slug="gallery-tile", sku="GALLERY-TILE"
        )

        with translation.override("en"):
            catalog_url = reverse("dashboard:products:catalog_data")
            detail_url = reverse("dashboard:products:detail", args=[product.pk])
            catalog_response = self.client.get(catalog_url)
            detail_response = self.client.get(detail_url)
        self.assertContains(catalog_response, "Catalog Data")
        self.assertContains(detail_response, "Product Gallery")
        self.assertContains(detail_response, "Add Gallery Images")

        with translation.override("fa"):
            catalog_url = reverse("dashboard:products:catalog_data")
            detail_url = reverse("dashboard:products:detail", args=[product.pk])
            catalog_response = self.client.get(catalog_url)
            detail_response = self.client.get(detail_url)
        self.assertContains(catalog_response, "داده‌های کاتالوگ")
        self.assertContains(detail_response, "گالری محصول")
        self.assertContains(detail_response, "افزودن تصاویر گالری")


class DashboardVariantWorkflowTests(TestCase):
    def setUp(self):
        call_command("seed_dashboard_roles", verbosity=0)
        self.user = get_user_model().objects.create_user(
            username="staff", password="secret"
        )
        self.user.groups.add(Group.objects.get(name="Product Manager"))
        self.product = Product.objects.create(
            name="Atlas Stone", name_fa="اطلس سنگ", slug="atlas-stone", sku="ATLAS-STONE"
        )
        self.other_product = Product.objects.create(
            name="Other", slug="other", sku="OTHER"
        )
        self.color = Color.objects.create(name="White", slug="white")
        self.other_color = Color.objects.create(name="Grey", slug="grey")
        self.size = Size.objects.create(name="60x60", slug="60x60")
        self.other_size = Size.objects.create(name="80x80", slug="80x80")
        self.finish = Finish.objects.create(name="Matte", slug="matte")
        self.other_finish = Finish.objects.create(name="Gloss", slug="gloss")
        apply_tile_catalog()

    def variant_data(self, **overrides):
        data = {
            "sku": "ATLAS-STONE-WHITE-6060",
            "color": str(self.color.pk),
            "size": str(self.size.pk),
            "finish": str(self.finish.pk),
            "usd_amount": "120.00",
            "usd_compare_at_amount": "150.00",
            "irt_amount": "3500000",
            "price_unit": "box",
            "stock": "12",
            "sort_order": "0",
            "is_active": "on",
        }
        data.update(overrides)
        return data

    def test_variant_form_uses_controlled_relational_selectors(self):
        form = DashboardProductVariantForm(data=self.variant_data())
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["color"], self.color)
        self.assertEqual(form.cleaned_data["size"], self.size)
        self.assertEqual(form.cleaned_data["finish"], self.finish)

    def test_staff_can_create_and_edit_variant_for_product(self):
        self.client.force_login(self.user)
        create_url = reverse("dashboard:products:variants:create", args=[self.product.pk])
        response = self.client.post(create_url, self.variant_data())
        self.assertRedirects(response, reverse("dashboard:products:variants:catalog", args=[self.product.pk]))
        variant = ProductVariant.objects.get(sku="ATLAS-STONE-WHITE-6060")
        self.assertEqual(variant.product, self.product)
        self.assertEqual(variant.stock, 12)

        response = self.client.post(
            reverse("dashboard:products:variants:edit", args=[self.product.pk, variant.pk]),
            self.variant_data(usd_amount="130.00", irt_amount="4000000", stock="9"),
        )
        self.assertRedirects(response, reverse("dashboard:products:variants:catalog", args=[self.product.pk]))
        variant.refresh_from_db()
        self.assertEqual(str(variant.prices.get(currency=Currency.USD).amount), "130.00")
        self.assertEqual(str(variant.prices.get(currency=Currency.TOMAN).amount), "4000000.00")
        self.assertEqual(variant.stock, 9)
        event = ProductOperationalChange.objects.get(variant=variant)
        self.assertEqual(event.actor, self.user)
        self.assertEqual(event.source, ProductOperationalChange.Source.MANUAL)
        self.assertEqual(event.changed_fields, ["stock", "IRT.amount", "USD.amount"])

    def test_dashboard_currency_prices_are_independent(self):
        self.client.force_login(self.user)
        create_url = reverse("dashboard:products:variants:create", args=[self.product.pk])
        self.client.post(create_url, self.variant_data())
        variant = ProductVariant.objects.get(sku="ATLAS-STONE-WHITE-6060")
        self.client.post(
            reverse("dashboard:products:variants:edit", args=[self.product.pk, variant.pk]),
            self.variant_data(usd_amount="130.00", irt_amount="3500000"),
        )
        self.assertEqual(str(variant.prices.get(currency=Currency.USD).amount), "130.00")
        self.assertEqual(str(variant.prices.get(currency=Currency.TOMAN).amount), "3500000.00")

    def test_pricing_and_inventory_stay_locale_independent(self):
        self.client.force_login(self.user)
        self.client.post(
            reverse("dashboard:products:variants:create", args=[self.product.pk]),
            self.variant_data(irt_compare_at_amount="4000000"),
        )
        variant = ProductVariant.objects.get(sku="ATLAS-STONE-WHITE-6060")
        with translation.override("en"):
            en_edit_url = reverse(
                "dashboard:products:variants:edit", args=[self.product.pk, variant.pk]
            )
            en_catalog_url = reverse(
                "dashboard:products:variants:catalog", args=[self.product.pk]
            )
            en_detail_url = reverse("dashboard:products:detail", args=[self.product.pk])

        with translation.override("fa"):
            fa_edit_url = reverse(
                "dashboard:products:variants:edit", args=[self.product.pk, variant.pk]
            )
            fa_catalog_url = reverse(
                "dashboard:products:variants:catalog", args=[self.product.pk]
            )
            fa_detail_url = reverse("dashboard:products:detail", args=[self.product.pk])
            response = self.client.get(fa_edit_url)
            self.assertContains(response, "قیمت‌گذاری")
            self.assertContains(response, "موجودی")
            self.assertContains(response, "تومان")
            detail_response = self.client.get(fa_detail_url)
            self.assertContains(detail_response, "قیمت‌گذاری بر اساس گونه‌ها")
            self.assertContains(detail_response, "مجموع موجودی فعال")
            response = self.client.post(
                fa_edit_url,
                self.variant_data(
                    usd_amount="130.00",
                    usd_compare_at_amount="160.00",
                    irt_amount="3500000",
                    irt_compare_at_amount="4000000",
                    stock="9",
                ),
            )
        self.assertRedirects(
            response,
            fa_catalog_url,
        )
        variant.refresh_from_db()
        self.assertEqual(variant.stock, 9)
        self.assertEqual(str(variant.prices.get(currency=Currency.USD).amount), "130.00")
        self.assertEqual(
            str(variant.prices.get(currency=Currency.USD).compare_at_amount), "160.00"
        )
        self.assertEqual(str(variant.prices.get(currency=Currency.TOMAN).amount), "3500000.00")
        self.assertEqual(
            str(variant.prices.get(currency=Currency.TOMAN).compare_at_amount),
            "4000000.00",
        )

        with translation.override("en"):
            response = self.client.post(
                en_edit_url,
                self.variant_data(
                    usd_amount="130.00",
                    usd_compare_at_amount="160.00",
                    irt_amount="3600000",
                    irt_compare_at_amount="4000000",
                    stock="9",
                ),
            )
        self.assertRedirects(
            response,
            en_catalog_url,
        )
        variant.refresh_from_db()
        self.assertEqual(str(variant.prices.get(currency=Currency.USD).amount), "130.00")
        self.assertEqual(str(variant.prices.get(currency=Currency.TOMAN).amount), "3600000.00")

        with translation.override("en"):
            detail_response = self.client.get(en_detail_url)
        self.assertContains(detail_response, "Derived Variant Pricing")
        self.assertContains(detail_response, "Total Active Stock")

        response = self.client.post(
            en_edit_url,
            self.variant_data(
                usd_amount="",
                usd_compare_at_amount="",
                irt_amount="3600000",
                irt_compare_at_amount="4000000",
                stock="9",
            ),
        )
        self.assertRedirects(
            response,
            en_catalog_url,
        )
        self.assertFalse(variant.prices.filter(currency=Currency.USD).exists())
        self.assertEqual(
            str(variant.prices.get(currency=Currency.TOMAN).compare_at_amount),
            "4000000.00",
        )

        response = self.client.post(
            en_edit_url,
            self.variant_data(
                usd_amount="",
                usd_compare_at_amount="",
                irt_amount="3600000",
                irt_compare_at_amount="4000000",
                stock="-1",
            ),
        )
        self.assertEqual(response.status_code, 200)
        variant.refresh_from_db()
        self.assertEqual(variant.stock, 9)

    def test_edit_updates_variant_fields_without_destroying_currency_prices(self):
        self.client.force_login(self.user)
        self.client.post(reverse("dashboard:products:variants:create", args=[self.product.pk]), self.variant_data())
        variant = ProductVariant.objects.get(sku="ATLAS-STONE-WHITE-6060")
        response = self.client.post(
            reverse("dashboard:products:variants:edit", args=[self.product.pk, variant.pk]),
            self.variant_data(
                color=str(self.other_color.pk),
                size=str(self.other_size.pk),
                finish=str(self.other_finish.pk),
                stock="24",
                tiles_per_box="6",
                coverage_per_box_m2="2.400",
                price_unit="m2",
                usd_amount="120.00",
                irt_amount="3500000",
            ),
        )
        self.assertRedirects(response, reverse("dashboard:products:variants:catalog", args=[self.product.pk]))
        variant.refresh_from_db()
        self.assertEqual(variant.color, self.other_color)
        self.assertEqual(variant.size, self.other_size)
        self.assertEqual(variant.finish, self.other_finish)
        self.assertEqual(variant.stock, 24)
        self.assertEqual(variant.tiles_per_box, 6)
        self.assertEqual(str(variant.coverage_per_box_m2), "2.400")
        self.assertEqual(variant.price_unit, ProductVariant.PriceUnit.M2)
        self.assertEqual(str(variant.prices.get(currency=Currency.USD).amount), "120.00")
        self.assertEqual(str(variant.prices.get(currency=Currency.TOMAN).amount), "3500000.00")

    def test_invalid_currency_price_does_not_partially_update_variant(self):
        self.client.force_login(self.user)
        self.client.post(reverse("dashboard:products:variants:create", args=[self.product.pk]), self.variant_data(stock="12"))
        variant = ProductVariant.objects.get(sku="ATLAS-STONE-WHITE-6060")
        response = self.client.post(
            reverse("dashboard:products:variants:edit", args=[self.product.pk, variant.pk]),
            self.variant_data(color=str(self.other_color.pk), stock="99", usd_amount="130.00", irt_amount="3500000.50"),
        )
        self.assertEqual(response.status_code, 200)
        variant.refresh_from_db()
        self.assertEqual(variant.color, self.color)
        self.assertEqual(variant.stock, 12)
        self.assertEqual(str(variant.prices.get(currency=Currency.USD).amount), "120.00")
        self.assertEqual(str(variant.prices.get(currency=Currency.TOMAN).amount), "3500000.00")

    def test_products_navigation_and_product_management_routes_are_reachable(self):
        self.client.force_login(self.user)
        catalog_url = reverse("dashboard:products:catalog")
        response = self.client.get(catalog_url)
        self.assertContains(response, "All Products")
        self.assertContains(response, reverse("dashboard:products:create"))

        response = self.client.get(
            reverse("dashboard:products:variants:catalog", args=[self.product.pk])
        )
        self.assertContains(response, "Add Variant")

    def test_variant_management_routes_render_localized_static_ui(self):
        self.client.force_login(self.user)
        variant = ProductVariant.objects.create(
            product=self.product,
            sku="ATLAS-LOCALIZED-6060",
            stock=7,
        )

        with translation.override("en"):
            catalog_response = self.client.get(
                reverse("dashboard:products:variants:catalog", args=[self.product.pk])
            )
            create_response = self.client.get(
                reverse("dashboard:products:variants:create", args=[self.product.pk])
            )
            edit_response = self.client.get(
                reverse(
                    "dashboard:products:variants:edit",
                    args=[self.product.pk, variant.pk],
                )
            )

        self.assertContains(catalog_response, "Manage SKU-level inventory")
        self.assertContains(create_response, "Variant Details")
        self.assertContains(edit_response, "Save Variant")
        self.assertContains(edit_response, 'dir="ltr"')

        with translation.override("fa"):
            catalog_response = self.client.get(
                reverse("dashboard:products:variants:catalog", args=[self.product.pk])
            )
            create_response = self.client.get(
                reverse("dashboard:products:variants:create", args=[self.product.pk])
            )
            edit_response = self.client.get(
                reverse(
                    "dashboard:products:variants:edit",
                    args=[self.product.pk, variant.pk],
                )
            )

        self.assertContains(catalog_response, "افزودن گونه")
        self.assertContains(create_response, "جزئیات گونه")
        self.assertContains(edit_response, "ذخیرهٔ گونه")
        self.assertContains(edit_response, 'dir="rtl"')

    def test_staff_can_create_product_with_persian_name(self):
        self.client.force_login(self.user)
        response = self.client.post(
            reverse("dashboard:products:create"),
            {
                "name": "Persian Product",
                "name_fa": "محصول فارسی",
                "slug": "persian-product",
                "sku": "PERSIAN-PRODUCT",
                "low_stock_threshold": "5",
                "is_active": "on",
            },
        )
        product = Product.objects.get(sku="PERSIAN-PRODUCT")
        self.assertRedirects(response, reverse("dashboard:products:detail", args=[product.pk]))
        self.assertEqual(product.name_fa, "محصول فارسی")

    def test_product_detail_exposes_variant_management(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse("dashboard:products:detail", args=[self.product.pk]))
        self.assertContains(response, "Add Variant")
        self.assertContains(
            response,
            reverse("dashboard:products:variants:create", args=[self.product.pk]),
        )

    def test_dashboard_inventory_uses_active_variant_stock_and_notification_url(self):
        ProductVariant.objects.create(
            product=self.product,
            sku="ATLAS-LOW",
            stock=3,
            low_stock_threshold=5,
            is_active=True,
        )
        ProductVariant.objects.create(
            product=self.product,
            sku="ATLAS-INACTIVE",
            stock=99,
            is_active=False,
        )
        listed_product = get_products().get(pk=self.product.pk)
        self.assertEqual(listed_product.variant_stock, 3)

        notification = next(
            item for item in get_dashboard_notifications()
            if "ATLAS-LOW" in item["message"]
        )
        self.assertEqual(
            notification["url"],
            reverse("dashboard:products:variants:edit", args=[self.product.pk, ProductVariant.objects.get(sku="ATLAS-LOW").pk]),
        )

    def test_localized_dashboard_shows_notifications_and_inventory_links(self):
        variant = ProductVariant.objects.create(
            product=self.product,
            sku="ATLAS-DASHBOARD-LOW",
            stock=3,
            low_stock_threshold=5,
            is_active=True,
        )
        owner = get_user_model().objects.create_superuser(
            username="inventory-dashboard-owner", email="inventory@example.com", password="secret"
        )
        self.client.force_login(owner)

        with translation.override("fa"):
            response = self.client.get(reverse("dashboard:home"))
            product_url = reverse("dashboard:products:detail", args=[self.product.pk])
            variant_url = reverse(
                "dashboard:products:variants:edit",
                args=[self.product.pk, variant.pk],
            )

        self.assertContains(response, "موجودی کم")
        self.assertContains(response, product_url)
        self.assertContains(response, variant_url)

    def test_variant_cannot_be_edited_through_a_different_product_url(self):
        variant = ProductVariant.objects.create(product=self.product, sku="ATLAS-OWNED")
        self.client.force_login(self.user)
        response = self.client.get(
            reverse("dashboard:products:variants:edit", args=[self.other_product.pk, variant.pk])
        )
        self.assertEqual(response.status_code, 404)


class DashboardSummaryCardTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser(
            username="dashboard-summary-owner", email="summary@example.com", password="secret"
        )
        self.client.force_login(self.user)

    def test_dashboard_summary_cards_render_statistics_values(self):
        base_order = {
            "user": self.user,
            "full_name": "Dashboard Buyer",
            "address": "1 Tile Street",
            "city": "Tehran",
            "phone": "123",
            "currency": Currency.USD,
        }
        Order.objects.create(
            **base_order,
            status=Order.Status.CONFIRMED,
            is_paid=True,
            total_amount=Decimal("20.00"),
        )
        Order.objects.create(
            **base_order,
            status=Order.Status.SHIPPED,
            is_paid=True,
            total_amount=Decimal("30.00"),
        )
        Order.objects.create(
            **base_order,
            status=Order.Status.DELIVERED,
            is_paid=True,
            refund_status=Order.RefundStatus.REQUESTED,
            total_amount=Decimal("40.00"),
        )

        with translation.override("en"):
            response = self.client.get(reverse("dashboard:home"))

        # F3H composes Dashboard Home from only the actor's capabilities.
        # An Owner sees operational queue cards rather than the retired
        # staff-wide revenue summary card.
        self.assertContains(response, "Order operations")
        self.assertContains(response, "Fulfillment: 1")
        self.assertContains(response, "In transit: 1")
        self.assertContains(response, "Refund operations")
        self.assertContains(response, "Requests: 1")


class DashboardOrderDetailLocalizationTests(TestCase):
    def setUp(self):
        call_command("seed_dashboard_roles", verbosity=0)
        self.staff = get_user_model().objects.create_superuser(
            username="dashboard-order-owner", email="order-owner@example.com", password="secret"
        )
        self.customer = get_user_model().objects.create_user(
            username="dashboard-order-customer", password="secret"
        )
        self.order = Order.objects.create(
            user=self.customer,
            full_name="Dashboard Buyer",
            address="1 Tile Street",
            city="Tehran",
            phone="123",
            currency=Currency.USD,
            payment_method=Order.PaymentMethod.MOCK,
            payment_provider="Development Gateway",
            payment_reference="MOCK-REF-100001",
            is_paid=True,
            status=Order.Status.CONFIRMED,
        )
        OrderStatusHistory.objects.create(
            order=self.order,
            status=Order.Status.PENDING,
            note="Order created, awaiting payment.",
        )
        OrderStatusHistory.objects.create(
            order=self.order,
            status=Order.Status.CONFIRMED,
            note="Online payment confirmed.",
        )
        OrderNote.objects.create(
            order=self.order,
            visibility=OrderNote.Visibility.INTERNAL,
            category=OrderNote.Category.PAYMENT,
            status=Order.Status.CONFIRMED,
            operation_code="ONLINE_PAYMENT_RECORDED",
            message="Payment recorded via Development Gateway. Reference: MOCK-REF-100001",
        )
        self.client.force_login(self.staff)

    def test_order_detail_localizes_system_timeline_notes_and_payment_provider(self):
        with translation.override("fa"):
            response = self.client.get(
                reverse("dashboard:order_detail", args=[self.order.id])
            )

        self.assertContains(response, "سفارش ایجاد شد و در انتظار پرداخت است.")
        self.assertContains(response, "پرداخت آنلاین تأیید شد.")
        self.assertContains(response, "پرداخت از طریق پرداخت آزمایشی تأیید شد.")
        self.assertContains(response, "پرداخت آزمایشی")
        self.assertNotContains(response, "Development Gateway")
        self.assertNotContains(response, "Payment recorded via")


class DashboardShellLocalizationTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser(
            username="dashboard-owner", email="shell@example.com", password="secret"
        )
        self.client.force_login(self.user)

    def test_dashboard_shell_is_english_ltr(self):
        with translation.override("en"):
            response = self.client.get(reverse("dashboard:home"))

        self.assertContains(response, 'lang="en" dir="ltr"')
        self.assertContains(response, "Operations Dashboard")
        self.assertContains(response, "Open dashboard navigation")
        self.assertContains(response, 'role="dialog"')
        self.assertContains(response, 'data-dashboard-menu-toggle')
        self.assertContains(response, 'class="dashboard-shell')

    def test_dashboard_shell_is_persian_rtl(self):
        with translation.override("fa"):
            response = self.client.get(reverse("dashboard:home"))

        self.assertContains(response, 'lang="fa" dir="rtl"')
        self.assertContains(response, "داشبورد عملیات")
        self.assertContains(response, "پشتیبانی")

    def test_dashboard_mobile_drawer_contract_is_available_to_staff(self):
        with translation.override("en"):
            response = self.client.get(reverse("dashboard:all_orders"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="dashboard-mobile-menu"')
        self.assertContains(response, 'aria-modal="true"')
        self.assertContains(response, 'id="dashboard-mobile-overlay"')
