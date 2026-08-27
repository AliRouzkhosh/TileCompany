from products.models import AttributeOption, Brand, Category, HomepageEditorialSelection, HomepageHero, HomepageMerchandisingMedia


def get_homepage_hero_configuration():
    return HomepageHero.objects.filter(key=HomepageHero.PRIMARY_KEY).first()


def get_homepage_editorial_configuration():
    return HomepageEditorialSelection.objects.select_related("article").filter(
        key=HomepageEditorialSelection.PRIMARY_KEY
    ).first()


def get_storefront_media_categories():
    """All categories, deterministically ordered, with no related-object reads."""

    return Category.objects.order_by("sort_order", "name", "id")


def get_homepage_merchandising_media():
    return HomepageMerchandisingMedia.objects.select_related("category", "attribute_option", "brand").order_by("updated_at", "id")


def get_merchandising_targets():
    return {
        "categories": Category.objects.order_by("sort_order", "name", "id"),
        "options": AttributeOption.objects.filter(is_active=True).select_related("definition").order_by("definition__name", "sort_order", "name", "id"),
        "brands": Brand.objects.order_by("name", "id"),
    }
