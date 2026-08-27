from .storefront_navigation import build_storefront_navigation


def storefront_navigation(request):
    return {"storefront_navigation": build_storefront_navigation(request)}
