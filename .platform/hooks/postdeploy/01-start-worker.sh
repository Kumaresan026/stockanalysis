#!/bin/bash

# Kill any existing stray worker processes
pkill -f 'manage.py poll_sqs' || true

# Run worker in background detached, so it doesn't block deployment
nohup bash -c 'source /var/app/venv/*/bin/activate && python /var/app/current/manage.py poll_sqs' > /tmp/sqs_worker.log 2>&1 &

# Always exit 0 to prevent deployment failure
exit 0
