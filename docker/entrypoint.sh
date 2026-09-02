#!/usr/bin/env bash
# Two roles from one image: the API and the job worker.
#
# Only the web role migrates.  If both did, a simultaneous restart would race
# two alembic runs against the same database.
set -euo pipefail

case "${1:-web}" in
  web)
    echo "waiting for the database..."
    for _ in $(seq 1 30); do
      if python -c "
from sqlalchemy import text
from sms.db import engine
with engine().connect() as c:
    c.execute(text('SELECT 1'))
" 2>/dev/null; then
        break
      fi
      sleep 2
    done

    echo "applying migrations..."
    alembic upgrade head

    # One worker process: this box is CPU-bound before it is request-bound.
    exec uvicorn sms.main:app --host 0.0.0.0 --port 8000 --workers 1 --proxy-headers
    ;;

  worker)
    echo "starting job worker"
    exec python -m sms.cli worker
    ;;

  shell)
    exec /bin/bash
    ;;

  *)
    # Anything else is a CLI invocation: `docker compose run sms collection list`
    exec python -m sms.cli "$@"
    ;;
esac
