"""Real-connection PostgreSQL concurrency characterization.

These tests deliberately skip outside PostgreSQL.  Workers receive only primary
keys, open their own Django connection, synchronize at a barrier, and surface
every exception back to the test thread.
"""

from datetime import timedelta
from decimal import Decimal
from threading import Barrier, Lock, Thread
from tempfile import TemporaryDirectory

from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import make_password
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, close_old_connections, connection
from django.test import TransactionTestCase, override_settings
from django.test.client import RequestFactory
from django.utils import timezone
from django.contrib.sessions.middleware import SessionMiddleware
from django.contrib.auth.middleware import AuthenticationMiddleware

from accounts.models import Address, OtpChallenge, PhoneIdentity
from accounts.otp import verify_login_otp, verify_phone_otp
from accounts.views import sms_signup_complete
from dashboard.services import approve_refund, reject_refund
from orders.email_delivery import ensure_order_email_delivery
from orders.models import Order, OrderEmailDelivery, OrderItem, OrderStatusHistory
from orders.selectors import CheckoutContext, CheckoutItem, CheckoutSummary
from orders.services import confirm_cod_order, create_order, mark_order_paid
from products.currency import Currency
from products.inventory_price_imports import apply_inventory_price_import, create_inventory_price_import_preview
from products.models import InventoryPriceImportBatch, Product, ProductOperationalChange, ProductVariant, VariantPrice
from products.storage import private_inventory_import_storage
from products.services import set_variant_price


@override_settings(
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    SITE_URL="https://example.test",
    SMS_ENABLED=True,
    SMS_OTP_LOGIN_ENABLED=True,
    SMS_BACKEND="accounts.sms.MemorySmsBackend",
    LOCAL_PAYMENT_SIMULATOR_ENABLED=True,
    MOCK_PAYMENT_SIMULATION_ENABLED=False,
)
class PostgreSQLConcurrencyCharacterizationTests(TransactionTestCase):
    """Characterize production service behavior under independent transactions."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._private_storage_directory = TemporaryDirectory()
        cls._previous_private_storage_location = private_inventory_import_storage.location
        private_inventory_import_storage.location = cls._private_storage_directory.name

    @classmethod
    def tearDownClass(cls):
        private_inventory_import_storage.location = cls._previous_private_storage_location
        cls._private_storage_directory.cleanup()
        super().tearDownClass()

    def setUp(self):
        super().setUp()
        if connection.vendor != "postgresql":
            self.skipTest("PostgreSQL-only concurrency characterization")
        self.user_model = get_user_model()

    def _concurrently(self, *operations, timeout=20):
        """Start workers together and return all values and exceptions deterministically."""
        barrier = Barrier(len(operations) + 1)
        results, errors, guard = [], [], Lock()

        def worker(operation):
            close_old_connections()
            try:
                barrier.wait(timeout=timeout)
                value = operation()
                with guard:
                    results.append(value)
            except BaseException as error:  # surfaced in the owning test thread
                with guard:
                    errors.append(error)
            finally:
                close_old_connections()

        threads = [Thread(target=worker, args=(operation,), daemon=True) for operation in operations]
        for thread in threads:
            thread.start()
        barrier.wait(timeout=timeout)
        for thread in threads:
            thread.join(timeout=timeout)
        alive = [thread.name for thread in threads if thread.is_alive()]
        self.assertFalse(alive, f"concurrency worker timed out: {alive}")
        return results, errors

    def _user_and_address(self, suffix):
        user = self.user_model.objects.create_user(f"concurrency-{suffix}", email=f"{suffix}@example.test", password="secret")
        address = Address.objects.create(
            user=user, first_name="Concurrency", last_name=suffix, phone="123",
            country="IR", province="Tehran", city="Tehran", street_address="1 Test Way",
        )
        return user, address

    def _variant(self, suffix, *, stock=1):
        product = Product.objects.create(name=f"Concurrency {suffix}", slug=f"concurrency-{suffix}", sku=f"CON-P-{suffix}", is_active=True)
        variant = ProductVariant.objects.create(product=product, sku=f"CON-V-{suffix}", stock=stock, is_active=True)
        VariantPrice.objects.create(variant=variant, currency=Currency.USD, amount=Decimal("10.00"))
        return variant

    def _checkout(self, *, user_id, address_id, variant_ids):
        user = self.user_model.objects.get(pk=user_id)
        address = Address.objects.get(pk=address_id)
        variants = [ProductVariant.objects.select_related("product").get(pk=pk) for pk in variant_ids]
        items = [CheckoutItem(key=f"v_{variant.pk}", product=variant.product, variant=variant, quantity=1, unit_price=Decimal("10.00"), subtotal=Decimal("10.00")) for variant in variants]
        summary = CheckoutSummary(currency=Currency.USD, subtotal=Decimal("10.00") * len(items), shipping=Decimal("0.00"), discount=Decimal("0.00"), tax=Decimal("0.00"), total=Decimal("10.00") * len(items))
        context = CheckoutContext(items=items, summary=summary, cart_quantity=len(items), default_address=address, addresses=[address], shipping_methods=[], currency=Currency.USD, unavailable_variant_ids=[])
        return create_order(user=user, address=address, cleaned_data={"payment_method": Order.PaymentMethod.MOCK, "shipping_method": "standard", "delivery_slot": "", "email_receipt_requested": False, "receipt_email": ""}, checkout=context, enforce_single_retryable_online_order=False)

    def _order(self, *, method=Order.PaymentMethod.MOCK, status=Order.Status.PENDING, receipt=False):
        user, _address = self._user_and_address(f"order-{Order.objects.count()}")
        return Order.objects.create(user=user, full_name="Concurrency Buyer", address="1 Test Way", city="Tehran", phone="123", payment_method=method, status=status, currency=Currency.USD, total_amount=Decimal("10.00"), email_receipt_requested=receipt, receipt_email=user.email if receipt else "")

    def test_final_stock_checkout_never_oversells(self):
        variant = self._variant("final", stock=1)
        first_user, first_address = self._user_and_address("final-one")
        second_user, second_address = self._user_and_address("final-two")
        results, errors = self._concurrently(
            lambda: self._checkout(user_id=first_user.pk, address_id=first_address.pk, variant_ids=[variant.pk]),
            lambda: self._checkout(user_id=second_user.pk, address_id=second_address.pk, variant_ids=[variant.pk]),
        )
        self.assertEqual(len(results), 1)
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], ValueError)
        variant.refresh_from_db()
        self.assertEqual(variant.stock, 0)
        self.assertGreaterEqual(variant.stock, 0)
        self.assertEqual(Order.objects.filter(items__variant=variant).distinct().count(), 1)

    def test_reversed_multi_line_checkout_has_no_deadlock_or_oversell(self):
        first, second = self._variant("line-a", stock=2), self._variant("line-b", stock=2)
        left_user, left_address = self._user_and_address("multi-left")
        right_user, right_address = self._user_and_address("multi-right")
        results, errors = self._concurrently(
            lambda: self._checkout(user_id=left_user.pk, address_id=left_address.pk, variant_ids=[first.pk, second.pk]),
            lambda: self._checkout(user_id=right_user.pk, address_id=right_address.pk, variant_ids=[second.pk, first.pk]),
        )
        self.assertEqual(errors, [])
        self.assertEqual(len(results), 2)
        first.refresh_from_db(); second.refresh_from_db()
        self.assertEqual((first.stock, second.stock), (0, 0))

    def test_otp_challenge_is_consumed_once(self):
        user, _address = self._user_and_address("otp")
        PhoneIdentity.objects.create(user=user, phone_e164="+989121234567", verified_at=timezone.now())
        challenge = OtpChallenge.objects.create(purpose=OtpChallenge.Purpose.LOGIN, phone_e164="+989121234567", user=user, code_hash=make_password("123456"), expires_at=timezone.now() + timedelta(minutes=2))
        results, errors = self._concurrently(
            lambda: verify_login_otp(challenge_id=challenge.pk, code="123456", request_context={"ip": "198.51.100.1"}),
            lambda: verify_login_otp(challenge_id=challenge.pk, code="123456", request_context={"ip": "198.51.100.2"}),
        )
        self.assertEqual(errors, [])
        self.assertEqual(sum(result.success for result in results), 1)
        challenge.refresh_from_db()
        self.assertFalse(challenge.is_active)
        self.assertIsNotNone(challenge.consumed_at)

    def test_duplicate_payment_callback_is_idempotent(self):
        order = self._order(receipt=True)
        results, errors = self._concurrently(
            lambda: mark_order_paid(Order.objects.get(pk=order.pk), "concurrent-ref", "test"),
            lambda: mark_order_paid(Order.objects.get(pk=order.pk), "concurrent-ref", "test"),
        )
        self.assertEqual(errors, [])
        self.assertEqual(len(results), 2)
        order.refresh_from_db()
        self.assertTrue(order.is_paid)
        self.assertEqual(order.status, Order.Status.CONFIRMED)
        self.assertEqual(OrderStatusHistory.objects.filter(order=order, status=Order.Status.CONFIRMED).count(), 1)
        self.assertEqual(OrderEmailDelivery.objects.filter(order=order, kind=OrderEmailDelivery.Kind.ONLINE_PAID_RECEIPT).count(), 1)

    def test_duplicate_cod_confirmation_is_idempotent(self):
        order = self._order(method=Order.PaymentMethod.COD, receipt=True)
        results, errors = self._concurrently(
            lambda: confirm_cod_order(Order.objects.get(pk=order.pk)),
            lambda: confirm_cod_order(Order.objects.get(pk=order.pk)),
        )
        self.assertEqual(errors, [])
        self.assertEqual(len(results), 2)
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.CONFIRMED)
        self.assertFalse(order.is_paid)
        self.assertEqual(OrderStatusHistory.objects.filter(order=order, status=Order.Status.CONFIRMED).count(), 1)
        self.assertEqual(OrderEmailDelivery.objects.filter(order=order, kind=OrderEmailDelivery.Kind.COD_CONFIRMATION).count(), 1)

    def test_conflicting_refund_decisions_require_one_winner(self):
        order = self._order(status=Order.Status.DELIVERED)
        order.refund_status = Order.RefundStatus.REQUESTED
        order.refund_requested_stage = Order.Status.DELIVERED
        order.save(update_fields=["refund_status", "refund_requested_stage"])
        approver, _ = self._user_and_address("approver")
        rejecter, _ = self._user_and_address("rejecter")

        def approve():
            stale = Order.objects.get(pk=order.pk)
            return approve_refund(stale, approver, "approve")

        def reject():
            stale = Order.objects.get(pk=order.pk)
            return reject_refund(stale, rejecter, "reject")

        results, errors = self._concurrently(approve, reject)
        self.assertEqual(errors, [])
        self.assertLessEqual(sum(result.success for result in results), 1, "conflicting stale refund decisions both proceeded")
        order.refresh_from_db()
        self.assertIn(order.refund_status, {Order.RefundStatus.APPROVED, Order.RefundStatus.REJECTED})

    def test_concurrent_first_price_creation_has_one_row_and_no_unhandled_error(self):
        variant = self._variant("price", stock=1)
        VariantPrice.objects.filter(variant=variant, currency=Currency.TOMAN).delete()
        results, errors = self._concurrently(
            lambda: set_variant_price(variant=ProductVariant.objects.get(pk=variant.pk), currency=Currency.TOMAN, amount=Decimal("100")),
            lambda: set_variant_price(variant=ProductVariant.objects.get(pk=variant.pk), currency=Currency.TOMAN, amount=Decimal("100")),
        )
        self.assertEqual(errors, [], f"unhandled first-price concurrency error: {errors}")
        self.assertEqual(len(results), 2)
        self.assertEqual(VariantPrice.objects.filter(variant=variant, currency=Currency.TOMAN).count(), 1)

    def test_sms_signup_completion_claim_has_one_winner_and_graceful_loser(self):
        """Two already-authorized signup completions compete at the real claim boundary."""
        factory = RequestFactory()
        phone = "+989121200000"

        def completion(username):
            request = factory.post(
                "/accounts/sms-signup/complete/",
                {"username": username, "email": "", "password1": "concurrency-secret-123", "password2": "concurrency-secret-123"},
            )
            SessionMiddleware(lambda response: response).process_request(request)
            AuthenticationMiddleware(lambda response: response).process_request(request)
            request.session.save()
            request.session["sms_signup_grant"] = {
                "phone": phone,
                "expires": (timezone.now() + timedelta(minutes=10)).timestamp(),
            }
            request.session.save()
            return sms_signup_complete(request)

        results, errors = self._concurrently(
            lambda: completion("phone-claim-first"),
            lambda: completion("phone-claim-second"),
        )
        self.assertEqual(errors, [])
        self.assertEqual(sum(response.status_code == 302 for response in results), 1)
        self.assertEqual(PhoneIdentity.objects.filter(phone_e164=phone).count(), 1)

    def test_email_delivery_claim_is_unique(self):
        order = self._order(receipt=True)
        results, errors = self._concurrently(
            lambda: ensure_order_email_delivery(order=Order.objects.get(pk=order.pk), kind=OrderEmailDelivery.Kind.ONLINE_PAID_RECEIPT),
            lambda: ensure_order_email_delivery(order=Order.objects.get(pk=order.pk), kind=OrderEmailDelivery.Kind.ONLINE_PAID_RECEIPT),
        )
        self.assertEqual(errors, [])
        self.assertEqual(len(results), 2)
        self.assertEqual(OrderEmailDelivery.objects.filter(order=order, kind=OrderEmailDelivery.Kind.ONLINE_PAID_RECEIPT).count(), 1)

    def test_concurrent_import_apply_is_single_and_audited_once(self):
        actor = self.user_model.objects.create_superuser("import-owner", "import-owner@example.test", "secret")
        variant = self._variant("import", stock=5)
        source = b"sku,stock,low_stock_threshold,irt_price,irt_compare_at,usd_price,usd_compare_at\nCON-V-import,7,,,,,,\n"
        batch = create_inventory_price_import_preview(actor=actor, uploaded_file=SimpleUploadedFile("concurrency.csv", source))
        self.assertEqual(batch.status, InventoryPriceImportBatch.Status.VALIDATED)
        results, errors = self._concurrently(
            lambda: apply_inventory_price_import(actor=self.user_model.objects.get(pk=actor.pk), batch_id=batch.pk),
            lambda: apply_inventory_price_import(actor=self.user_model.objects.get(pk=actor.pk), batch_id=batch.pk),
        )
        self.assertEqual(errors, [])
        self.assertEqual(len(results), 2)
        batch.refresh_from_db(); variant.refresh_from_db()
        self.assertEqual(batch.status, InventoryPriceImportBatch.Status.APPLIED)
        self.assertEqual(variant.stock, 7)
        self.assertEqual(ProductOperationalChange.objects.filter(import_batch=batch).count(), 1)
