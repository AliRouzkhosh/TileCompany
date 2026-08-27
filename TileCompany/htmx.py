"""Small request helpers for progressively enhanced storefront views."""

from django.shortcuts import render


def is_htmx(request):
    """Return whether a request was issued by the locally-vendored HTMX runtime."""
    return (
        request.headers.get("HX-Request") == "true"
        and not is_htmx_history_restore(request)
    )


def is_htmx_history_restore(request):
    """History cache misses must use the normal full-page response authority."""
    return request.headers.get("HX-History-Restore-Request") == "true"


def htmx_target(request):
    """Return the explicit replacement target requested by HTMX, if any."""
    return request.headers.get("HX-Target", "") if is_htmx(request) else ""


def is_htmx_target(request, target):
    return htmx_target(request) == target


def render_page_or_fragment(request, page_template, fragment_template, context):
    """Use the same server context for a full page or its bounded GET workspace."""
    return render(request, fragment_template if is_htmx(request) else page_template, context)
