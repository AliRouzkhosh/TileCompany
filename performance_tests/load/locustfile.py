"""Weighted, HTTPS-only Locust workload for tilecompany.localhost."""

from __future__ import annotations

import json
import os
import re
import threading
from pathlib import Path
from urllib.parse import urlparse

from locust import HttpUser, LoadTestShape, between, events, task
from locust.exception import StopUser


TARGET = "https://tilecompany.localhost"
PASSWORD = os.environ.get("LOADTEST_PASSWORD", "LocalLoadOnly-2026!")
PROFILE = os.environ.get("LOADTEST_PROFILE", "mixed").strip().lower()
RESULT_PATH = Path(os.environ.get("LOADTEST_STAGE_RESULTS", "performance_tests/load/results/stages.jsonl"))
LEVELS = tuple(int(value) for value in os.environ.get("LOADTEST_LEVELS", "10,25,50,100,200").split(","))
WARM_SECONDS = int(os.environ.get("LOADTEST_WARM_SECONDS", "30"))
MEASURE_SECONDS = int(os.environ.get("LOADTEST_MEASURE_SECONDS", "120"))
FAILURE_STOP_PERCENT = float(os.environ.get("LOADTEST_FAILURE_STOP_PERCENT", "5"))

CSRF_RE = re.compile(r'name="csrfmiddlewaretoken" value="([^"]+)"')
VARIANT_RE = re.compile(r'name="variant_id"[^>]*value="(\d+)"')
ADDRESS_RE = re.compile(r'name="address_id"[^>]*value="(\d+)"')
TICKET_RE = re.compile(r'/en/dashboard/support/(\d+)/')
CART_KEY_RE = re.compile(r'data-cart-key="v_(\d+)"')

_identity_lock = threading.Lock()
_identity_counters = {"shopper": 0, "staff": 0}


def identity(kind: str, pool_size: int) -> str:
    with _identity_lock:
        index = _identity_counters[kind]
        _identity_counters[kind] += 1
    return f"loadtest-{kind}-{index % pool_size:03d}"


def tls_verify():
    configured = os.environ.get("LOADTEST_CA_BUNDLE", "").strip()
    if configured:
        return configured
    if os.environ.get("LOADTEST_INSECURE_LOCAL_TLS", "").lower() in {"1", "true", "yes"}:
        return False
    return True


def target_is_allowed(value: str) -> bool:
    parsed = urlparse(value)
    return (
        parsed.scheme == "https"
        and parsed.hostname == "tilecompany.localhost"
        and parsed.port in (None, 443)
    )


def stage_snapshot(environment, level: int, *, aborted: str = "") -> None:
    stats = environment.stats.total
    payload = {
        "users": level,
        "requests": stats.num_requests,
        "failures": stats.num_failures,
        "failure_percent": round(stats.fail_ratio * 100, 4),
        "rps": round(stats.total_rps, 3),
        "median_ms": stats.median_response_time,
        "p90_ms": stats.get_response_time_percentile(0.90),
        "p95_ms": stats.get_response_time_percentile(0.95),
        "p99_ms": stats.get_response_time_percentile(0.99),
        "max_ms": stats.max_response_time,
        "aborted": aborted,
    }
    RESULT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with RESULT_PATH.open("a", encoding="utf-8") as output:
        output.write(json.dumps(payload, sort_keys=True) + "\n")
    print("LOADTEST_STAGE " + json.dumps(payload, sort_keys=True), flush=True)


@events.init_command_line_parser.add_listener
def add_arguments(parser):
    parser.add_argument("--local-load-ack", action="store_true", help="Acknowledge this local-only workload.")


@events.test_start.add_listener
def validate_target(environment, **_kwargs):
    if not environment.parsed_options.local_load_ack:
        environment.process_exit_code = 2
        environment.runner.quit()
        raise RuntimeError("Refusing to start without --local-load-ack.")
    effective_host = environment.host or TARGET
    if not target_is_allowed(effective_host):
        environment.process_exit_code = 2
        environment.runner.quit()
        raise RuntimeError(f"Refusing non-local or non-HTTPS load-test target: {effective_host}")
    if RESULT_PATH.exists():
        RESULT_PATH.unlink()


class ProgressiveLocalShape(LoadTestShape):
    """Monotonically add users, then reset stats after each stage warm-up."""

    use_common_options = True

    def __init__(self):
        super().__init__()
        self.stage = 0
        self.reset_stages: set[int] = set()
        self.captured_stages: set[int] = set()

    def tick(self):
        elapsed = self.get_run_time()
        span = WARM_SECONDS + MEASURE_SECONDS

        # Capture the just-completed window before switching/resetting stats for
        # the next level. This also captures the final level before stopping.
        for completed in range(len(LEVELS)):
            if elapsed >= (completed + 1) * span and completed not in self.captured_stages:
                stage_snapshot(self.runner.environment, LEVELS[completed])
                self.captured_stages.add(completed)

        if elapsed >= len(LEVELS) * span:
            return None

        stage = min(int(elapsed // span), len(LEVELS) - 1)
        stage_elapsed = elapsed - stage * span
        environment = self.runner.environment

        if stage_elapsed >= WARM_SECONDS and stage not in self.reset_stages:
            environment.stats.reset_all()
            self.reset_stages.add(stage)
            print(f"LOADTEST_MEASURE_START users={LEVELS[stage]}", flush=True)

        if stage in self.reset_stages:
            total = environment.stats.total
            if total.num_requests >= 100 and total.fail_ratio * 100 > FAILURE_STOP_PERCENT:
                stage_snapshot(environment, LEVELS[stage], aborted="sustained_failure_rate")
                self.runner.quit()
                return None

        spawn_rate = max(1, LEVELS[stage] // 5)
        return LEVELS[stage], spawn_rate


class LocalUser(HttpUser):
    abstract = True
    host = TARGET
    wait_time = between(1.0, 3.0)

    def request(self, method: str, path: str, *, name: str, expected=(200,), **kwargs):
        if not target_is_allowed(self.environment.host or self.host):
            self.environment.process_exit_code = 2
            self.environment.runner.quit()
            raise StopUser()
        kwargs.setdefault("allow_redirects", False)
        if method.upper() not in {"GET", "HEAD", "OPTIONS", "TRACE"}:
            headers = dict(kwargs.pop("headers", {}))
            headers.setdefault("Origin", TARGET)
            headers.setdefault("Referer", TARGET + path)
            kwargs["headers"] = headers
        with self.client.request(
            method,
            path,
            name=name,
            verify=tls_verify(),
            catch_response=True,
            **kwargs,
        ) as response:
            if response.status_code not in expected:
                location = response.headers.get("Location", "")
                response.failure(
                    f"expected {expected}, got {response.status_code}"
                    + (f" location={location}" if location else "")
                )
            return response

    def csrf_get(self, path: str, name: str):
        response = self.request("GET", path, name=name)
        match = CSRF_RE.search(response.text)
        if not match:
            response.failure("CSRF token missing")
            return response, ""
        return response, match.group(1)

    def login(self, username: str) -> None:
        _response, token = self.csrf_get("/en/accounts/login/", "/accounts/login [setup GET]")
        response = self.request(
            "POST",
            "/en/accounts/login/",
            name="/accounts/login [setup POST]",
            expected=(302,),
            data={"username": username, "password": PASSWORD, "csrfmiddlewaretoken": token},
            allow_redirects=False,
        )
        if response.status_code == 302 and "/accounts/" not in response.headers.get("Location", ""):
            response.failure("login did not redirect to the account area")


class PublicBrowser(LocalUser):
    weight = 17 if PROFILE == "mixed" else 1

    @task(6)
    def homepage(self):
        self.request("GET", "/en/", name="/ [homepage]")

    @task(5)
    def catalog(self):
        self.request("GET", "/en/shop/", name="/shop/ [catalog]")

    @task(3)
    def category(self):
        self.request("GET", "/en/shop/category/tiles-ceramics/", name="/shop/category/:slug")

    @task(5)
    def product_detail(self):
        self.request("GET", "/en/product/demo-eef-pietra-a1/", name="/product/:slug")

    @task(2)
    def articles(self):
        self.request("GET", "/en/articles/", name="/articles/")
        self.request("GET", "/en/articles/ceramic-vs-porcelain-tile/", name="/articles/:slug")

    @task(2)
    def help_center(self):
        self.request("GET", "/en/support/help/", name="/support/help/")
        self.request("GET", "/en/support/help/creating-and-managing-your-account/", name="/support/help/:slug")

    @task(1)
    def on_sale(self):
        self.request("GET", "/en/shop/?on_sale=1", name="/shop/?on_sale=1")


class AuthenticatedShopper(LocalUser):
    weight = 2 if PROFILE == "mixed" else 0

    def on_start(self):
        self.login(identity("shopper", 64))
        self.variant_id = ""
        self.ensure_cart()

    def ensure_cart(self):
        response, token = self.csrf_get("/en/product/demo-eef-pietra-a1/", "/product/:slug [shopper setup]")
        match = VARIANT_RE.search(response.text)
        if not match:
            response.failure("purchasable variant missing")
            return
        self.variant_id = match.group(1)
        self.request(
            "POST",
            "/en/orders/cart/add-variant/",
            name="/orders/cart/add-variant/ [safe write]",
            expected=(302,),
            data={
                "variant_id": self.variant_id,
                "quantity": 1,
                "next": "/en/orders/cart/",
                "csrfmiddlewaretoken": token,
            },
            headers={"Referer": TARGET + "/en/product/demo-eef-pietra-a1/"},
            allow_redirects=False,
        )
        cart = self.request("GET", "/en/orders/cart/", name="/orders/cart/ [setup verify]")
        cart_key = CART_KEY_RE.search(cart.text)
        if cart_key:
            self.variant_id = cart_key.group(1)
        else:
            cart.failure("safe cart setup produced no cart line")

    @task(4)
    def account(self):
        self.request("GET", "/en/accounts/", name="/accounts/")

    @task(5)
    def browse(self):
        self.request("GET", "/en/shop/", name="/shop/ [shopper]")
        self.request("GET", "/en/product/demo-eef-pietra-a1/", name="/product/:slug [shopper]")

    @task(3)
    def cart_view_update(self):
        response, token = self.csrf_get("/en/orders/cart/", "/orders/cart/")
        if not self.variant_id:
            self.ensure_cart()
            return
        self.request(
            "POST",
            f"/en/orders/cart/update/v_{self.variant_id}/",
            name="/orders/cart/update/:key [safe write]",
            expected=(302,),
            data={"quantity": 1, "csrfmiddlewaretoken": token},
            allow_redirects=False,
        )

    @task(20 if os.environ.get("LOADTEST_SMOKE_FLOW") == "checkout" else 2)
    def checkout_read_path(self):
        shipping, token = self.csrf_get("/en/orders/checkout/shipping/", "/orders/checkout/shipping/ [GET]")
        address = ADDRESS_RE.search(shipping.text)
        if not address:
            shipping.failure("default shipping address missing")
            return
        self.request(
            "POST",
            "/en/orders/checkout/shipping/",
            name="/orders/checkout/shipping/ [POST]",
            expected=(302,),
            data={
                "address_id": address.group(1),
                "shipping_method": "standard",
                "delivery_slot": "",
                "csrfmiddlewaretoken": token,
            },
            allow_redirects=False,
        )
        _payment, payment_token = self.csrf_get("/en/orders/checkout/payment/", "/orders/checkout/payment/ [GET]")
        self.request(
            "POST",
            "/en/orders/checkout/payment/",
            name="/orders/checkout/payment/ [POST]",
            expected=(302,),
            data={"payment_method": "mock", "csrfmiddlewaretoken": payment_token},
            allow_redirects=False,
        )
        self.request("GET", "/en/orders/checkout/review/", name="/orders/checkout/review/ [GET]")


class OperationalStaff(LocalUser):
    weight = 1 if PROFILE == "mixed" else 0

    def on_start(self):
        self.login(identity("staff", 32))
        self.ticket_id = ""

    @task(5)
    def dashboard_home(self):
        self.request("GET", "/en/dashboard/", name="/dashboard/")

    @task(4)
    def order_catalog(self):
        self.request("GET", "/en/dashboard/orders/", name="/dashboard/orders/")

    @task(3)
    def attention_queue(self):
        self.request("GET", "/en/dashboard/attention/", name="/dashboard/attention/")

    @task(2)
    def support(self):
        inbox = self.request("GET", "/en/dashboard/support/", name="/dashboard/support/")
        match = TICKET_RE.search(inbox.text)
        if match:
            self.ticket_id = match.group(1)
        if self.ticket_id:
            self.request("GET", f"/en/dashboard/support/{self.ticket_id}/", name="/dashboard/support/:id/")
