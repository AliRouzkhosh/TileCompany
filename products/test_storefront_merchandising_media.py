import shutil
import tempfile
import inspect
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.template.loader import render_to_string
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.contrib.auth.models import Group
from io import BytesIO
from PIL import Image

from products.models import (
    AttributeDefinition,
    AttributeOption,
    Brand,
    Category,
    HomepageHero,
    HomepageMerchandisingMedia,
    Product,
    ProductType,
)
from dashboard.management.commands.seed_dashboard_roles import Command as SeedDashboardRolesCommand
from products.selectors import get_home_categories, get_homepage_hero


class StorefrontMerchandisingMediaTests(TestCase):
    def setUp(self):
        self.media_root = tempfile.mkdtemp(prefix="storefront-media-tests-")
        self.media_override = override_settings(MEDIA_ROOT=self.media_root)
        self.media_override.enable()
        self.staff = get_user_model().objects.create_user(
            username="merchandiser",
            password="test-password",
            is_staff=True,
        )
        SeedDashboardRolesCommand().handle()
        self.staff.groups.add(Group.objects.get(name="Content Manager"))

    def tearDown(self):
        self.media_override.disable()
        shutil.rmtree(self.media_root, ignore_errors=True)

    def upload(self, name):
        image = Image.new("RGB", (1, 1), "white")
        payload = BytesIO()
        image.save(payload, format="PNG")
        return SimpleUploadedFile(name, payload.getvalue(), content_type="image/png")

    def make_category_product(self, category, *, image="products/representative.jpg", featured=True):
        product_type = ProductType.objects.create(
            code=f"generic-{ProductType.objects.count()}",
            name="Generic",
        )
        product = Product.objects.create(
            product_type=product_type,
            name="Representative product",
            name_fa="محصول نماینده",
            slug=f"representative-{Product.objects.count()}",
            sku=f"REP-{Product.objects.count()}",
            image=image,
            is_featured=featured,
            is_active=True,
        )
        product.categories.add(category)
        return product

    def test_category_image_precedence_preserves_product_and_geometric_fallbacks(self):
        category = Category.objects.create(
            name="Mobile Devices",
            name_fa="دستگاه‌های همراه",
            slug="mobile-devices",
            storefront_image="storefront/categories/operator.jpg",
        )
        product = self.make_category_product(category)

        with self.assertNumQueries(1):
            card = get_home_categories()[0]
        self.assertEqual(card.image_url, "/media/storefront/categories/operator.jpg")

        category.storefront_image = None
        category.save(update_fields=["storefront_image"])
        self.assertEqual(get_home_categories()[0].image_url, "/media/products/representative.jpg")

        product.is_active = False
        product.save(update_fields=["is_active"])
        fallback_card = get_home_categories()[0]
        self.assertEqual(fallback_card.image_url, "")
        with self.assertNumQueries(0):
            rendered = render_to_string(
                "products/components/category_card.html",
                {"category": fallback_card},
            )
        self.assertIn("home-media-fallback", rendered)

    def test_configured_hero_precedes_product_then_neutral_fallback(self):
        category = Category.objects.create(name="Laptop", slug="laptop")
        product = self.make_category_product(category, image="products/hero-product.jpg")
        HomepageHero.objects.create(
            image="storefront/homepage/operator.jpg",
            image_alt="Operator showroom",
            image_alt_fa="نمایشگاه اپراتور",
        )

        english = self.client.get("/en/")
        persian = self.client.get("/fa/")
        self.assertContains(english, "/media/storefront/homepage/operator.jpg")
        self.assertEqual(
            english.context["hero_image_url"],
            "/media/storefront/homepage/operator.jpg",
        )
        self.assertContains(english, 'alt="Operator showroom"')
        self.assertContains(persian, 'alt="نمایشگاه اپراتور"')

        HomepageHero.objects.update(is_enabled=False)
        product_fallback = self.client.get("/en/")
        self.assertContains(product_fallback, "/media/products/hero-product.jpg")

        product.image = None
        product.save(update_fields=["image"])
        neutral = self.client.get("/en/")
        self.assertContains(neutral, "Tile showroom image coming soon")

    def test_hero_selector_is_bounded_and_templates_do_not_read_models(self):
        HomepageHero.objects.create(
            image="storefront/homepage/bounded.jpg",
            image_alt="Bounded",
        )
        with self.assertNumQueries(1):
            hero = get_homepage_hero()
        with self.assertNumQueries(0):
            rendered = render_to_string(
                "products/components/hero.html",
                {"hero_image_url": hero.image_url, "hero_image_alt": hero.image_alt},
            )
        self.assertIn("bounded.jpg", rendered)

        with CaptureQueriesContext(connection) as queries:
            response = self.client.get("/en/")
        self.assertEqual(response.status_code, 200)
        self.assertLessEqual(len(queries), 24)

    def test_dashboard_authorization_and_arbitrary_category_listing(self):
        category = Category.objects.create(name="Accessories", slug="accessories")
        url = reverse("dashboard:storefront:media")
        anonymous = self.client.get(url)
        self.assertEqual(anonymous.status_code, 302)
        self.assertIn("login", anonymous.url)

        customer = get_user_model().objects.create_user("customer", password="password")
        self.client.force_login(customer)
        denied = self.client.get(url)
        self.assertEqual(denied.status_code, 403)

        self.client.force_login(self.staff)
        allowed = self.client.get(url)
        self.assertContains(allowed, category.name)
        self.assertContains(allowed, "Storefront Media")

    def test_operator_can_upload_replace_and_remove_category_image(self):
        category = Category.objects.create(name="Tablet", slug="tablet")
        self.client.force_login(self.staff)
        url = reverse("dashboard:storefront:update_category", args=[category.pk])

        self.client.post(url, {"image": self.upload("first.png")})
        category.refresh_from_db()
        first_name = category.storefront_image.name
        self.assertTrue(first_name.startswith("storefront/categories/"))

        self.client.post(url, {"image": self.upload("replacement.png")})
        category.refresh_from_db()
        self.assertNotEqual(category.storefront_image.name, first_name)
        self.assertIn("replacement", category.storefront_image.name)

        self.client.post(url, {"remove_image": "on"})
        category.refresh_from_db()
        self.assertFalse(category.storefront_image)

    def test_operator_can_upload_replace_remove_and_localize_hero(self):
        self.client.force_login(self.staff)
        url = reverse("dashboard:storefront:update_hero")
        base = {
            "image_alt": "Warm material showroom",
            "image_alt_fa": "نمایشگاه گرم متریال",
            "is_enabled": "on",
        }

        self.client.post(url, {**base, "image": self.upload("hero-first.png")})
        hero = HomepageHero.objects.get(key=HomepageHero.PRIMARY_KEY)
        first_name = hero.image.name
        self.assertEqual(hero.image_alt, base["image_alt"])
        self.assertEqual(hero.image_alt_fa, base["image_alt_fa"])

        self.client.post(url, {**base, "image": self.upload("hero-replacement.png")})
        hero.refresh_from_db()
        self.assertNotEqual(hero.image.name, first_name)
        self.assertIn("hero-replacement", hero.image.name)

        self.client.post(url, {**base, "remove_image": "on"})
        hero.refresh_from_db()
        self.assertFalse(hero.image)

    def test_discovery_media_create_replace_and_remove_preserve_each_canonical_target(self):
        category = Category.objects.create(name="Discovery Category", slug="discovery-category")
        definition = AttributeDefinition.objects.create(
            code="discovery-area",
            name="Discovery area",
            data_type=AttributeDefinition.DataType.OPTION,
        )
        option = AttributeOption.objects.create(
            definition=definition,
            code="kitchen",
            name="Kitchen",
        )
        brand = Brand.objects.create(name="Discovery Brand", slug="discovery-brand")
        self.client.force_login(self.staff)
        create_url = reverse("dashboard:storefront:update_merchandising_media")

        for target_name, target in (
            ("category", category),
            ("attribute_option", option),
            ("brand", brand),
        ):
            with self.subTest(target=target_name):
                response = self.client.post(
                    create_url,
                    {target_name: str(target.pk), "image": self.upload(f"{target_name}-first.png"), "is_active": "on"},
                )
                self.assertEqual(response.status_code, 302)
                media = HomepageMerchandisingMedia.objects.get(**{target_name: target})
                media_id = media.pk
                self.assertEqual(
                    sum(value is not None for value in (media.category_id, media.attribute_option_id, media.brand_id)),
                    1,
                )

                response = self.client.post(
                    reverse("dashboard:storefront:update_existing_merchandising_media", args=[media_id]),
                    {"image": self.upload(f"{target_name}-replacement.png"), "is_active": "on"},
                )
                self.assertEqual(response.status_code, 302)
                media.refresh_from_db()
                self.assertEqual(media.pk, media_id)
                self.assertEqual(getattr(media, f"{target_name}_id"), target.pk)
                self.assertEqual(
                    sum(value is not None for value in (media.category_id, media.attribute_option_id, media.brand_id)),
                    1,
                )
                self.assertEqual(HomepageMerchandisingMedia.objects.filter(**{target_name: target}).count(), 1)
                self.assertIn("replacement", media.image.name)

                response = self.client.post(
                    reverse("dashboard:storefront:update_existing_merchandising_media", args=[media_id]),
                    {"remove_image": "on", "is_active": "on"},
                )
                self.assertEqual(response.status_code, 302)
                media.refresh_from_db()
                self.assertFalse(media.image)
                self.assertEqual(getattr(media, f"{target_name}_id"), target.pk)

    def test_discovery_media_form_rejects_zero_or_multiple_targets_before_save(self):
        category = Category.objects.create(name="Form category", slug="form-category")
        brand = Brand.objects.create(name="Form brand", slug="form-brand")
        self.client.force_login(self.staff)
        url = reverse("dashboard:storefront:update_merchandising_media")

        zero = self.client.post(url, {"image": self.upload("zero.png"), "is_active": "on"})
        multiple = self.client.post(
            url,
            {"category": str(category.pk), "brand": str(brand.pk), "image": self.upload("multiple.png"), "is_active": "on"},
        )

        self.assertEqual(zero.status_code, 302)
        self.assertEqual(multiple.status_code, 302)
        self.assertFalse(HomepageMerchandisingMedia.objects.exists())

    def test_new_media_sources_have_no_category_slug_or_tile_authority_logic(self):
        paths = (
            "dashboard/forms_storefront.py",
            "dashboard/services_storefront.py",
            "dashboard/views/storefront.py",
            "dashboard/selectors/storefront.py",
            "products/selectors.py",
        )
        source = "\n".join(
            (Path(settings.BASE_DIR) / path).read_text(encoding="utf-8")
            for path in paths
            if path != "products/selectors.py"
        )
        source += inspect.getsource(get_home_categories)
        source += inspect.getsource(get_homepage_hero)
        self.assertNotIn("ProductType.objects.filter", source)
        self.assertNotIn("category__slug", source)
        self.assertNotIn("slug__in", source)
        self.assertNotIn('code="tile"', source)
