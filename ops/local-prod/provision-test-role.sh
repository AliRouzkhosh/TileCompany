#!/bin/sh
set -eu

: "${POSTGRES_DB:?POSTGRES_DB is required}"
: "${POSTGRES_USER:?POSTGRES_USER is required}"
: "${POSTGRES_PASSWORD:?POSTGRES_PASSWORD is required}"
: "${LOCALPROD_POSTGRES_TEST_USER:?LOCALPROD_POSTGRES_TEST_USER is required}"
: "${LOCALPROD_POSTGRES_TEST_PASSWORD:?LOCALPROD_POSTGRES_TEST_PASSWORD is required}"

# This local-only administrative bootstrap is safe for retained volumes. It
# never grants the test role schema access to the retained application DB.
export PGPASSWORD="$POSTGRES_PASSWORD"
psql --set=ON_ERROR_STOP=1 --host "${POSTGRES_HOST:-postgres}" --port "${POSTGRES_PORT:-5432}" \
  --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
  --set=database_name="$POSTGRES_DB" \
  --set=test_user="$LOCALPROD_POSTGRES_TEST_USER" \
  --set=test_password="$LOCALPROD_POSTGRES_TEST_PASSWORD" <<'SQL'
SELECT format(
  'CREATE ROLE %I LOGIN NOSUPERUSER CREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD %L',
  :'test_user',
  :'test_password'
)
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'test_user')
\gexec

ALTER ROLE :"test_user"
  LOGIN NOSUPERUSER CREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS
  PASSWORD :'test_password';
GRANT CONNECT ON DATABASE :"database_name" TO :"test_user";
SQL
