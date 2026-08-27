"""Small project-owned browser-security response-header boundary."""

from django.conf import settings


class BrowserSecurityHeadersMiddleware:
    """Add headers that Django does not expose as first-class settings."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if settings.BROWSER_SECURITY_HEADERS_ENABLED:
            response.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
            response.setdefault(
                "Content-Security-Policy",
                settings.CONTENT_SECURITY_POLICY,
            )
        return response
