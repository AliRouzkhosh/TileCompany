from django.conf import settings
from django.http import HttpResponse
from django.middleware.csrf import CsrfViewMiddleware, get_token
from django.middleware.clickjacking import XFrameOptionsMiddleware
from django.middleware.security import SecurityMiddleware
from django.contrib.sessions.middleware import SessionMiddleware
from django.test import Client, RequestFactory, TestCase, override_settings
from django.views.defaults import server_error
from pathlib import Path

from .security import BrowserSecurityHeadersMiddleware


class BrowserSecurityHeaderTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()

    @override_settings(SECURE_SSL_REDIRECT=False)
    def test_development_http_is_not_redirected(self):
        response = SecurityMiddleware(lambda request: HttpResponse("ok"))(self.factory.get("/"))
        self.assertEqual(response.status_code, 200)

    @override_settings(SECURE_SSL_REDIRECT=True)
    def test_production_http_redirects_to_https(self):
        response = SecurityMiddleware(lambda request: HttpResponse("ok"))(self.factory.get("/"))
        self.assertEqual(response.status_code, 301)
        self.assertEqual(response["Location"], "https://testserver/")

    @override_settings(
        SECURE_HSTS_SECONDS=3600,
        SECURE_HSTS_INCLUDE_SUBDOMAINS=False,
        SECURE_HSTS_PRELOAD=False,
    )
    def test_https_response_gets_staged_hsts(self):
        response = SecurityMiddleware(lambda request: HttpResponse("ok"))(self.factory.get("/", secure=True))
        self.assertEqual(response["Strict-Transport-Security"], "max-age=3600")

    @override_settings(
        BROWSER_SECURITY_HEADERS_ENABLED=True,
        CONTENT_SECURITY_POLICY="default-src 'self'; object-src 'none'; base-uri 'self'; frame-ancestors 'none'; script-src 'self'",
        SECURE_CONTENT_TYPE_NOSNIFF=True,
        SECURE_REFERRER_POLICY="strict-origin-when-cross-origin",
        X_FRAME_OPTIONS="DENY",
    )
    def test_production_browser_headers_are_emitted(self):
        response = BrowserSecurityHeadersMiddleware(lambda request: HttpResponse("ok"))(self.factory.get("/"))
        response = SecurityMiddleware(
            XFrameOptionsMiddleware(lambda request: response)
        )(self.factory.get("/", secure=True))
        self.assertEqual(response["Permissions-Policy"], "camera=(), microphone=(), geolocation=()")
        self.assertEqual(response["Content-Security-Policy"], "default-src 'self'; object-src 'none'; base-uri 'self'; frame-ancestors 'none'; script-src 'self'")
        self.assertEqual(response["X-Content-Type-Options"], "nosniff")
        self.assertEqual(response["X-Frame-Options"], "DENY")
        self.assertEqual(response["Referrer-Policy"], "strict-origin-when-cross-origin")

    @override_settings(
        BROWSER_SECURITY_HEADERS_ENABLED=True,
        CONTENT_SECURITY_POLICY="default-src 'self'; base-uri 'self'; object-src 'none'; frame-ancestors 'none'; form-action 'self'; script-src 'self' https://cdn.jsdelivr.net https://unpkg.com; style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; font-src 'self'; img-src 'self' data:; connect-src 'self'",
    )
    def test_enforced_csp_has_no_unsafe_script_policy(self):
        response = BrowserSecurityHeadersMiddleware(lambda request: HttpResponse("ok"))(self.factory.get("/"))
        policy = response["Content-Security-Policy"]
        self.assertNotIn("script-src 'unsafe-inline'", policy)
        self.assertNotIn("unsafe-eval", policy)
        self.assertIn("object-src 'none'", policy)
        self.assertIn("base-uri 'self'", policy)
        self.assertIn("frame-ancestors 'none'", policy)

    def test_active_templates_do_not_reintroduce_executable_inline_handlers(self):
        root = Path(settings.BASE_DIR)
        templates = (
            "templates/base.html",
            "accounts/templates/accounts/sms_login_verify.html",
            "accounts/templates/accounts/sms_signup_verify.html",
            "blog/templates/blog/components/comment_form.html",
            "blog/templates/blog/components/comment_card.html",
            "dashboard/templates/dashboard/variants/form.html",
            "dashboard/templates/dashboard/products/inventory_import/detail.html",
        )
        prohibited = ("<script>", "onclick=", "onchange=", "onload=", "onerror=", "onsubmit=")
        for template in templates:
            source = (root / template).read_text(encoding="utf-8")
            for token in prohibited:
                with self.subTest(template=template, token=token):
                    self.assertNotIn(token, source)

    def test_active_templates_do_not_use_javascript_urls(self):
        root = Path(settings.BASE_DIR)
        template_roots = (
            root / "templates",
            root / "accounts" / "templates",
            root / "blog" / "templates",
            root / "dashboard" / "templates",
            root / "orders" / "templates",
            root / "products" / "templates",
            root / "reviews" / "templates",
            root / "support" / "templates",
        )
        for template_root in template_roots:
            for template in template_root.rglob("*.html"):
                with self.subTest(template=template):
                    self.assertNotIn("javascript:", template.read_text(encoding="utf-8").casefold())

    @override_settings(DEBUG=False)
    def test_production_error_pages_do_not_render_debug_details(self):
        not_found = Client().get("/not-a-real-route/")
        internal_error = server_error(self.factory.get("/controlled-error/"))
        for response in (not_found, internal_error):
            body = response.content.decode()
            self.assertNotIn(str(settings.BASE_DIR), body)
            self.assertNotIn(settings.SECRET_KEY, body)
            self.assertNotIn("Traceback", body)
        self.assertEqual(not_found.status_code, 404)
        self.assertEqual(internal_error.status_code, 500)

    @override_settings(
        SESSION_COOKIE_SECURE=True,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        CSRF_COOKIE_SECURE=True,
        CSRF_COOKIE_HTTPONLY=True,
        CSRF_COOKIE_SAMESITE="Lax",
        SESSION_ENGINE="django.contrib.sessions.backends.signed_cookies",
    )
    def test_secure_lax_session_and_csrf_cookies_are_compatible_with_rendered_tokens(self):
        session_request = self.factory.get("/")
        session_middleware = SessionMiddleware(lambda request: HttpResponse("ok"))
        session_middleware.process_request(session_request)
        session_request.session["test"] = "value"
        session_response = session_middleware.process_response(session_request, HttpResponse("ok"))
        session_cookie = session_response.cookies[settings.SESSION_COOKIE_NAME]
        self.assertTrue(session_cookie["secure"])
        self.assertTrue(session_cookie["httponly"])
        self.assertEqual(session_cookie["samesite"], "Lax")

        csrf_request = self.factory.get("/")
        get_token(csrf_request)
        csrf_response = CsrfViewMiddleware(lambda request: HttpResponse("ok")).process_response(
            csrf_request, HttpResponse("ok")
        )
        csrf_cookie = csrf_response.cookies[settings.CSRF_COOKIE_NAME]
        self.assertTrue(csrf_cookie["secure"])
        self.assertTrue(csrf_cookie["httponly"])
        self.assertEqual(csrf_cookie["samesite"], "Lax")
