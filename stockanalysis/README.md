# Cloud Stock Market Analysis Platform

A production-ready Django web application for real-time stock market analytics, powered by AWS cloud services with event-driven architecture, deployed on Elastic Beanstalk.

![Python](https://img.shields.io/badge/Python-3.9+-blue)
![Django](https://img.shields.io/badge/Django-4.2-green)
![AWS](https://img.shields.io/badge/AWS-boto3-orange)
![License](https://img.shields.io/badge/License-MIT-yellow)

---

## Architecture Overview

```
User → Django (EB web process)
         ├── Stock API → DynamoDB (store raw data)
         ├── SQS (send stock update event)
         │     └── poll_sqs worker (EB worker process)
         │           ├── stock_processor → DynamoDB + S3 + analytics
         │           └── alert_handler  → SNS (email notification)
         ├── CloudWatch (log all events)
         └── S3 (store reports & charts)
```

**Two processes run side-by-side on EB** (defined in `Procfile`):
- `web` — Gunicorn serving the Django app
- `worker` — `poll_sqs` management command, evaluating alert rules

---

## Tech Stack

| Layer       | Technology                                       |
|-------------|--------------------------------------------------|
| Backend     | Django 4.2, Python 3.9+                          |
| Frontend    | Bootstrap 5, Chart.js                            |
| Cloud       | AWS (DynamoDB, S3, SQS, Lambda, SNS, CloudWatch) |
| SDK         | boto3                                            |
| Analytics   | pandas, numpy, stock_event_engine                |
| Deployment  | Gunicorn + Nginx on Elastic Beanstalk (AL2)      |

---

## Project Structure

```
stock_platform/          # Django project settings
stocks/                  # Single Django app (all features)
├── services/            # AWS service layer (boto3)
│   ├── aws_session.py   # Credential validation (STS-based)
│   ├── dynamodb_service.py
│   ├── s3_service.py
│   ├── sqs_service.py
│   ├── sns_service.py
│   └── cloudwatch_service.py
├── management/commands/
│   ├── check_aws.py     # Diagnostic: python manage.py check_aws
│   ├── init_aws_resources.py
│   └── poll_sqs.py      # EB worker process (Procfile)
├── lambda_functions/    # stock_processor, alert_handler
├── templates/stocks/
└── static/
stock_event_engine/      # Custom analytics library
.ebextensions/           # EB configuration
.platform/hooks/         # EB lifecycle hooks
```

---

## Features

- **User Management** — Register, login, profiles, role-based access
- **Stock Search & Detail** — Real-time quotes, historical charts
- **Analytics Dashboard** — SMA, EMA, RSI, Bollinger Bands, trend detection
- **Watchlist** — Track your favourite stocks
- **Price Alerts** — Event-driven alerts via SQS → Lambda handler → SNS email
- **Cloud Storage** — Reports in S3, data in DynamoDB (persistent across deployments)
- **Monitoring** — All events logged to CloudWatch

---

## Quick Start (Local Development)

### 1. Clone & Install

```bash
git clone https://github.com/your-repo/stock-platform.git
cd stock-platform

python -m venv venv
source venv/bin/activate        # Linux/Mac
# venv\Scripts\activate         # Windows

pip install -r requirements.txt
pip install -e .                # Install stock_event_engine
```

### 2. Configure Environment

```bash
cp .env.example .env
# Edit .env — add your AWS credentials and API keys
```

**For AWS Academy users** — copy credentials from:  
`AWS Academy → Start Lab → AWS Details → AWS CLI`

```env
AWS_ACCESS_KEY_ID=ASIA...
AWS_SECRET_ACCESS_KEY=...
AWS_SESSION_TOKEN=...           # Required for Academy — tokens expire every ~4-6h
AWS_DEFAULT_REGION=us-east-1
```

### 3. Initialise AWS Resources & Start Server

```bash
python manage.py migrate
python manage.py init_aws_resources   # Creates DynamoDB tables, S3 bucket, SQS, SNS
python manage.py check_aws            # Verify all services reachable
python manage.py runserver
```

Visit `http://127.0.0.1:8000/`

To also run the alert worker locally:
```bash
# In a second terminal:
python manage.py poll_sqs
```

---

## AWS Resource Names

All resources are created **programmatically** by `init_aws_resources`:

| Service    | Resource Name                    |
|------------|----------------------------------|
| DynamoDB   | `alert_rules`                    |
| DynamoDB   | `stock_data`                     |
| DynamoDB   | `user_watchlists`                |
| DynamoDB   | `analytics_results`              |
| S3         | `stock-platform-reports-2024`    |
| SQS        | `stock-events-queue`             |
| SNS        | `stock-alerts-topic`             |
| CloudWatch | `stock-platform-logs`            |
| Lambda     | `stock_processor`                |
| Lambda     | `alert_handler`                  |

---

## Elastic Beanstalk Deployment

### Prerequisites

- AWS CLI and EB CLI installed
- EB application already created (`eb init`)

### Deploy

```bash
eb deploy
```

### ⚠️ AWS Academy Credential Refresh (Every 4-6 Hours)

AWS Academy provides **temporary** credentials that expire. After expiry, all AWS services silently fail.

**After getting new credentials from AWS Academy → AWS Details → AWS CLI:**

1. Go to **EB Console → Configuration → Software → Environment Properties**
2. Update these three values:
   ```
   AWS_ACCESS_KEY_ID     = <new value>
   AWS_SECRET_ACCESS_KEY = <new value>
   AWS_SESSION_TOKEN     = <new value>
   ```
3. Click **Apply** — EB restarts in ~60 seconds

**Verify credentials are valid after restart:**
```bash
eb ssh
python manage.py check_aws
# Must show: ✅ CREDENTIALS PASS
```

### Verify Deployment Health

```bash
# SSH into EB instance
eb ssh

# Check both processes are running
ps aux | grep gunicorn    # web process
ps aux | grep poll_sqs    # worker process

# Run full AWS diagnostic
cd /var/app/current
source /var/app/venv/*/bin/activate
python manage.py check_aws --verbose

# Check startup credential log
cat /var/log/startup-aws-check.log

# Check application logs
tail -f /var/log/web.stdout.log
```

### Where to Find Logs

| Log file | What it contains |
|----------|-----------------|
| `/var/log/web.stdout.log` | Django app logs, AWS errors, alert processing |
| `/var/log/startup-aws-check.log` | Credential check run after every deploy |
| `/var/log/eb-engine.log` | EB deployment engine events |
| `/var/log/cfn-init-cmd.log` | Output of `.ebextensions` container commands |

---

## Diagnostic Tool

```bash
# Run from EB instance or local machine
python manage.py check_aws

# Example output:
# ✅ CREDENTIALS   PASS   (identity: arn:aws:sts::669...)
# ✅ DYNAMODB      PASS   (4 tables, write test OK)
# ✅ S3            PASS   (upload test OK, no lifecycle rules)
# ✅ SQS           PASS   (queue depth: 0 pending)
# ⚠️  SNS           PASS   (1 subscription pending email confirmation)
# ✅ LAMBDA        PASS   (stock_processor Active, alert_handler Active)
```

Each failure prints the exact fix command or EB Console step to resolve it.

---

## Alert End-to-End Flow

```
1. User creates alert  → stored in DynamoDB (alert_rules table) + SQLite
2. Stock page visited  → price sent to SQS (STOCK_UPDATE event)
3. poll_sqs worker     → picks up message
4. stock_processor     → stores price in DynamoDB, runs analytics, uploads to S3
5. alert_handler       → scans alert_rules for this symbol
6. If condition met    → marks alert "triggered", publishes SNS notification
7. User receives email → "Stock Alert: AAPL PRICE_ABOVE 200"
```

### Test Alerts Manually

```bash
# Send a test stock price event to SQS
aws sqs send-message \
  --queue-url $(aws sqs get-queue-url --queue-name stock-events-queue --query QueueUrl --output text) \
  --message-body '{"event_type":"STOCK_UPDATE","symbol":"AAPL","price":210,"volume":50000000,"change_percent":2.5}' \
  --region us-east-1

# Verify alert was triggered in DynamoDB
aws dynamodb scan --table-name alert_rules --region us-east-1 \
  --query 'Items[?status.S==`triggered`]'
```

---

## Environment Variables Reference

| Variable | Description | Required |
|----------|-------------|----------|
| `DJANGO_SECRET_KEY` | Django secret key | ✅ |
| `DJANGO_DEBUG` | `False` in production | ✅ |
| `DJANGO_ALLOWED_HOSTS` | Comma-separated hostnames | ✅ |
| `AWS_ACCESS_KEY_ID` | AWS access key | ✅ |
| `AWS_SECRET_ACCESS_KEY` | AWS secret key | ✅ |
| `AWS_SESSION_TOKEN` | Session token (AWS Academy) | Academy only |
| `AWS_DEFAULT_REGION` | AWS region | ✅ (default: us-east-1) |
| `DYNAMODB_ALERTS_TABLE` | Alert rules table name | default: `alert_rules` |
| `S3_BUCKET_NAME` | Reports bucket | default: `stock-platform-reports-2024` |
| `SQS_QUEUE_NAME` | Event queue name | default: `stock-events-queue` |
| `SNS_TOPIC_NAME` | Alert notifications topic | default: `stock-alerts-topic` |
| `ALPHA_VANTAGE_API_KEY` | Stock data API key | Optional |
| `FINNHUB_API_KEY` | Stock data API key | Optional |

---

## Custom Library: stock_event_engine

```python
from stock_event_engine import StockAnalyzer, SignalDetector, AlertEngine

# Technical Analysis
analyzer = StockAnalyzer(prices=[150, 152, 148, 155, 160], symbol="AAPL")
sma = analyzer.moving_average(window=3)
rsi = analyzer.rsi()

# Signal Detection
detector = SignalDetector(prices, "AAPL")
trend = detector.detect_trend()

# Alert Evaluation
from stock_event_engine.alerts import AlertRule
engine = AlertEngine()
engine.add_rule(AlertRule("AAPL", "PRICE_ABOVE", 180.0, "user1"))
triggered = engine.evaluate({"AAPL": {"price": 185}})
```

---

## License

MIT License
