# TileCompany

**A bilingual Django commerce platform with a tile-store storefront and a reusable product catalog.**

TileCompany connects customer shopping with the operational workflows behind a store: catalog publishing, inventory and pricing, checkout, fulfillment, refunds, support, and content moderation. The storefront is branded **TileStore** and supports English and Persian, including RTL presentation.

Built by [Ali Rouzkhosh](https://github.com/AliRouzkhosh). This repository is an independent portfolio project; deployment configuration does not by itself establish a live production service.

## Highlights

- **Generic catalog:** product types, typed attributes, product/variant scope, variant dimensions, hierarchical categories, and publication validation. The same framework supports tiles and installation materials without moving price or stock away from the purchasable variant.
- **Variant-level commerce:** SKU-based inventory and separate USD and Iranian toman (`IRT`) prices. Checkout resolves current prices and stock, then persists purchase facts for historical orders.
- **Order and payment workflows:** shipping, payment, and review steps; cash on delivery; ZarinPal; payment retry and recovery; cancellation and refund handling. COD orders are confirmed but remain unpaid until settlement.
- **Commerce policies:** tax and delivery rules with independent currency thresholds, atomic updates, version checks, audit records, and order-time snapshots.
- **Staff dashboard:** catalog and inventory operations, CSV/XLSX import preview and application, fulfillment, support, moderation, and analytics, authorized through Django permissions and version-controlled role bundles.
- **Customer experience:** account/profile management, delivery addresses, order history, notifications, reviews, support tickets, help-center content, and blog articles.
- **Persian interface:** language-prefixed routes, RTL/LTR layouts, localized money and dates, and Iranian address and phone handling.

## Architecture

The project is a Django monolith with domain boundaries between catalog, accounts, orders, content, support, and staff operations.

| Responsibility | Approach |
| --- | --- |
| Reading data | Selectors and presentation contracts |
| Changing data | Services and explicit business workflows |
| Handling requests | Thin views coordinating forms, selectors, and services |
| Rendering | Django templates, reusable components, Tailwind CSS, and HTMX |
| Historical correctness | Persisted order pricing/policy values and purchase snapshots |
| Authorization | Django permissions checked at operational boundaries |

`ProductVariant` is the purchasable identity. `VariantPrice` belongs to a variant and currency. Product types and attributes describe catalog items; they do not replace the ownership of price, stock, media, or order lines. Legacy tile compatibility remains in the codebase.

## Technology

| Layer | Implementation |
| --- | --- |
| Backend | Python 3.12; Django 6.0.8 |
| Data | PostgreSQL; SQLite for lightweight development; Redis for shared cache and abuse controls |
| Interface | Django templates, Tailwind CSS v4, HTMX 2.0.4, JavaScript |
| Application server | Gunicorn 23.0.0 |
| Local deployment | Docker Compose, PostgreSQL 18.6, Redis 7.4.11, Nginx 1.28.0 |
| Imports and localization | openpyxl, phonenumbers, jdatetime |

[requirements.txt](requirements.txt) is the authority for runtime dependency pins. The local Docker image pins Python 3.12.12. The Liara staging contract targets PostgreSQL 16.3 separately from the local PostgreSQL 18 environment.

## Repository map

| Path | Purpose |
| --- | --- |
| `TileCompany/` | Settings, URL configuration, infrastructure checks, and application security utilities |
| `products/` | Generic catalog, variants, prices, inventory, imports, and storefront discovery |
| `accounts/` | Authentication, profiles, addresses, OTP infrastructure, and abuse controls |
| `orders/` | Cart, checkout, pricing policies, payments, order lifecycle, and refunds |
| `dashboard/` | Staff capabilities, operational editors, moderation, and analytics |
| `blog/`, `reviews/`, `support/`, `notifications/` | Content, feedback, customer support, and notifications |
| `templates/`, `theme/`, `static/`, `locale/` | Shared templates, frontend assets, and translations |
| `ops/` | Local deployment and isolated staging contracts |
| `performance_tests/` | Query-count tests and optional local load-testing tools |

## Run locally

For a lightweight review, use Python 3.12, Node.js with pnpm, a fresh local virtual environment, and SQLite. The following commands use **Windows PowerShell** and run from the repository root:

```powershell
git clone https://github.com/AliRouzkhosh/TileCompany.git
cd TileCompany
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
pnpm --dir static/vendor install --frozen-lockfile

$env:APP_ENV = "development"
$env:DB_BACKEND = "sqlite"
$env:CACHE_BACKEND = "locmem"
$env:DEBUG = "true"
$env:SMS_ENABLED = "false"
$env:SMS_OTP_LOGIN_ENABLED = "false"
# Keep email local during review; no SMTP server is needed for this backend.
$env:EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"

.\.venv\Scripts\python.exe manage.py migrate
.\.venv\Scripts\python.exe manage.py seed_dashboard_roles
.\.venv\Scripts\python.exe manage.py seed_demo_catalog --dry-run
.\.venv\Scripts\python.exe manage.py seed_demo_catalog --apply
.\.venv\Scripts\python.exe manage.py createsuperuser
.\.venv\Scripts\python.exe manage.py runserver 127.0.0.1:8000
```

The demo catalog command is opt-in, validates its writes, and seeds a controlled 50-product catalog. Without `--apply`, it runs a transaction that rolls back. It refuses `APP_ENV=production` and refuses to overwrite conflicting unrelated identities. Run it on a fresh review database.

Open [English](http://127.0.0.1:8000/en/) or [Persian](http://127.0.0.1:8000/fa/). The Django admin is at `/en/admin/`; the staff dashboard is at `/en/dashboard/` and uses the corresponding Persian language prefix when needed.

Compiled Tailwind CSS is included in `theme/static/css/dist/`. The pnpm command installs the locally served HTMX dependency from the included lockfile; a separate frontend development server is not required.

The root [.env.example](.env.example) documents configuration names and development placeholders. **It is not automatically loaded**: settings read the process environment. Copying it to `.env` alone does not configure the application. The settings module is `TileCompany/settings.py`.

These are source-derived development instructions. Production-only behavior, PostgreSQL concurrency, and reverse-proxy behavior need the separate environments below; a SQLite review does not validate them.

## Docker and staging

The [local production simulation guide](ops/local-prod/README.md) describes the full Compose setup, local credentials, certificates, and startup sequence. Use it for the hardened PostgreSQL/Redis/Nginx environment rather than reusing the development commands above.

- Compose configuration: `ops/local-prod/compose.yml`.
- Migration jobs and web workers use separate PostgreSQL roles.
- Web workers run as a non-root user and do not migrate on startup.
- Nginx exposes loopback-only HTTP/HTTPS ports; PostgreSQL and Redis have no host port mappings.
- Static output, public media, and private inventory-import files use distinct storage boundaries. Support/refund attachments remain protected by Django authorization.
- Readiness is exposed at `/_health/ready/`.

`local_production` is a local verification mode, not a public deployment mode. Email and SMS are disabled there; live online payment is unavailable, with a separate opt-in local simulator for controlled verification.

The [Liara staging contract](ops/liara/README.md) documents a separate deployment artifact and required release actions. Provider resources, secrets, disk wiring, and acceptance checks require deployment configuration. PostgreSQL 18-to-16 production data cutover remains a separate, unresolved migration task.

## Validation

Development checks:

```powershell
.\.venv\Scripts\python.exe manage.py check
.\.venv\Scripts\python.exe manage.py makemigrations --check --dry-run
.\.venv\Scripts\python.exe manage.py test
```

The repository contains tests for catalog integrity and publication, checkout and payment state, authorization, inventory imports, localization, notifications, uploads, browser security, readiness, and recovery. PostgreSQL-specific concurrency behavior requires a PostgreSQL test environment.

For infrastructure validation and performance runs, follow the [local environment guide](ops/local-prod/README.md) and [load-testing guide](performance_tests/load/README.md). The load harness uses isolated synthetic identities and avoids external SMS, email, payment calls, and final checkout submission. No benchmark or current passing-test count is claimed by this README.

## Further documentation

- [Storefront current-state checkpoint](docs/architecture/STOREFRONT_CURRENT_STATE.md) — implementation checkpoints and known validation limits.
- [Commerce policy](docs/architecture/COMMERCE_POLICY.md) — pricing authority, shipping semantics, snapshots, and policy authorization.
- [Catalog architecture contract](docs/architecture/Framework_Catalog_Architecture.md) — original architecture proposal; read alongside current code and the storefront checkpoint.
- [Persian design language](docs/persian-design-language.md).
- [Local infrastructure](ops/local-prod/README.md) and [Liara staging](ops/liara/README.md).

## License

No project-wide license file is present. Third-party assets retain their own notices, including the Inter font license. Public repository visibility does not assign a new license to this application.

