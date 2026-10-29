#!/bin/sh
# start searxng with its own entrypoint in background
if [ -x /usr/local/searxng/entrypoint.sh ]; then E=/usr/local/searxng/entrypoint.sh; else E=$(ls /usr/local/searxng/dockerfiles/docker-entrypoint.sh 2>/dev/null); fi
echo "searxng entrypoint: $E"
PORT= $E &
PY=$(command -v python3 || echo /usr/local/searxng/.venv/bin/python)
exec $PY /app/api.py
