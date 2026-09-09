# Local production simulation: PostgreSQL and Redis

This directory defines the isolated local production-verification environment. It contains PostgreSQL, Redis, a Django/Gunicorn image used by separate migration and web services, and Nginx as the only browser-facing service. It does not include demo data or real external-provider configuration.

The PostgreSQL database is a clean Docker-managed database and is completely independent of the Windows PostgreSQL instance and its `tilecompany_dev` database. Do not use, dump, restore, migrate, or reuse credentials from `tilecompany_dev` for this simulation.

PostgreSQL and Redis deliberately have no host port mappings. They are reachable only by services attached to the private Docker `backend` network. This is intentional security isolation, not an omission. Later, Django/Gunicorn can join `backend` and a separate proxy-facing network; browsers must never reach either data service directly.

## Setup

From the repository root, create the ignored local environment file and choose unique local-only credentials:

```powershell
Copy-Item ops/local-prod/.env.example ops/local-prod/.env
```

Edit `ops/local-prod/.env`. Do not commit it, do not place real credentials in `.env.example`, and use a distinct password for each PostgreSQL role.

The required PostgreSQL variables separate three credentials:

- `LOCALPROD_POSTGRES_BOOTSTRAP_USER` / `LOCALPROD_POSTGRES_BOOTSTRAP_PASSWORD`: the privileged `POSTGRES_USER` account that initializes and administers the local PostgreSQL cluster. Django must never use this account.
- `LOCALPROD_POSTGRES_MIGRATION_USER` / `LOCALPROD_POSTGRES_MIGRATION_PASSWORD`: the non-superuser account for deliberate Django migration jobs. It owns the application database and `public` schema, and can create the schema objects migrations require.
- `LOCALPROD_POSTGRES_RUNTIME_USER` / `LOCALPROD_POSTGRES_RUNTIME_PASSWORD`: the non-superuser account for long-running Django/Gunicorn workers. It has runtime data privileges only and does not own the database, schema, or tables.

Use clearly distinct role names, such as `tilecompany_localprod_admin`, `tilecompany_localprod_migrator`, and `tilecompany_localprod_runtime`.

Using `POSTGRES_USER` as Django's credential would give the web process the image bootstrap superuser and violate least privilege. Migrations must use the migration credential: default privileges for objects it creates automatically grant the runtime role table `SELECT`, `INSERT`, `UPDATE`, `DELETE` and sequence `USAGE`, `SELECT` permissions.

## Environment modes

- `development` is for developer convenience.
- `local_production` is only for local infrastructure and security verification. It keeps the hardened production posture but disables live external integrations and permits only explicit local hosts (`localhost`, `127.0.0.1`, `[::1]`, and `tilecompany.localhost`).
- `production` is for real deployment and retains every live SMTP and ZarinPal requirement.

`local_production` is not staging and is not an acceptable public deployment mode. It rejects public hostnames, requires PostgreSQL and Redis, forces `DEBUG=False`, secure cookies, HTTPS redirect, HSTS defaults, CSP/browser protections, and a strong secret. Email uses a backend that raises clearly without opening SMTP, and online payment has no merchant ID, sandbox, mock, or auto-success setting; a payment attempt therefore fails before any ZarinPal request. SMS remains explicitly disabled. Real provider behavior is deferred to staging/Liara verification.

The initialization script runs only during the official image's empty-volume initialization. This command applied only to the original empty first-start volume, before Django migrations existed:

```powershell
docker compose --env-file ops/local-prod/.env -f ops/local-prod/compose.yml down -v
docker compose --env-file ops/local-prod/.env -f ops/local-prod/compose.yml up -d postgres redis
```

Do not use `down -v` on a populated volume: it deletes the Docker PostgreSQL data volume. The current migrated volume must be retained.

## Build, migrate, and start

Run these commands from the repository root:

```powershell
# Build the one shared application image. The Dockerfile runs pip check during the build.
docker compose --env-file ops/local-prod/.env -f ops/local-prod/compose.yml build migrate web

# Start only the private data services, then deliberately run migrations once.
docker compose --env-file ops/local-prod/.env -f ops/local-prod/compose.yml up -d postgres redis
docker compose --profile migration --env-file ops/local-prod/.env -f ops/local-prod/compose.yml run --rm migrate

# Collect into the shared static volume with the migration-capable one-shot job.
docker compose --profile migration --env-file ops/local-prod/.env -f ops/local-prod/compose.yml run --rm migrate python manage.py collectstatic --noinput

# The web service never migrates automatically and receives runtime DB credentials only.
docker compose --env-file ops/local-prod/.env -f ops/local-prod/compose.yml up -d web
docker compose --env-file ops/local-prod/.env -f ops/local-prod/compose.yml up -d nginx
docker compose --env-file ops/local-prod/.env -f ops/local-prod/compose.yml ps
docker compose --env-file ops/local-prod/.env -f ops/local-prod/compose.yml logs --follow web nginx
docker compose --env-file ops/local-prod/.env -f ops/local-prod/compose.yml exec web python manage.py check
docker compose --env-file ops/local-prod/.env -f ops/local-prod/compose.yml exec web python manage.py migrate --check
docker compose --env-file ops/local-prod/.env -f ops/local-prod/compose.yml exec postgres sh -c 'pg_isready -U "$POSTGRES_USER" -d "$POSTGRES_DB"'
docker compose --env-file ops/local-prod/.env -f ops/local-prod/compose.yml exec redis sh -c 'redis-cli --no-auth-warning -a "$REDIS_PASSWORD" ping'
```

Before starting Nginx, complete the local certificate preparation below. The `ps` output reports Compose health status. The web healthcheck is a container-local TCP listener probe, not an HTTP request, so it does not disable Django's HTTPS redirect. Gunicorn has two workers, runs as the non-root `tilecompany` image user, binds only to the Docker network, and has no Windows host port.

`collectstatic` writes to the named `staticfiles` volume through the deliberate one-shot migration service. Nginx and web mount that volume read-only; Nginx serves `/static/` directly and Gunicorn does not serve static files. Rerun the collectstatic command after changing static assets or rebuilding an image that changes them.

## Persistent uploaded media

`media_data` is a separate named volume for operator and customer uploads. The
one-shot `media-init` service owns only its `/app/media` mount as UID/GID
`10001`, then `web` mounts it read-write while still running as the non-root
`tilecompany` user. Nginx mounts the same volume read-only at `/var/www/media`.
It directly serves only these public prefixes: `products/`,
`storefront/homepage/`, `storefront/categories/`, `storefront/discovery/`, and
`blog/`. There is deliberately no general `/media/` location: support and
refund attachments continue to use authenticated Django download views, and
inventory-import sources are stored outside `MEDIA_ROOT`.

## Private inventory-import evidence

`private_inventory_import_data` is a second named volume, mounted only in the
web service at `/app/private_inventory_price_import_sources`. The application
receives that exact path through `PRIVATE_INVENTORY_IMPORT_STORAGE_ROOT`; Nginx
does not mount this volume and has no location block for it. `media-init` owns
this mount as UID/GID `10001` with mode `0755` before non-root Gunicorn starts.

To prove persistence without deleting any volume, create a source through the
authorized import workflow, record its storage key, then run:

```powershell
docker compose --env-file ops/local-prod/.env -f ops/local-prod/compose.yml up -d --force-recreate web
docker compose --env-file ops/local-prod/.env -f ops/local-prod/compose.yml exec web sh -c 'id; test -w /app/private_inventory_price_import_sources; find /app/private_inventory_price_import_sources -type f -print'
curl.exe -k -I https://tilecompany.localhost/media/products/inventory-price-imports/guessed.csv
curl.exe -k -I https://tilecompany.localhost/static/products/inventory-price-imports/guessed.csv
curl.exe -k -I https://tilecompany.localhost/private_inventory_price_import_sources/guessed.csv
```

The source remains available to its authorized dashboard workflow after only
`web` recreation. Each guessed public path must be unserved (404/403, never
the source bytes). Do not use `docker compose down -v` for this verification.

After changing the Docker image or Compose configuration, rebuild and recreate
only the affected services (do not use `down -v`):

```powershell
docker compose --env-file ops/local-prod/.env -f ops/local-prod/compose.yml build web migrate
docker compose --env-file ops/local-prod/.env -f ops/local-prod/compose.yml up -d --force-recreate web nginx
docker compose --env-file ops/local-prod/.env -f ops/local-prod/compose.yml exec web sh -c 'id; ls -ld /app/media; touch /app/media/.write-test; rm /app/media/.write-test'
docker compose --env-file ops/local-prod/.env -f ops/local-prod/compose.yml exec nginx sh -c 'ls -ld /var/www/media; test ! -w /var/www/media'
```

The first command that starts `web` also runs the idempotent `media-init`
service. The expected web permission line is owned by `tilecompany tilecompany`
with mode `drwxr-xr-x`; Nginx has no write mount. Recreating `web` and `nginx`
does not remove `media_data`.

The image rebuilds only when `docker compose build` runs. `docker compose restart web` restarts the existing container and does not rebuild the image. To stop or restart just Gunicorn, use `docker compose stop web` or `docker compose restart web` with the same `--env-file` and `-f` arguments.

The migration job is intentionally one-shot and must be invoked deliberately. It receives only `LOCALPROD_POSTGRES_MIGRATION_*` through Django's `POSTGRES_USER/PASSWORD`; the web service receives only `LOCALPROD_POSTGRES_RUNTIME_*`. Neither service receives bootstrap credentials, and the web service cannot receive migration credentials from its Compose environment.

Once Django migrations have run, `postgres_data` contains valuable schema state. Do not use `docker compose down -v` casually or as a normal restart mechanism.

## Local TLS and Nginx

Nginx is the only service with Windows host ports, bound loopback-only as `127.0.0.1:80:80` and `127.0.0.1:443:443`. Web, PostgreSQL, and Redis have no host mappings. Nginx joins only the `proxy` network; web joins `proxy` and the internal `backend`; PostgreSQL and Redis remain `backend`-only.

Create owner-machine local certificates in the ignored `ops/local-prod/certs/` directory. Nginx expects exactly:

- `ops/local-prod/certs/tilecompany.localhost.pem`
- `ops/local-prod/certs/tilecompany.localhost-key.pem`

For example, after installing [mkcert](https://github.com/FiloSottile/mkcert) and its local CA, run from the repository root:

```powershell
mkcert -cert-file ops/local-prod/certs/tilecompany.localhost.pem -key-file ops/local-prod/certs/tilecompany.localhost-key.pem tilecompany.localhost localhost 127.0.0.1 ::1
```

Browser trust is an owner-machine action. A mkcert certificate is for this local verification environment only; it is neither a public CA certificate nor production TLS. The certificate directory has its own `.gitignore`, so neither certificate nor private key is tracked.

HTTP redirects solely based on Nginx's port-80 listener to `https://tilecompany.localhost`; it does not trust incoming `X-Forwarded-Proto`. On HTTPS, Nginx overwrites `Host`, `X-Forwarded-Proto=https`, and `X-Real-IP=$remote_addr`, and clears `X-Forwarded-For`. Web enables the existing explicit proxy-trust flags only behind that proxy network. Django remains authoritative for CSP, HSTS, frame, nosniff, referrer, cookie, and other application security headers.

Nginx has no general `/media/` route—only the documented public-media prefix allowlist. This is intentional: support/refund attachments remain private through authorized Django download views. `client_max_body_size 32m` is the aggregate reverse-proxy request ceiling only; it does not replace Django's per-file upload, import, or decoder validation.

## HTTPS owner validation

After creating the certificate and starting the stack, run:

```powershell
docker compose --env-file ops/local-prod/.env -f ops/local-prod/compose.yml config --quiet
docker compose --env-file ops/local-prod/.env -f ops/local-prod/compose.yml exec nginx nginx -t
docker compose --env-file ops/local-prod/.env -f ops/local-prod/compose.yml ps
docker compose --env-file ops/local-prod/.env -f ops/local-prod/compose.yml exec nginx ls -la /var/www/static
curl.exe -I http://tilecompany.localhost
curl.exe -I -H "X-Forwarded-Proto: https" http://tilecompany.localhost
curl.exe -I https://tilecompany.localhost
curl.exe -I https://tilecompany.localhost/static/
docker compose --env-file ops/local-prod/.env -f ops/local-prod/compose.yml exec nginx nginx -T
```

Both HTTP commands must return an HTTPS redirect even with the spoofed header. The HTTPS command should use the owner-trusted certificate and must not loop. Inspect the HTTPS headers for Django's hardened policy and inspect `nginx -T` to confirm header overwrite/cleared XFF, 32m limit, and no `/media/` route. Use Docker network inspection to confirm Nginx has no `backend` attachment; PostgreSQL, Redis, and web must show no host port mappings.

## Django/Redis validation

After the web container is healthy, a harmless cache probe confirms the existing Django Redis backend rather than LocMem:

```powershell
docker compose --env-file ops/local-prod/.env -f ops/local-prod/compose.yml exec web python manage.py shell -c "from django.core.cache import cache; cache.set('localprod_probe', 'ok', 30); assert cache.get('localprod_probe') == 'ok'"
docker compose --env-file ops/local-prod/.env -f ops/local-prod/compose.yml exec web sh -c 'test -z "$LOCALPROD_POSTGRES_BOOTSTRAP_PASSWORD" && test -z "$LOCALPROD_POSTGRES_MIGRATION_PASSWORD"'
```

For later multi-worker abuse-control proof, issue the same harmless cache probe through code executed by each Gunicorn worker and verify a value written by one is read by the other. Browser/auth throttling and proxy/TLS behavior wait for the reverse-proxy checkpoint.

## Role validation after empty-volume recreation

Run these only after the owner recreates the empty PostgreSQL volume and the database is healthy. They use environment variables inside the PostgreSQL container, so passwords are not placed in command lines or output.

```powershell
# A and B: both non-bootstrap roles have no superuser, CREATEDB, CREATEROLE, REPLICATION, or BYPASSRLS capability.
docker compose --env-file ops/local-prod/.env -f ops/local-prod/compose.yml exec postgres sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -P pager=off -c "SELECT rolname, rolsuper, rolcreatedb, rolcreaterole, rolreplication, rolbypassrls FROM pg_roles WHERE rolcanlogin ORDER BY rolname;"'

# C: expected to fail (runtime role has no CREATE on public).
docker compose --env-file ops/local-prod/.env -f ops/local-prod/compose.yml exec postgres sh -c 'PGPASSWORD="$LOCALPROD_POSTGRES_RUNTIME_PASSWORD" psql -h 127.0.0.1 -U "$LOCALPROD_POSTGRES_RUNTIME_USER" -d "$POSTGRES_DB" -c "CREATE TABLE public.localprod_privilege_validation (id integer PRIMARY KEY, note text);"'

# D: migration role creates the validation table; default privileges grant runtime DML access.
docker compose --env-file ops/local-prod/.env -f ops/local-prod/compose.yml exec postgres sh -c 'PGPASSWORD="$LOCALPROD_POSTGRES_MIGRATION_PASSWORD" psql -h 127.0.0.1 -U "$LOCALPROD_POSTGRES_MIGRATION_USER" -d "$POSTGRES_DB" -v ON_ERROR_STOP=1 -c "CREATE TABLE public.localprod_privilege_validation (id integer PRIMARY KEY, note text);"'

# E: expected to succeed (runtime SELECT/INSERT/UPDATE/DELETE).
docker compose --env-file ops/local-prod/.env -f ops/local-prod/compose.yml exec postgres sh -c 'PGPASSWORD="$LOCALPROD_POSTGRES_RUNTIME_PASSWORD" psql -h 127.0.0.1 -U "$LOCALPROD_POSTGRES_RUNTIME_USER" -d "$POSTGRES_DB" -v ON_ERROR_STOP=1 -c "INSERT INTO public.localprod_privilege_validation VALUES (1, '"'"'ok'"'"'); UPDATE public.localprod_privilege_validation SET note = '"'"'updated'"'"' WHERE id = 1; SELECT * FROM public.localprod_privilege_validation; DELETE FROM public.localprod_privilege_validation WHERE id = 1;"'

# F: each command is expected to fail (runtime cannot alter or drop migration-owned tables).
docker compose --env-file ops/local-prod/.env -f ops/local-prod/compose.yml exec postgres sh -c 'PGPASSWORD="$LOCALPROD_POSTGRES_RUNTIME_PASSWORD" psql -h 127.0.0.1 -U "$LOCALPROD_POSTGRES_RUNTIME_USER" -d "$POSTGRES_DB" -c "ALTER TABLE public.localprod_privilege_validation ADD COLUMN denied integer;"'
docker compose --env-file ops/local-prod/.env -f ops/local-prod/compose.yml exec postgres sh -c 'PGPASSWORD="$LOCALPROD_POSTGRES_RUNTIME_PASSWORD" psql -h 127.0.0.1 -U "$LOCALPROD_POSTGRES_RUNTIME_USER" -d "$POSTGRES_DB" -c "DROP TABLE public.localprod_privilege_validation;"'

# G: migration role owns and removes the validation table.
docker compose --env-file ops/local-prod/.env -f ops/local-prod/compose.yml exec postgres sh -c 'PGPASSWORD="$LOCALPROD_POSTGRES_MIGRATION_PASSWORD" psql -h 127.0.0.1 -U "$LOCALPROD_POSTGRES_MIGRATION_USER" -d "$POSTGRES_DB" -v ON_ERROR_STOP=1 -c "DROP TABLE public.localprod_privilege_validation;"'
```

## PostgreSQL performance-test harness

The optional performance profile has one purpose: trustworthy query-count tests
against PostgreSQL 18.6 without using the retained tilecompany_localprod
database for test data. It creates a dedicated local-only role named by
LOCALPROD_POSTGRES_TEST_USER. This role has LOGIN CREATEDB, but explicitly has
no superuser, createrole, replication, or bypass-RLS capability. It is never
passed to web, Nginx, or the normal migration service.

From the repository root:

~~~powershell
# A. Provision idempotently, including on an existing retained volume.
docker compose --profile performance --env-file ops/local-prod/.env -f ops/local-prod/compose.yml run --rm test-role-provision

# B. Print only role names and non-secret privilege booleans.
docker compose --profile performance --env-file ops/local-prod/.env -f ops/local-prod/compose.yml run --rm test-role-verify

# C. Build and run the isolated PostgreSQL smoke measurements.
docker compose --profile performance --env-file ops/local-prod/.env -f ops/local-prod/compose.yml build performance
docker compose --profile performance --env-file ops/local-prod/.env -f ops/local-prod/compose.yml run --rm performance

# D. Retain Django's disposable test database during an intentional repeated session.
docker compose --profile performance --env-file ops/local-prod/.env -f ops/local-prod/compose.yml run --rm performance python manage.py test performance_tests --keepdb --verbosity 2

# E. Remove only the disposable Django test database when it was retained.
docker compose --env-file ops/local-prod/.env -f ops/local-prod/compose.yml exec postgres sh -c 'dropdb -U "$LOCALPROD_POSTGRES_TEST_USER" --maintenance-db "$POSTGRES_DB" --if-exists "test_$POSTGRES_DB"'
~~~

Normal test completion creates, migrates, runs against, and drops
test_<database-name>; it does not alter the retained database's schema or rows.
The profile uses process-local cache, not Redis, so test execution cannot flush
or mutate retained Redis OTP/throttle state. The printed elapsed field is
labelled local Docker timing; query count and normalized duplicate SQL shapes
are the primary evidence. explain_analyze_buffers(queryset) is test-side,
manual support for later confirmed read-only queries only.

## Stop and remove

```powershell
docker compose --env-file ops/local-prod/.env -f ops/local-prod/compose.yml stop
docker compose --env-file ops/local-prod/.env -f ops/local-prod/compose.yml down
docker compose --env-file ops/local-prod/.env -f ops/local-prod/compose.yml down -v
```

`stop` stops containers but retains them, the network, and the named PostgreSQL volume. `down` removes the project containers and network but retains the named volume, so database data remains for a later `up`. **`down -v` also deletes the named PostgreSQL data volume and permanently removes this simulation database; do not use it casually, especially after Django migrations have run.**

## Docker security contract

- **Image:** immutable software template (the pinned PostgreSQL or Redis image).
- **Container:** running instance of an image.
- **Volume:** persistent container data; `postgres_data` preserves the simulated database through container recreation.
- **Network:** service communication boundary; the internal `backend` network isolates PostgreSQL and Redis from host and browser access.

Pinned images: `postgres:18.6-bookworm` and `redis:7.4.11-alpine`.
