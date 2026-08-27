#!/bin/sh
set -eu

: "${LOCALPROD_POSTGRES_TEST_USER:?LOCALPROD_POSTGRES_TEST_USER is required}"
: "${LOCALPROD_POSTGRES_TEST_PASSWORD:?LOCALPROD_POSTGRES_TEST_PASSWORD is required}"

: "${POSTGRES_DB:?POSTGRES_DB is required}"
: "${POSTGRES_USER:?POSTGRES_USER is required}"
: "${LOCALPROD_POSTGRES_MIGRATION_USER:?LOCALPROD_POSTGRES_MIGRATION_USER is required}"
: "${LOCALPROD_POSTGRES_MIGRATION_PASSWORD:?LOCALPROD_POSTGRES_MIGRATION_PASSWORD is required}"
: "${LOCALPROD_POSTGRES_RUNTIME_USER:?LOCALPROD_POSTGRES_RUNTIME_USER is required}"
: "${LOCALPROD_POSTGRES_RUNTIME_PASSWORD:?LOCALPROD_POSTGRES_RUNTIME_PASSWORD is required}"

# This script is run only by the official image during first initialization of
# an empty data volume. psql variables quote role names as identifiers and
# passwords as SQL literals, preventing shell/SQL interpolation of credentials.
psql --set=ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
  --set=database_name="$POSTGRES_DB" \
  --set=migration_user="$LOCALPROD_POSTGRES_MIGRATION_USER" \
  --set=migration_password="$LOCALPROD_POSTGRES_MIGRATION_PASSWORD" \
  --set=runtime_user="$LOCALPROD_POSTGRES_RUNTIME_USER" \
  --set=runtime_password="$LOCALPROD_POSTGRES_RUNTIME_PASSWORD" \
  --set=test_user="$LOCALPROD_POSTGRES_TEST_USER" \
  --set=test_password="$LOCALPROD_POSTGRES_TEST_PASSWORD" <<'SQL'
CREATE ROLE :"migration_user"
  LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS
  PASSWORD :'migration_password';

CREATE ROLE :"runtime_user"
  LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS
  PASSWORD :'runtime_password';

CREATE ROLE :"test_user"
  LOGIN NOSUPERUSER CREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS
  PASSWORD :'test_password';

ALTER DATABASE :"database_name" OWNER TO :"migration_user";

REVOKE ALL ON DATABASE :"database_name" FROM PUBLIC;
GRANT CONNECT ON DATABASE :"database_name" TO :"migration_user", :"runtime_user", :"test_user";

ALTER SCHEMA public OWNER TO :"migration_user";
REVOKE ALL ON SCHEMA public FROM PUBLIC;
GRANT USAGE, CREATE ON SCHEMA public TO :"migration_user";
GRANT USAGE ON SCHEMA public TO :"runtime_user";

ALTER DEFAULT PRIVILEGES FOR ROLE :"migration_user" IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO :"runtime_user";
ALTER DEFAULT PRIVILEGES FOR ROLE :"migration_user" IN SCHEMA public
  GRANT USAGE, SELECT ON SEQUENCES TO :"runtime_user";
SQL
