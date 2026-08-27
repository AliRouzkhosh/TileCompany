# TileCompany Storefront — Current State

Last updated: 2026-08-19

## 1. Purpose

This file is the compact authoritative checkpoint for storefront work.

Codex should read this file before each new storefront phase.

Earlier frozen phases must NOT be reopened unless:
- the current phase demonstrates a real regression, or
- the prompt explicitly authorizes a change.

Do not reconstruct project history from old reports unless needed for a specific defect.

---

# 2. Architecture

Stack:
- Django 6
- Python 3.12
- Tailwind CSS v4
- EN / FA single-codebase storefront
- RTL Persian / LTR English
- Vazirmatn for Persian
- Inter for English

Architecture:
- selectors for reads
- services for writes
- thin views
- reusable components
- mobile-first
- semantic storefront tokens
- logical RTL/LTR CSS

Commerce identity:
- ProductVariant is the purchasable identity
- VariantPrice is currency-specific
- USD + IRT/Toman
- ZarinPal uses exact integer Toman values

Generic ProductType / Product / Variant attribute framework is functionally complete.

Tile legacy compatibility remains intentionally present.
Do not perform legacy Tile cutover/cleanup during storefront phases.

---

# 3. Frozen Visual System

Brand direction:

Premium trust + friendly warmth + purchase/conversion energy.

Key colors:

- Page: #F8F7F5
- White: #FFFFFF
- Ivory: #FCFBF9
- Charcoal: #211C18
- Secondary text: #62574E
- Copper: #C76035
- Copper hover: #B64F28
- Copper active: #96401E
- Success: #2F7D5B
- Warning: #B7791F
- Error: #B64545
- Info: #356A9A

Radius:
- chips: 8px
- controls: 12px
- cards: 16px
- hero: 24px

Minimum interactive target:
44px

Use Lucide-like line icons.
No emoji UI.

---

# 4. Frozen Storefront Phases

## V1 — Global Shell / Navigation

FROZEN.

Includes:
- desktop header
- mobile header
- mega menu
- mobile drawer
- search
- notification badge
- shared store container
- RTL/LTR shell

Do not redesign.

---

## V2 — Homepage / Discovery / Footer

FROZEN.

Includes:
- homepage hero
- category discovery
- shared product discovery cards
- article discovery
- trust/support sections
- footer
- merchandising images

Do not redesign.

---

## V3 — Catalog / Search

FROZEN.

Includes:
- mobile compact product rows
- desktop catalog cards/sidebar
- filter drawer
- sorting
- pagination
- empty states
- generic variant-aware filtering

Do not redesign.

---

## V4 — Product Detail

FROZEN.

Includes:
- gallery
- purchase panel
- exact Variant resolver
- Add Cart / Buy Now
- selected Variant details
- Product details
- Dimensions & packaging
- tabs
- reviews
- related products
- mobile purchase CTA behavior

Do not alter resolver semantics or commerce identity.

---

# 5. V5 — Cart / Checkout / Payment

V5 commerce architecture is FROZEN.

Final automated baseline:
676 tests passed.

Final cross-site visual/browser matrix was not completed because the Codex
browser runtime failed before rendering localhost.

This is a tooling limitation, not a known commerce defect.

Outstanding visual verification is deferred to V7 final storefront QA.

## V5A Cart

Frozen:
- compact commerce-specific line items
- quantity AJAX
- authoritative server-formatted money
- responsive Order Summary
- current removal behavior

## V5B Checkout Shell

Frozen flow:

Shipping
→ Payment
→ Review

Cart is NOT a Checkout step.

## V5C Checkout Steps

Frozen:
- address selection
- shipping method
- payment choice
- Review
- COD
- ZarinPal
- USD development provider

Do NOT reintroduce delivery_slot into the active checkout flow.

Coupon UI remains informational only.
There is no active coupon-calculation contract.

## V5D Payment / Result

Frozen business rules:

Order creation:
- one durable checkout-attempt token
- immutable Order / OrderItem creation
- inventory reserved at Order creation
- Cart detached after successful Order creation

COD:
- confirmed
- unpaid
- no gateway
- no Retry Payment

Online success:
- paid only after provider verification
- confirmed
- repeat callbacks are idempotent

Online failure/cancellation:
- Order remains pending + unpaid
- inventory remains reserved while retryable
- recovery uses existing payment_start authority

Result truth:
- persisted Order state overrides transient callback data
- cancelled Order overrides late callback success
- payment reference appears only when paid

## Development ZarinPal Simulator

Development-only Toman/ZarinPal simulator supports:
- success
- failure
- customer cancellation

It injects provider outcomes only.
It must never directly mutate Order/payment state.

Production ZarinPal remains unchanged.

---

# 6. Payment Recovery

Frozen authority:

Order.can_start_online_payment

Do NOT introduce a separate needs_payment flag.

## Notifications

Failed and cancelled online payments produce localized recovery notifications.

Notifications:
- are created at the authoritative callback boundary
- are deduplicated
- remain historical after later success
- now link to customer Order Detail

## Global Payment Attention

Authenticated users with retryable unpaid online Orders see a compact normal-flow
attention strip below the global navbar.

One eligible Order:
- shows Order reference
- action: Review and pay
- destination: customer Order Detail

Multiple eligible Orders:
- aggregate count
- action: View orders

COD, paid, cancelled, and expired Orders do not appear.

The strip is NOT fixed or sticky.

## Order-First Recovery

Recovery flow:

Notification / global reminder
→ customer Order Detail
→ Complete payment
→ guarded payment_start
→ provider

Customer Order Detail exposes Complete payment only when:

order.can_start_online_payment == True

---

# 7. One Retryable Online Order Rule

Normal storefront checkout allows at most:

ONE concurrent retryable unpaid online Order per customer.

Shared selector:

orders.selectors.get_retryable_online_orders_for_user()

Normal Review Order creation uses:

enforce_single_retryable_online_order=True

Rules:

- same checkout-attempt token still resolves the same Order
- separate online attempt is blocked when an eligible Order already exists
- new Cart / checkout state remains intact
- COD does not count
- paid/cancelled/expired Orders do not block
- legacy/admin multiple rows remain readable

Concurrency:

Authenticated User row is locked with select_for_update()
inside the Order-creation transaction.

This provides PostgreSQL serialization for separate Review attempts.

Do not replace this with a broad customer-level database unique constraint.

---

# 8. Stale Unpaid Orders

Existing management command:

python manage.py cancel_stale_orders --hours 24

Default:
24 hours.

Targets:
- pending
- unpaid
- online Mock/ZarinPal Orders

Outcome:
- Order cancelled
- can_start_online_payment becomes false
- inventory restored exactly once using inventory_restored_at
- payment-attention strip disappears

IMPORTANT:

The cleanup command currently exists but is NOT automatically scheduled.

Production Hardening must configure this command with:
- cron
- systemd timer
- hosting-platform scheduler
- or equivalent

Do not introduce Celery/APScheduler during storefront V6 solely for this.

---

# 9. Current Known Deferred Work

Do not address during V6 unless explicitly requested:

- final V5 browser matrix → V7
- stale-order production scheduler → Production Hardening
- Tile generic-authority cutover → later backend phase
- production provider/environment hardening → later production phase
- AR Room Visualizer → post-launch feature

---

# 10. V6 Scope

V6 = Customer Account & Post-Purchase Experience.

Planned stages:

V6A
Customer-area audit + shared shell/navigation
## V6A — Customer Area Shared Shell

FROZEN.

Final automated baseline:
679 tests passed.

Implemented:
- unified customer Account shell
- Account identity context
- customer navigation:
  - Overview
  - Orders
  - Wishlist
  - Addresses
  - Profile
  - Notifications
  - Support
- < lg: horizontal scrollable 44px account navigation
- >= lg: restrained non-sticky left navigation rail
- one EN/FA markup path
- aria-current active destination
- Overview, Profile, and Addresses migrated to shared shell

Do not redesign or replace the Account shell during V6B–V6F.

Outstanding browser visual verification remains deferred to V7 due to the
known Codex browser-runtime limitation.

V6B
Account overview + Profile + Addresses
## V6B — Account Overview / Profile / Addresses

FROZEN.

Latest full-suite baseline before final responsive-order correction:
683 tests passed.

Final V6B.5.1 focused Account suite:
19 tests passed.

Account Overview:
- compact premium customer identity surface
- truthful name/email/optional phone/business context only
- My Orders aggregate:
  - Current
  - Delivered
  - Cancelled
- one grouped Order-status query
- retryable pending online Orders remain V5 payment-recovery owned
- compact default shipping-address surface
- no raw recent-order list
- no redundant shortcuts

Mobile Overview order:
1. Identity
2. My Orders
3. Default Address

Desktop Overview:
- Identity + Default Address peer surfaces
- My Orders beneath across main content

Profile:
- Personal / Business grouping
- existing form semantics preserved
- premium Account visual system

Addresses:
- compact information-first cards
- existing ownership/default/create/edit/delete behavior preserved
- checkout compatibility preserved

Account navigation:
- mobile horizontal swipe rail
- internal scrolling only
- hidden scrollbar
- 44px targets
- active state uses aria-current
- active destination auto-reveals with scoped scrollIntoView()
- no focus stealing
- desktop Account rail remains non-sticky

Do not reopen V6B during V6C–V6F without a demonstrated regression.

V6C
Orders list
## Discount & Savings Continuity

FROZEN.

Final automated baseline:
697 tests passed.

Authoritative pricing:
- VariantPrice.amount = selling/charged price
- VariantPrice.compare_at_amount = same-currency reference price
- OrderItem.unit_price = immutable historical paid unit price
- OrderItem.unit_reference_price = nullable immutable purchase-time reference price

Visual contract:

Per discounted item:
- selling price strongest
- reference price struck through
- compact percentage discount

Financial summary:
- Items at original price
- Product discounts
- Subtotal
- Shipping
- Tax
- Total

Do not show duplicate per-line or aggregate "You save" callouts.

Legacy OrderItems without unit_reference_price make no historical discount claim.

Do not read current VariantPrice when presenting historical Order pricing.

This pricing presentation is generic across ProductTypes and must be reused by
V6D Order Detail without redesign.

V6D
Order Detail
## V6D.1 — Customer Order Detail

FROZEN.

Final baseline:
704 tests passed.

Frozen contracts:
- Order Detail lives inside the V6 Account shell
- Orders remains the active Account destination
- current status/action appears before Order contents
- `can_start_online_payment` remains the sole online-payment recovery authority
- COD unpaid state is distinct from Payment Required
- all OrderItems render from historical generic purchase presentation
- Product-level current image/title may link to current Product Detail when safely public
- current Product data is navigation/media aid only, never historical purchase truth
- all OrderItems render on Order Detail; V6C 2+N limit does not apply
- historical paid/reference prices remain immutable
- historical discount remains visible as restrained value reinforcement
- no current VariantPrice lookup for Order history
- historical delivery/address/payment data is authoritative
- generic Tile/Laptop/Accessory/arbitrary ProductTypes use the same components
- 1-item and 12-item Orders remain bounded without N+1
## V6D — Order Detail, Refunds & Clarification

FROZEN.

Final automated baseline:
718 tests passed.

Frozen contracts:

- V6D.1 Customer Order Detail is final.
- Order Detail uses historical generic purchase presentation.
- Product-level current imagery/navigation is recognition/navigation aid only.
- historical pricing never reads current VariantPrice.
- historical discounts remain visible as restrained purchase-time value context.
- Payment Required uses only can_start_online_payment.
- COD remains distinct from online payment recovery.
- Refund eligibility/window/amount authority remains backend-owned.
- Refund Additional information is required for new requests.
- Refund clarification uses NEEDS_INFORMATION without changing Order.status.
- Refund ↔ Support Ticket is an explicit owned one-to-one workflow.
- Refund status describes refund phase.
- Ticket status describes whose turn it is:
  - waiting_customer = waiting for customer
  - waiting_support = customer replied / staff action required
- linked refund Ticket is reused across clarification rounds.
- customer replies never automatically approve/reject/process refunds.
- active refund workflow remains excluded from fulfillment until resolved.
- approval routes to refund processing.
- rejection returns Order to its authoritative normal lifecycle.
- clarification notifications link directly to the owned Support Ticket.
- shared customer file-upload language is established for Refund and Support.
- refund/support ownership and security boundaries are frozen.

Do not reopen V6D in later V6 phases without a demonstrated regression.
Do not reopen V6D.1 during V6D.2 without a demonstrated regression.
V6E
Notifications
## V6E — Customer Notifications Center

FROZEN.

Final contracts:

- Notifications Center belongs to the V6 Account shell.
- Notifications is the active Account destination.
- mobile active-item auto-reveal remains enabled.
- one shared notification-row architecture is used for all event types.
- unread state is visually and semantically distinct.
- notification destinations remain server-authoritative.
- payment recovery remains Order-first.
- refund clarification links to the owned Support Ticket.
- historical/stale notifications remain safe.
- read/unread counts use the same authoritative definition across header,
  Account navigation and Notifications Center.
- Persian notification chrome and event presentation are localized.
- Lucide notification icons render from the presentation-layer icon mapping.
- Account activity / unread-count translations are corrected.
- no notification business/event-generation semantics were redesigned.

Do not reopen V6E without a demonstrated regression.

V6F
Wishlist + Support consistency + final customer-area freeze

Do not perform all V6 stages in one task.

---

# 11. Codex Efficiency Rules

For each phase:

1. Read this file first.
2. Inspect only files directly relevant to the requested phase.
3. Do not crawl frozen storefront phases without a demonstrated dependency.
4. Run focused tests during implementation.
5. Run full suite only at a meaningful checkpoint/freeze.
6. Do not repeat frozen architecture in reports.
7. Keep reports concise.
8. Stop at the requested checkpoint.