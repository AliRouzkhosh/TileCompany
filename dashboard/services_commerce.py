from decimal import Decimal

from django.db import transaction

from orders.models import CommercePolicy, CommercePolicyAudit, DeliveryMethodPolicy
from orders.pricing import load_policy


class StaleCommercePolicy(Exception):
    pass


def _snapshot_policy(policy):
    return {
        "tax_enabled": policy.tax_enabled, "tax_rate": str(policy.tax_rate),
        "threshold_enabled": policy.free_shipping_threshold_enabled,
        "threshold_irt": str(policy.free_shipping_threshold_irt) if policy.free_shipping_threshold_irt is not None else None,
        "threshold_usd": str(policy.free_shipping_threshold_usd) if policy.free_shipping_threshold_usd is not None else None,
        "version": policy.version,
    }


def _snapshot_method(method):
    return {"code": method.code, "charge_mode": method.charge_mode,
            "fixed_amount_irt": str(method.fixed_amount_irt), "fixed_amount_usd": str(method.fixed_amount_usd),
            "threshold_eligible": method.eligible_for_free_shipping_threshold}


@transaction.atomic
def update_commerce_settings(*, actor, cleaned_data):
    policy, methods = load_policy(for_update=True)
    if cleaned_data["version"] != policy.version:
        raise StaleCommercePolicy
    before_policy = _snapshot_policy(policy)
    policy.tax_enabled = cleaned_data["tax_enabled"]
    policy.tax_rate = cleaned_data["tax_rate"]
    policy.free_shipping_threshold_enabled = cleaned_data["threshold_enabled"]
    policy.free_shipping_threshold_irt = cleaned_data["threshold_irt"]
    policy.free_shipping_threshold_usd = cleaned_data["threshold_usd"]
    policy.version += 1
    policy.full_clean()
    policy.save()
    after_policy = _snapshot_policy(policy)
    if before_policy != after_policy:
        CommercePolicyAudit.objects.create(actor=actor, section="commerce", before_values=before_policy, after_values=after_policy)
    for code, method in methods.items():
        before = _snapshot_method(method)
        method.charge_mode = cleaned_data[f"{code}_mode"]
        method.fixed_amount_irt = cleaned_data.get(f"{code}_irt") or Decimal("0")
        method.fixed_amount_usd = cleaned_data.get(f"{code}_usd") or Decimal("0")
        method.eligible_for_free_shipping_threshold = cleaned_data[f"{code}_eligible"]
        method.full_clean()
        method.save()
        after = _snapshot_method(method)
        if before != after:
            CommercePolicyAudit.objects.create(actor=actor, section=f"delivery:{code}", before_values=before, after_values=after)
    return policy
