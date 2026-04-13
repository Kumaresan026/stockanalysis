#!/bin/bash
# .platform/hooks/poststart/01-verify-aws.sh
#
# Runs after every EB deployment to verify AWS connectivity.
# Output goes to /var/log/startup-aws-check.log — visible in EB log bundles.

LOG=/var/log/startup-aws-check.log
echo "===== AWS Startup Check: $(date -u) =====" >> "$LOG"

cd /var/app/current
source /var/app/venv/*/bin/activate

python3 - >> "$LOG" 2>&1 << 'EOF'
import os, sys
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'stock_platform.settings')

# Load .env if present (local dev); EB uses its own env vars
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

import django
django.setup()

from stocks.services.aws_session import check_aws_available, get_boto3_session

print("[AWS-CHECK] AWS_ACCESS_KEY_ID    :", "SET" if os.getenv("AWS_ACCESS_KEY_ID") else "MISSING")
print("[AWS-CHECK] AWS_SECRET_ACCESS_KEY:", "SET" if os.getenv("AWS_SECRET_ACCESS_KEY") else "MISSING")
print("[AWS-CHECK] AWS_SESSION_TOKEN    :", "SET" if os.getenv("AWS_SESSION_TOKEN") else "MISSING")
print("[AWS-CHECK] AWS_DEFAULT_REGION   :", os.getenv("AWS_DEFAULT_REGION", "NOT SET"))
print("[AWS-CHECK] DYNAMODB_ALERTS_TABLE:", os.getenv("DYNAMODB_ALERTS_TABLE", "NOT SET"))
print("[AWS-CHECK] S3_BUCKET_NAME       :", os.getenv("S3_BUCKET_NAME", "NOT SET"))
print("[AWS-CHECK] SQS_QUEUE_NAME       :", os.getenv("SQS_QUEUE_NAME", "NOT SET"))

available = check_aws_available()
print(f"[AWS-CHECK] Credentials valid    : {available}")

if available:
    try:
        session = get_boto3_session()
        identity = session.client('sts').get_caller_identity()
        print(f"[AWS-CHECK] Identity             : {identity.get('Arn', 'unknown')}")
    except Exception as e:
        print(f"[AWS-CHECK] Identity lookup failed: {e}")
else:
    print("[AWS-CHECK] *** Credentials INVALID or EXPIRED — AWS services will not work ***")
    print("[AWS-CHECK] Fix: EB Console → Configuration → Software → Environment Properties")
    print("[AWS-CHECK] Update AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_SESSION_TOKEN")
EOF

echo "===== End AWS Startup Check =====" >> "$LOG"
