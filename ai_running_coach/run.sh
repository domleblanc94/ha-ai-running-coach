#!/bin/sh
set -eu

# Supervisor supplies /data and owns its options.json.
if [ -L /data/app ]; then
    echo "Refusing a symbolic link for the app data directory" >&2
    exit 1
fi
install -d -m 0700 -o coach -g coach /data/app
umask 077
unset SUPERVISOR_TOKEN HASSIO_TOKEN
exec gosu coach:coach python -m app.server --data-dir /data/app
