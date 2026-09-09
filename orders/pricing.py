"""Single server-authoritative commerce policy and checkout calculation path."""

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP

from django.utils.translation import gettext_lazy as _

from products.currency import Currency
from .models import CommercePolicy, DeliveryMethodPolicy


METHOD_COPY = {
    "standard": (_("Standard delivery"), _("3–5 business days")),
    "express": (_("Express delivery"), _("1–2 business days")),
    "pickup": (_("Store pickup"), _("Today")),
}


@dataclass(frozen=True, slots=True)
class DeliveryPrice:
    code: str
    name: str
    estimated_days: str
    configured_mode: str
    effective_mode: str
    amount: Decimal
    free_by_threshold: bool
    pay_on_delivery: bool
    threshold: Decimal | None
    amount_to_threshold: Decimal | None

    @property
    def price(self):
        return self.amount

    @property
    def description(self):
        if self.pay_on_delivery:
            return _("Delivery charge is paid separately when you receive the order.")
        if self.free_by_threshold:
            return _("Free — order threshold reached")
        if self.effective_mode == DeliveryMethodPolicy.ChargeMode.FREE:
            return _("Free delivery.")
        return _("Delivery fee is included in the order total.")


@dataclass(frozen=True, slots=True)
class PricingResult:
    currency: str
    subtotal: Decimal
    qualifying_subtotal: Decimal
    discount: Decimal
    tax_rate: Decimal
    tax_amount: Decimal
    delivery_method: str | None
    configured_delivery_mode: str | None
    effective_delivery_mode: str | None
    delivery_amount: Decimal
    delivery_is_free: bool
    delivery_free_by_threshold: bool
    delivery_pay_on_delivery: bool
    total_payable_now: Decimal
    policy_version: int


def _money_quantum(currency):
    return Decimal("1") if currency == Currency.TOMAN else Decimal("0.01")


def load_policy(*, for_update=False):
    manager = CommercePolicy.objects.select_for_update() if for_update else CommercePolicy.objects
    policy, _ = manager.get_or_create(singleton_key=1)
    delivery_manager = DeliveryMethodPolicy.objects.select_for_update() if for_update else DeliveryMethodPolicy.objects
    methods = {item.code: item for item in delivery_manager.filter(code__in=METHOD_COPY)}
    defaults = {
        "standard": (DeliveryMethodPolicy.ChargeMode.FREE, Decimal("0"), Decimal("0"), True),
        "express": (DeliveryMethodPolicy.ChargeMode.FIXED, Decimal("1500000"), Decimal("19.90"), False),
        "pickup": (DeliveryMethodPolicy.ChargeMode.FREE, Decimal("0"), Decimal("0"), False),
    }
    for code, (mode, irt, usd, eligible) in defaults.items():
        if code not in methods:
            methods[code], _ = DeliveryMethodPolicy.objects.get_or_create(
                code=code,
                defaults={"charge_mode": mode, "fixed_amount_irt": irt, "fixed_amount_usd": usd,
                          "eligible_for_free_shipping_threshold": eligible},
            )
    return policy, methods


def delivery_price(*, policy, method_policy, currency, qualifying_subtotal):
    threshold = None
    if policy.free_shipping_threshold_enabled:
        threshold = (policy.free_shipping_threshold_irt if currency == Currency.TOMAN
                     else policy.free_shipping_threshold_usd)
    free_by_threshold = bool(
        method_policy.charge_mode != DeliveryMethodPolicy.ChargeMode.FREE
        and method_policy.eligible_for_free_shipping_threshold
        and threshold is not None and qualifying_subtotal >= threshold
    )
    if method_policy.charge_mode == DeliveryMethodPolicy.ChargeMode.FREE or free_by_threshold:
        effective = DeliveryMethodPolicy.ChargeMode.FREE
        amount = Decimal("0")
    elif method_policy.charge_mode == DeliveryMethodPolicy.ChargeMode.PAY_ON_DELIVERY:
        effective = DeliveryMethodPolicy.ChargeMode.PAY_ON_DELIVERY
        amount = Decimal("0")
    else:
        effective = DeliveryMethodPolicy.ChargeMode.FIXED
        amount = method_policy.fixed_amount_irt if currency == Currency.TOMAN else method_policy.fixed_amount_usd
    remaining = threshold - qualifying_subtotal if threshold is not None and qualifying_subtotal < threshold and method_policy.eligible_for_free_shipping_threshold else None
    name, days = METHOD_COPY[method_policy.code]
    return DeliveryPrice(
        code=method_policy.code, name=name, estimated_days=days,
        configured_mode=method_policy.charge_mode, effective_mode=effective,
        amount=amount, free_by_threshold=free_by_threshold,
        pay_on_delivery=effective == DeliveryMethodPolicy.ChargeMode.PAY_ON_DELIVERY,
        threshold=threshold, amount_to_threshold=remaining,
    )


def calculate_pricing(*, subtotal, currency, delivery_method=None, for_update=False, policy_bundle=None):
    if currency not in Currency.values:
        raise ValueError("Unsupported currency.")
    policy, methods = policy_bundle or load_policy(for_update=for_update)
    qualifying_subtotal = subtotal  # existing discounted merchandise subtotal
    selected = None
    if delivery_method is not None:
        try:
            selected = delivery_price(policy=policy, method_policy=methods[delivery_method], currency=currency,
                                      qualifying_subtotal=qualifying_subtotal)
        except KeyError as error:
            raise ValueError("Unsupported delivery method.") from error
    tax_rate = policy.tax_rate if policy.tax_enabled else Decimal("0")
    tax_amount = (qualifying_subtotal * tax_rate / Decimal("100")).quantize(_money_quantum(currency), rounding=ROUND_HALF_UP)
    delivery_amount = selected.amount if selected else Decimal("0")
    return PricingResult(
        currency=currency, subtotal=subtotal, qualifying_subtotal=qualifying_subtotal,
        discount=Decimal("0"), tax_rate=tax_rate, tax_amount=tax_amount,
        delivery_method=delivery_method,
        configured_delivery_mode=selected.configured_mode if selected else None,
        effective_delivery_mode=selected.effective_mode if selected else None,
        delivery_amount=delivery_amount,
        delivery_is_free=bool(selected and selected.effective_mode == DeliveryMethodPolicy.ChargeMode.FREE),
        delivery_free_by_threshold=bool(selected and selected.free_by_threshold),
        delivery_pay_on_delivery=bool(selected and selected.pay_on_delivery),
        total_payable_now=subtotal + tax_amount + delivery_amount,
        policy_version=policy.version,
    )


def shipping_methods(*, subtotal, currency, policy_bundle=None):
    policy, methods = policy_bundle or load_policy()
    return [delivery_price(policy=policy, method_policy=methods[code], currency=currency,
                           qualifying_subtotal=subtotal) for code in ("standard", "express", "pickup")]
