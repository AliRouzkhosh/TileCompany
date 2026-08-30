# TileCompany backup and isolated recovery runbook

This runbook is for deployment operators. It never supplies or records a database password. Use the platform secret manager or PostgreSQL's protected password-file mechanism; do not put `PGPASSWORD` or real credentials in scripts, repository files, or shell history.

## Authoritative coherent backup contract

`backup_tilecompany.ps1 -BackupRoot <absolute operator-owned path>` is the
authoritative local-production application backup. It preflights the active
stack, stops only `web`, creates a PostgreSQL custom dump and separate
`media_data` / `private_inventory_import_data` archives, validates each
artifact, writes a manifest and SHA-256 list, then restarts web in a `finally`
path. It writes an incomplete directory first and renames it only after all
validation succeeds. The set contains no Redis, staticfiles, `.env`, TLS keys,
or runtime/provider secrets.

The private-inventory archive records its 90-day lifecycle and must not be
retained past that lifecycle without an explicit policy decision. Redis is
recreated empty; staticfiles are regenerated with `collectstatic`; recovery
requires separately protected Django, database, Redis, and provider secrets.

Restore only with `recovery_drill.ps1 -BackupSet <absolute set path>`. It
verifies checksums, refuses a Git SHA mismatch, uses uniquely named isolated
Docker resources, never mounts active volumes, restores into a new PostgreSQL
database, safely extracts archives into new volumes, regenerates staticfiles,
and removes only resources labelled for that drill after success. A point-in-
time restore can resurrect protected files deleted after the backup; payment
state needs real-production reconciliation before reopening payment flows.

Real production still needs approved encryption, access control, off-host
storage, scheduling, alerting, RPO/RTO, retention, and secret-manager policy.

## PostgreSQL-only utility

Use `pg_dump -Fc` for a transactionally consistent logical database snapshot. Ordinary logical backups do not require stopping the web application, but PostgreSQL and uploaded media are not one atomic snapshot: protect database rows and their required private/public media with coordinated backup/retention operations.

`ops/backup_postgresql.ps1.example` takes `PGHOST`, `PGPORT`, `PGUSER`, `PGDATABASE`, and `TILECOMPANY_BACKUP_DIR` from the deployment environment and fails on `pg_dump` failure or zero-byte output. The backup directory must be outside public static/media roots, encrypted at rest, access-controlled, and copied to deployment-owned off-host storage.

Initial technical rotation is a starting point, pending business approval of RPO/RTO: retain several recent frequent backups, fewer daily backups, and fewer weekly backups; never automatically delete the only verified recoverable copy. Final backup cadence, retention, and off-site replication must follow approved recovery-point objective (acceptable data loss) and recovery-time objective (acceptable downtime).

Back up the PostgreSQL database and required private/public uploaded media. Source code is recovered through version control; `collectstatic` is reproducible and is not irreplaceable backup data; deployment secrets remain in the secret manager, not plaintext backup archives. Do not retain temporary import sources beyond their approved 90-day lifecycle.

## Owner restore drill — PostgreSQL 18 / PowerShell

The following drill creates a backup of `tilecompany_dev`, restores it into a distinct temporary database, verifies it, then drops **only** that temporary database. Do not run it against production or use `dropdb` on `tilecompany_dev` or `gamerate`.

```powershell
# Supply these through the owner shell/secret mechanism; do not type a password.
$env:PGHOST = '127.0.0.1'
$env:PGPORT = '5432'
$env:PGUSER = 'tilecompany_app'
$env:PGDATABASE = 'tilecompany_dev'
$drillRoot = Join-Path $env:TEMP 'tilecompany-p26b-restore-drill'
New-Item -ItemType Directory -Force -Path $drillRoot | Out-Null
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$backupFile = Join-Path $drillRoot "tilecompany_dev-$stamp.dump"
$restoreDb = "tilecompany_restore_$stamp"

pg_dump --format=custom --file=$backupFile --no-owner --no-privileges
if ($LASTEXITCODE -ne 0 -or (Get-Item -LiteralPath $backupFile).Length -le 0) { throw 'Backup failed.' }

createdb $restoreDb
if ($LASTEXITCODE -ne 0) { throw 'Temporary restore database creation failed.' }

pg_restore --exit-on-error --no-owner --no-privileges --dbname=$restoreDb $backupFile
if ($LASTEXITCODE -ne 0) { throw 'Restore failed; retain the dump for investigation.' }

psql --dbname=$restoreDb -v ON_ERROR_STOP=1 -c "SELECT to_regclass('public.django_migrations') AS migrations_table; SELECT count(*) AS migration_rows FROM django_migrations; SELECT count(*) AS user_rows FROM auth_user;"

# One-off Django validation against the restored database, using the existing
# credential mechanism. This does not migrate or otherwise mutate the restore.
$env:APP_ENV = 'development'
$env:DB_BACKEND = 'postgresql'
$env:POSTGRES_DB = $restoreDb
python manage.py check
python manage.py shell -c "from django.db import connection; print(connection.vendor); print(connection.introspection.table_names()[:3])"

# Only after the checks pass and no investigation is needed:
dropdb $restoreDb
Remove-Item -LiteralPath $drillRoot -Recurse -Force
```

If a restore fails, preserve the backup and temporary database only as long as needed for restricted investigation, then clean them with the same access controls as live data. Never restore a drill backup over a live database.

## Recovery discipline

* Accidental data corruption: restore to a separate database first, assess scope, then perform an approved recovery.
* Failed deployment/migration: use a tested forward-fix or restore boundary. Do not assume Django migrations are reversible.
* Host loss: rebuild application/proxy workers from the pinned release, restore PostgreSQL and required media from protected backups.
* Media/storage loss: recover from the coordinated media backup, not the database dump alone.
* Credential compromise: revoke/rotate the affected PostgreSQL, Redis, SMTP, payment, or SMS credential and review access; `DJANGO_SECRET_KEY` rotation can invalidate signed/session-related data and resets HMAC-derived short-lived throttling identities.
* Redis outage: production authentication/OTP abuse controls fail safely; restore Redis connectivity before reopening dependent actions.

Before destructive or high-risk migrations, require a verified current backup, a migration rehearsal, and an agreed rollback/forward-fix plan. For data-destructive schema changes, backup/restore may be the true rollback boundary.
