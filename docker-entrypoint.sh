#!/bin/sh
# One image, several roles:  web (default) | worker | migrate | any other command.
set -e
role="${1:-web}"
[ "$#" -gt 0 ] && shift
case "$role" in
  web)
    # Request logs come from the app as JSON; uvicorn's own access log would duplicate them.
    exec uvicorn planyourshot.web.app:app --host 0.0.0.0 --port "${PORT:-8000}" \
      --proxy-headers --forwarded-allow-ips "${FORWARDED_ALLOW_IPS:-*}" --no-access-log "$@" ;;
  worker)
    exec python -m planyourshot.web.worker "$@" ;;
  migrate)
    exec alembic upgrade head ;;
  *)
    exec "$role" "$@" ;;
esac
