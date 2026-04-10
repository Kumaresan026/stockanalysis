#!/bin/bash

# Find the exact python executable path dynamically since systemd ExecStart does not support wildcards (*)
PYTHON_EXEC=$(find /var/app/venv -type f -name python -path "*/bin/python" | head -n 1)

cat > /etc/systemd/system/stock-worker.service << EOF
[Unit]
Description=Stock SQS Worker
After=network.target

[Service]
Type=simple
User=webapp
Group=webapp
WorkingDirectory=/var/app/current
ExecStart=$PYTHON_EXEC manage.py poll_sqs
Restart=always

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable stock-worker.service
systemctl restart stock-worker.service
