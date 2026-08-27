#!/bin/sh
set -eu

: "${POSTGRES_DB:?POSTGRES_DB is required}"
: "${POSTGRES_USER:?POSTGRES_USER is required}"
: "${POSTGRES_PASSWORD:?POSTGRES_PASSWORD is required}"
: "${LOCALPROD_POSTGRES_MIGRATION_USER:?LOCALPROD_POSTGRES_MIGRATION_USER is required}"
: "${LOCALPROD_POSTGRES_RUNTIME_USER:?LOCALPROD_POSTGRES_RUNTIME_USER is required}"
: "${LOCALPROD_POSTGRES_TEST_USER:?LOCALPROD_POSTGRES_TEST_USER is required}"

export PGPASSWORD="$POSTGRES_PASSWORD"
psql --set=ON_ERROR_STOP=1 --host "${POSTGRES_HOST:-postgres}" --port "${POSTGRES_PORT:-5432}" \
  --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" --tuples-only --no-align \
  --set=migration_user="$LOCALPROD_POSTGRES_MIGRATION_USER" \
  --set=runtime_user="$LOCALPROD_POSTGRES_RUNTIME_USER" \
  --set=test_user="$LOCALPROD_POSTGRES_TEST_USER" <<'SQL'
SELECT rolname, rolsuper, rolcreatedb, rolcreaterole
FROM pg_roles
WHERE rolname IN (:'migration_user', :'runtime_user', :'test_user')
ORDER BY rolname;
SQL
