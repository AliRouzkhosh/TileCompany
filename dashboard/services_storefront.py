from django.db import transaction

from products.models import Category, HomepageEditorialSelection, HomepageHero, HomepageMerchandisingMedia


def _delete_replaced_file_on_commit(old_file, new_name=""):
    if not old_file or not old_file.name or old_file.name == new_name:
        return
    storage = old_file.storage
    old_name = old_file.name
    transaction.on_commit(lambda: storage.delete(old_name))


@transaction.atomic
def save_homepage_hero(form):
    """Create or update the one authoritative Hero configuration."""

    hero, _ = HomepageHero.objects.select_for_update().get_or_create(
        key=HomepageHero.PRIMARY_KEY,
    )
    old_image = hero.image
    replacement = form.files.get("image")
    remove = form.cleaned_data.get("remove_image", False)

    hero.image_alt = form.cleaned_data["image_alt"].strip()
    hero.image_alt_fa = form.cleaned_data["image_alt_fa"].strip()
    hero.is_enabled = form.cleaned_data["is_enabled"]
    if remove:
        hero.image = None
    elif replacement:
        hero.image = replacement
    hero.full_clean()
    hero.save()

    if remove or replacement:
        _delete_replaced_file_on_commit(old_image, hero.image.name if hero.image else "")
    return hero


@transaction.atomic
def save_homepage_editorial_selection(form):
    selection, _ = HomepageEditorialSelection.objects.select_for_update().get_or_create(
        key=HomepageEditorialSelection.PRIMARY_KEY,
    )
    selection.article = form.cleaned_data.get("article")
    selection.full_clean()
    selection.save(update_fields=("article", "updated_at"))
    return selection


@transaction.atomic
def save_category_storefront_image(category, form):
    """Replace or clear presentation media without changing Category semantics."""

    category = Category.objects.select_for_update().get(pk=category.pk)
    old_image = category.storefront_image
    replacement = form.cleaned_data.get("image")
    remove = form.cleaned_data.get("remove_image", False)

    category.storefront_image = None if remove else replacement
    category.full_clean()
    category.save(update_fields=["storefront_image"])
    _delete_replaced_file_on_commit(
        old_image,
        category.storefront_image.name if category.storefront_image else "",
    )
    return category


@transaction.atomic
def save_homepage_merchandising_media(form):
    targets = {name: form.cleaned_data.get(name) for name in ("category", "attribute_option", "brand")}
    selected = {name: value for name, value in targets.items() if value is not None}
    # The form has already established exactly one target.  Lookup by that
    # canonical FK identity so an operator creates-or-updates one record rather
    # than relying on ModelForm uniqueness validation for an empty instance.
    media = HomepageMerchandisingMedia.objects.select_for_update().filter(**selected).first()
    if media is None:
        media = HomepageMerchandisingMedia(**selected)
    old_image = media.image
    replacement = form.cleaned_data.get("image")
    media.image_alt = form.cleaned_data["image_alt"].strip()
    media.image_alt_fa = form.cleaned_data["image_alt_fa"].strip()
    media.is_active = form.cleaned_data["is_active"]
    if form.cleaned_data.get("remove_image"):
        media.image = None
    elif replacement:
        media.image = replacement
    media.full_clean()
    media.save()
    if replacement or form.cleaned_data.get("remove_image"):
        _delete_replaced_file_on_commit(old_image, media.image.name if media.image else "")
    return media


@transaction.atomic
def update_homepage_merchandising_media(media, form):
    """Replace/remove presentation media without ever retargeting the row."""

    media = HomepageMerchandisingMedia.objects.select_for_update().get(pk=media.pk)
    old_image = media.image
    replacement = form.cleaned_data.get("image")
    remove = form.cleaned_data.get("remove_image", False)

    media.image_alt = form.cleaned_data["image_alt"].strip()
    media.image_alt_fa = form.cleaned_data["image_alt_fa"].strip()
    media.is_active = form.cleaned_data["is_active"]
    if remove:
        media.image = None
    elif replacement:
        media.image = replacement
    media.full_clean()
    media.save()

    if replacement or remove:
        _delete_replaced_file_on_commit(old_image, media.image.name if media.image else "")
    return media
