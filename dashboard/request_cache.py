"""Per-request reuse for permission-scoped Dashboard computations."""


REQUEST_CACHE_ATTRIBUTE = "_dashboard_prepared_values"


def get_request_value(request, key, loader):
    """Return a value prepared earlier in this same request, or compute it."""
    if request is None:
        return loader()
    cache = getattr(request, REQUEST_CACHE_ATTRIBUTE, None)
    if cache is None:
        cache = {}
        setattr(request, REQUEST_CACHE_ATTRIBUTE, cache)
    if key not in cache:
        cache[key] = loader()
    return cache[key]


def set_request_value(request, key, value):
    """Publish a value already computed by the current view."""
    if request is None:
        return value
    cache = getattr(request, REQUEST_CACHE_ATTRIBUTE, None)
    if cache is None:
        cache = {}
        setattr(request, REQUEST_CACHE_ATTRIBUTE, cache)
    cache[key] = value
    return value
