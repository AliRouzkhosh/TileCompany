# Local load-test harness

This harness targets only `https://tilecompany.localhost` through the existing
Nginx, two-worker synchronous Gunicorn, PostgreSQL, and Redis Docker topology.
It refuses other hosts and requires the explicit `--local-load-ack` flag.

## Dependencies and fixture setup

Install `requirements-load.txt` into the development virtual environment. Build
and start the existing local-production stack, then create deterministic data:

```powershell
docker compose --env-file ops/local-prod/.env -f ops/local-prod/compose.yml build web
docker compose --env-file ops/local-prod/.env -f ops/local-prod/compose.yml up -d
docker compose --env-file ops/local-prod/.env -f ops/local-prod/compose.yml run --rm web python -m performance_tests.load.seed --apply
```

The seed operation is local-production/PostgreSQL guarded. It creates 64
`loadtest-shopper-*` users, 32 `loadtest-staff-*` users, local `.invalid` email
addresses, addresses explicitly labelled as fixtures, 30 queue orders, and 20
support tickets. It also invokes the approved idempotent demo catalog, launch
blog, Help Center, and dashboard-role seeds. SMS remains disabled and the
workload never submits the final checkout review/payment action.

Remove mutable load identities, addresses, orders, and tickets with:

```powershell
docker compose --env-file ops/local-prod/.env -f ops/local-prod/compose.yml run --rm web python -m performance_tests.load.seed --cleanup-identities
```

The approved shared demo catalog/editorial content remains for repeatable local
performance work.

## TLS

Prefer the mkcert root CA for this process only:

```powershell
$env:LOADTEST_CA_BUNDLE = "$(mkcert -CAROOT)\rootCA.pem"
```

If the local root cannot be exported, `LOADTEST_INSECURE_LOCAL_TLS=true` disables
verification only in Locust requests. The harness still hard-refuses every host
except the HTTPS `tilecompany.localhost` endpoint. Application TLS behavior is
never changed.

## Workload

The mixed profile uses class weights 17:2:1:

- 85% public browsing
- 10% authenticated shopping
- 5% operational staff

Within each class, route weights favor homepage/catalog/product reads. `on_sale`
has a low public weight. Writes are limited to session-backed cart add/update
operations. Checkout performs real GET/POST shipping and payment-form handling,
including CSRF, but stops at the review GET and creates no order.

Default progressive levels are 10, 25, 50, 100, and 200 users. Each level warms
for 30 seconds and measures for 120 seconds. Users persist while concurrency
rises, keeping total mixed-profile login attempts at the real local IP throttle
limit of 30 at 200 users. A sustained failure rate above 5% stops escalation.

```powershell
locust -f performance_tests/load/locustfile.py --headless --host https://tilecompany.localhost --local-load-ack --csv performance_tests/load/results/mixed --html performance_tests/load/results/mixed.html
```

Run `sample_system.ps1` alongside Locust to capture Docker/host utilization and
PostgreSQL connection counts as JSON Lines. Inspect `docker compose logs web
nginx postgres` after the run for worker timeouts, database errors, and 5xxs.

Environment variables can shorten diagnostics without changing the committed
baseline: `LOADTEST_LEVELS`, `LOADTEST_WARM_SECONDS`,
`LOADTEST_MEASURE_SECONDS`, `LOADTEST_PROFILE` (`mixed` or `public`), and
`LOADTEST_STAGE_RESULTS`.

These results describe only the developer machine, Docker Desktop, and current
local stack. They cannot establish Liara or production capacity.

## Worker-count experiment

`compose.workers.yml` is an explicitly local override that changes only the
Gunicorn sync-worker count. The frozen two-worker command in
`ops/local-prod/compose.yml` remains untouched. Example:

```powershell
$env:LOADTEST_GUNICORN_WORKERS = "3"
docker compose --env-file ops/local-prod/.env -f ops/local-prod/compose.yml -f performance_tests/load/compose.workers.yml up -d --force-recreate web nginx
```

Omitting the override restores the frozen two-worker baseline.
