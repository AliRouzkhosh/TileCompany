from django.contrib import messages
from django.http import HttpResponseNotAllowed
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext as _

from dashboard.forms_storefront import CategoryStorefrontImageForm, HomepageEditorialSelectionForm, HomepageHeroForm, HomepageMerchandisingMediaForm, HomepageMerchandisingMediaUpdateForm
from dashboard.permissions import dashboard_permission_required
from dashboard.selectors.storefront import (
    get_homepage_hero_configuration,
    get_homepage_editorial_configuration,
    get_storefront_media_categories,
    get_homepage_merchandising_media,
    get_merchandising_targets,
)
from dashboard.services_storefront import (
    save_category_storefront_image,
    save_homepage_hero,
    save_homepage_editorial_selection,
    save_homepage_merchandising_media,
    update_homepage_merchandising_media,
)
from products.models import Category, HomepageHero, HomepageMerchandisingMedia


@dashboard_permission_required("products.manage_storefront_media")
def media(request):
    hero = get_homepage_hero_configuration()
    editorial = get_homepage_editorial_configuration()
    merchandising_media = list(get_homepage_merchandising_media())
    for item in merchandising_media:
        item.edit_form = HomepageMerchandisingMediaUpdateForm(instance=item)
    return render(
        request,
        "dashboard/storefront/media.html",
        {
            "hero": hero,
            "hero_form": HomepageHeroForm(instance=hero or HomepageHero()),
            "editorial_form": HomepageEditorialSelectionForm(instance=editorial),
            "categories": get_storefront_media_categories(),
            "merchandising_form": HomepageMerchandisingMediaForm(),
            "merchandising_media": merchandising_media,
            **get_merchandising_targets(),
        },
    )


@dashboard_permission_required("products.manage_storefront_media")
def update_editorial(request):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    editorial = get_homepage_editorial_configuration()
    form = HomepageEditorialSelectionForm(request.POST, instance=editorial)
    if form.is_valid():
        save_homepage_editorial_selection(form)
        messages.success(request, _("Homepage featured article updated."))
        return redirect("dashboard:storefront:media")
    hero = get_homepage_hero_configuration()
    merchandising_media = list(get_homepage_merchandising_media())
    for item in merchandising_media:
        item.edit_form = HomepageMerchandisingMediaUpdateForm(instance=item)
    return render(request, "dashboard/storefront/media.html", {
        "hero": hero,
        "hero_form": HomepageHeroForm(instance=hero or HomepageHero()),
        "editorial_form": form,
        "categories": get_storefront_media_categories(),
        "merchandising_form": HomepageMerchandisingMediaForm(),
        "merchandising_media": merchandising_media,
        **get_merchandising_targets(),
    }, status=400)


@dashboard_permission_required("products.manage_storefront_media")
def update_hero(request):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    hero = get_homepage_hero_configuration()
    form = HomepageHeroForm(
        request.POST,
        request.FILES,
        instance=hero or HomepageHero(),
    )
    if form.is_valid():
        save_homepage_hero(form)
        messages.success(request, _("Homepage Hero media updated."))
        return redirect("dashboard:storefront:media")
    return render(
        request,
        "dashboard/storefront/media.html",
        {
            "hero": hero,
            "hero_form": form,
            "editorial_form": HomepageEditorialSelectionForm(instance=get_homepage_editorial_configuration()),
            "categories": get_storefront_media_categories(),
            "merchandising_form": HomepageMerchandisingMediaForm(),
            "merchandising_media": get_homepage_merchandising_media(),
            **get_merchandising_targets(),
        },
        status=400,
    )


@dashboard_permission_required("products.manage_storefront_media")
def update_category(request, pk):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    category = get_object_or_404(Category, pk=pk)
    form = CategoryStorefrontImageForm(request.POST, request.FILES)
    if form.is_valid():
        save_category_storefront_image(category, form)
        messages.success(
            request,
            _("Storefront image updated for %(category)s.") % {"category": category.name},
        )
    else:
        messages.error(request, " ".join(form.non_field_errors() or form.errors.get("image", [])))
    return redirect("dashboard:storefront:media")


@dashboard_permission_required("products.manage_storefront_media")
def update_merchandising_media(request):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    form = HomepageMerchandisingMediaForm(request.POST, request.FILES)
    if form.is_valid():
        save_homepage_merchandising_media(form)
        messages.success(request, _("Homepage discovery media updated."))
    else:
        messages.error(request, " ".join(form.non_field_errors() or form.errors.get("image", [])))
    return redirect("dashboard:storefront:media")


@dashboard_permission_required("products.manage_storefront_media")
def update_existing_merchandising_media(request, pk):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    media = get_object_or_404(HomepageMerchandisingMedia, pk=pk)
    form = HomepageMerchandisingMediaUpdateForm(request.POST, request.FILES, instance=media)
    if form.is_valid():
        update_homepage_merchandising_media(media, form)
        messages.success(request, _("Homepage discovery media updated."))
    else:
        messages.error(request, " ".join(form.non_field_errors() or form.errors.get("image", [])))
    return redirect("dashboard:storefront:media")
