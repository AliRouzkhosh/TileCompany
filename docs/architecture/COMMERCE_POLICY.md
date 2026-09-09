# Commerce policy source of truth

`orders.CommercePolicy` is the singleton source for tax enablement/rate and independent IRT/USD free-shipping thresholds. `orders.DeliveryMethodPolicy` owns the three system-controlled methods (`standard`, `express`, `pickup`), their charge mode, independent IRT/USD fixed amounts, and threshold eligibility. Cross-request policy caching is intentionally not used.

`orders.pricing.calculate_pricing()` is the authoritative calculator. Its qualifying subtotal is the existing discounted merchandise subtotal: current `VariantPrice.amount × quantity`, before shipping and tax. Tax keeps that pre-feature basis and is rounded once (whole Toman for IRT, cents for USD). Delivery precedence is direct free, threshold free, pay on delivery, then fixed fee. Pay-on-delivery freight is a semantic state and is never included in `total_payable_now`.

Order creation re-locks current catalog prices and policy rows, recalculates, then snapshots the tax rate/amount, effective shipping mode, threshold-free flag, collected shipping amount, total, and policy version. Historical pages, emails, payments, analytics, and refunds use those persisted Order values; they never consult today's policy. Consequently, freight collected separately by a carrier is absent from TileStore's payment and refund amount.

The custom Dashboard page is `/dashboard/settings/commerce/`. It requires `orders.view_commercepolicy` to read and both `orders.change_commercepolicy` and `orders.change_deliverymethodpolicy` to write. The version-controlled Order Manager bundle receives those four view/change permissions; other operational roles are unchanged. Writes are atomic, lock all policy rows, reject stale versions, and append non-editable `CommercePolicyAudit` before/after snapshots.

After migrations, run `python manage.py seed_dashboard_roles` using the existing deployment role-synchronization workflow.
