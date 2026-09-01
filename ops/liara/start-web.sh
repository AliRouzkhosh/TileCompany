#!/bin/sh
set -eu

# Liara supplies PORT at runtime.  The fallback keeps this artifact testable
# outside Liara without changing the frozen local-production Compose command.
exec gunicorn \
    --bind "0.0.0.0:${PORT:-8000}" \
    --workers 2 \
    --error-logfile=- \
    --capture-output \
    --timeout 60 \
    TileCompany.wsgi:application
