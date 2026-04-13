#!/bin/bash
# .platform/hooks/postdeploy/03-init-aws.sh
#
# Runs AFTER the application is fully deployed and environment variables
# (including AWS credentials) are available on the EB instance.
#
# This is the CORRECT place to call init_aws_resources -- the
# container_commands phase runs during the build BEFORE env vars exist,
# which is why resources were never being created.
#
# Execution order:
#   container_commands (build phase, no env vars)
#     -> Application starts
#     -> postdeploy hooks run (env vars available) <-- this file
#
# Output log: /var/log/eb-init-aws.log (included in "eb logs --all")

LOG=/var/log/eb-init-aws.log

echo "=====================================================" >> "$LOG"
echo "  AWS Resource Init: $(date -u)" >> "$LOG"
echo "=====================================================" >> "$LOG"

# Activate the virtualenv
source /var/app/venv/*/bin/activate

cd /var/app/current

# Check credentials before attempting resource creation
echo "[init-aws] Checking AWS credentials..." >> "$LOG"
python3 -c "
import os
key = os.getenv('AWS_ACCESS_KEY_ID', '')
token = os.getenv('AWS_SESSION_TOKEN', '')
print(f'  AWS_ACCESS_KEY_ID    : {\"SET (\" + key[:6] + \"...)\" if key else \"MISSING - will fail\"}')
print(f'  AWS_SESSION_TOKEN    : {\"SET\" if token else \"NOT SET\"}')
" >> "$LOG" 2>&1

# Run resource initialization
echo "[init-aws] Running: python manage.py init_aws_resources --skip-lambda" >> "$LOG"
python manage.py init_aws_resources --skip-lambda >> "$LOG" 2>&1
INFRA_EXIT=$?

if [ $INFRA_EXIT -eq 0 ]; then
    echo "[init-aws] Infrastructure resources created successfully." >> "$LOG"
else
    echo "[init-aws] WARNING: Infrastructure init had errors (exit code $INFRA_EXIT)." >> "$LOG"
    echo "[init-aws] This may be due to expired credentials. Fix in EB Console and redeploy." >> "$LOG"
fi

# Deploy Lambda functions separately (takes longer, run in background)
echo "[init-aws] Deploying Lambda functions in background..." >> "$LOG"
nohup python manage.py deploy_lambda >> "$LOG" 2>&1 &
LAMBDA_PID=$!
echo "[init-aws] Lambda deployment started in background (PID: $LAMBDA_PID)" >> "$LOG"

# Subscribe the default notification email to SNS
echo "[init-aws] Ensuring alert email subscription..." >> "$LOG"
python3 -c "
import os, django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'stock_platform.settings')
django.setup()
from stocks.services.sns_service import SNSService
sns = SNSService()
if sns.available:
    email = os.getenv('SNS_ALERT_EMAIL', 'kumaresan2126@gmail.com')
    if email:
        sns.subscribe(email)
        print(f'[SNS] Subscription requested for: {email}')
    else:
        print('[SNS] SNS_ALERT_EMAIL not set in env vars.')
else:
    print('[SNS] SNS service unavailable - credentials may be expired.')
" >> "$LOG" 2>&1

echo "[init-aws] Postdeploy hook complete: $(date -u)" >> "$LOG"
echo "=====================================================" >> "$LOG"

# Never block the deployment -- always exit 0
exit 0
