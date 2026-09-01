# Liara isolated staging deployment contract

This directory prepares the **web application artifact only** for a future,
isolated Liara staging deployment. It does not create Liara resources, set
secrets, configure a domain, or change local production.

## Artifact contract

- `Dockerfile` preserves the local-production Python 3.12.12 slim-bookworm,
  `/app`, dependency, `pip check`, and non-root `tilecompany` (UID/GID 10001)
  image contract.
- `start-web.sh` is the Liara-only PID 1 command. It uses `exec gunicorn`,
  binds `0.0.0.0:${PORT:-8000}`, runs two workers with a 60-second timeout,
  preserves error/application output, and deliberately does not enable
  Gunicorn request access logging.
- It never runs migrations, `collectstatic`, ownership changes, or privileged
  setup. Migrations are not part of normal web startup.
- `liara.json` uses only documented provider-neutral fields: port `8000` and
  the Dockerfile path. It intentionally contains no app ID, platform, domain,
  endpoint, account identifier, or secret. Liara's documented console/GitHub
  flow supplies application identity; if a later CLI workflow requires an app
  field, keep that account-specific value out of this committed file.

Liara's documented Docker deployment accepts a Dockerfile and one HTTP port.
The staging console must configure port `8000`; Liara's runtime `PORT` value is
honored when supplied.

## Required staging configuration (names only)

Set these in the isolated staging application configuration, never in Git:

```text
APP_ENV=production
DJANGO_SECRET_KEY
ALLOWED_HOSTS
CSRF_TRUSTED_ORIGINS
SITE_URL
DB_BACKEND=postgresql
POSTGRES_DB
POSTGRES_USER
POSTGRES_PASSWORD
POSTGRES_HOST
POSTGRES_PORT
POSTGRES_CONNECT_TIMEOUT
POSTGRES_SSLMODE
POSTGRES_SSLROOTCERT (only if provider TLS policy requires it)
CACHE_BACKEND=redis
REDIS_URL
STATIC_ROOT=/app/staticfiles
EMAIL_BACKEND=TileCompany.email_backends.LocalProductionDisabledEmailBackend
EMAIL_HOST=staging.invalid
EMAIL_PORT=465
EMAIL_USE_TLS=false
EMAIL_USE_SSL=true
DEFAULT_FROM_EMAIL
SERVER_EMAIL
SMS_ENABLED=false
SMS_OTP_LOGIN_ENABLED=false
SMS_BACKEND=accounts.sms.DisabledSmsBackend
ZARINPAL_SANDBOX=false
ZARINPAL_DEVELOPMENT_AUTO_SUCCESS=false
MOCK_PAYMENT_SIMULATION_ENABLED=false
LOCAL_PAYMENT_SIMULATOR_ENABLED=false
TRUST_PROXY_SSL_HEADER=false
TRUST_PROXY_CLIENT_IP=false
```

The existing `LocalProductionDisabledEmailBackend` is deliberately fail-closed:
it does not open SMTP connections and raises if a message would otherwise be
sent. The syntactically explicit non-local email settings above satisfy the
production configuration contract without enabling mail delivery. Initial
staging must not send customer mail.
No production merchant, SMTP, SMS, database, or Redis credential belongs in
this repository.

`TRUST_PROXY_SSL_HEADER` and `TRUST_PROXY_CLIENT_IP` remain false until the
Liara staging forwarded-header behavior is measured. Do not trust arbitrary
`X-Forwarded-*` headers.

## Disks and static content

Attach separate persistent staging disks at exactly:

```text
/app/media
/app/private_inventory_price_import_sources
```

Both mount roots require one explicit, provider-approved initialization action
to create the directory if needed and set owner `10001:10001`, mode `0755`.
That action must be scoped to those roots and must not become a root web-startup
wrapper. The private inventory disk must never be mapped to `MEDIA_URL` or an
anonymous edge location.

`STATIC_ROOT=/app/staticfiles` is regenerable. Run `collectstatic` as an
explicit release action. The first staging deployment must validate whether the
retained Nginx application can safely consume a generated static artifact or a
provider-supported shared disk. Until that disk behavior is proven, do not
assume cross-application disk sharing and do not make Django serve production
static files.

The frozen Nginx security contract remains required: privacy-safe edge access
logging, public-prefix-only media delivery, and Django-authorized protected
media. Liara Nginx disk wiring is a 2F-4 staging validation item, not an
implementation in this change.

## Controlled staging release sequence

1. Create isolated staging web, PostgreSQL 16.3, Redis, network, disks, and a
   staging-only hostname through approved provider actions.
2. Add the required environment values, with outbound email, SMS, and payment
   disabled/simulated safely. Do not add production credentials or callbacks.
3. Attach the two disks, then perform the one-shot ownership initialization.
4. Build the immutable web image from `ops/liara/Dockerfile`.
5. Explicitly run, against the same artifact and private PostgreSQL:

   ```sh
   python manage.py migrate --noinput
   python manage.py collectstatic --noinput
   ```

6. Deploy/redeploy the web application. Configure provider health checking for
   `/_health/ready/`; provider-specific interval, timeout, grace, and retry
   values must be confirmed in the console rather than guessed here.
7. After Nginx disk wiring is validated, deploy/redeploy the staging Nginx
   application and verify HTTPS.
8. Verify readiness, public routes, static/public media, protected-media
   denial, uploads, logging privacy, backups/exports, and monitoring signals.

Required smoke responses are `200` for `/_health/ready/`, `/en/`,
`/en/shop/`, and `/en/accounts/login/`, plus a representative product and
article. Verify normal and rejected uploads around the existing 5 MiB, 10 MiB,
and local 32 MiB boundaries using synthetic files only.

Use provider-level access restriction/authentication and non-production DNS to
prevent staging indexing. Do not add a staging-only noindex header to shared
application behavior without a separate reviewed, environment-gated change.

## PostgreSQL 16 record and production-cutover debt

Liara staging targets PostgreSQL 16.3. TileCompany has proven empty migrations,
schema/constraints, transaction concurrency, restored-data smoke, and PG16 to
PG16 logical backup/restore on that major.

Direct PostgreSQL 18 custom-format restore into PostgreSQL 16 is not a valid
production cutover procedure: PG18 output includes target-incompatible session
and meta commands. Production PG18-to-PG16 data cutover remains blocked on a
separate approved logical-migration workflow. That stage must use supported
tooling, reproducible artifacts, sequence/constraint and aggregate validation,
application-read validation, a post-migration backup, a quiesce/downtime plan,
and rollback. Do not use the narrow experimental filtering proof as production
automation.

## Staging acceptance and operations baseline

Inspect provider and application logs with synthetic requests: ordinary 404,
sensitive password-reset-style path, query marker, and harmless application
error path. Preserve no Gunicorn access log, Django sensitive-path redaction,
and Nginx privacy-safe access logging where Nginx is retained.

Collect Liara CPU/RAM/network and database/Redis/storage health. Add external
or APM components later for route percentiles, traces, uptime, and visitor
analytics. Initial operating guardrails—not fixed alert policy—are sustained
application CPU or RAM around 75–80%, p95 dynamic latency around one second or
worse, worker timeouts/restarts, database pressure/connection headroom, and
storage around 75–80%. Confirm backup export/download and restore procedures
before staging holds meaningful data.

## 2F-4 staging validation still required

- actual Liara health-check timing and Docker `PORT` injection;
- forwarded-header/HTTPS behavior before enabling proxy trust;
- persistence and replacement/redeploy behavior of each disk;
- whether a disk can safely be shared with a separate Nginx application;
- Nginx static/public-media wiring while preserving protected-media denial;
- provider access restriction, staging DNS/TLS, no-index policy, and public
  DB/Redis exposure closure;
- backup/export/restore, monitoring, external uptime, and upload-limit proof.
