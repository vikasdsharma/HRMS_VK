#!/bin/sh
# Container start-up: upgrade the database once (before any worker starts), then serve.
set -e
python manage.py migrate
exec gunicorn "app:app" \
  --bind "0.0.0.0:${HRMS_PORT:-8000}" \
  --workers "${WEB_CONCURRENCY:-3}" \
  --timeout 60 \
  --access-logfile - \
  --forwarded-allow-ips="*"
