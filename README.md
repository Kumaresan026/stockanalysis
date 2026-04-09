# Cloud Stock Market Analysis Platform

A production-ready Django web application for real-time stock market analytics, powered by AWS cloud services with event-driven architecture.

![Python](https://img.shields.io/badge/Python-3.9+-blue)
![Django](https://img.shields.io/badge/Django-4.2-green)
![AWS](https://img.shields.io/badge/AWS-boto3-orange)
![License](https://img.shields.io/badge/License-MIT-yellow)

---

## Architecture Overview

```
User → Django → Stock API → DynamoDB (store)
                           → SQS (event queue)
                           → Lambda (process)
                              → stock_event_engine (analytics)
                              → DynamoDB (store results)
                              → SNS (notify if alert triggered)
                           → CloudWatch (log everything)
                           → S3 (store reports/charts)
```

## Tech Stack

| Layer       | Technology                            |
|-------------|---------------------------------------|
| Backend     | Django 4.2, Python 3.9+               |
| Frontend    | Bootstrap 5, Chart.js                 |
| Cloud       | AWS (DynamoDB, S3, SQS, Lambda, SNS, CloudWatch) |
| SDK         | boto3                                 |
| Analytics   | pandas, numpy, stock_event_engine     |
| Deployment  | Gunicorn + Nginx on EC2               |

## Project Structure

```
stock_platform/          # Django project settings
stocks/                  # Single Django app (all features)
├── services/            # AWS service layer (boto3)
├── api/                 # Stock data API integration
├── analytics/           # Technical indicators & portfolio
├── events/              # SQS producer & consumer
├── lambda_functions/    # AWS Lambda handlers
├── templates/stocks/    # Django templates
└── static/              # CSS, JS assets
stock_event_engine/      # Custom PyPI library
```

## Features

- **User Management** — Register, login, profiles, role-based access
- **Stock Search & Detail** — Real-time quotes, historical charts
- **Analytics Dashboard** — SMA, EMA, RSI, Bollinger Bands, trend detection
- **Watchlist** — Track your favorite stocks
- **Price Alerts** — Event-driven alerts via SQS → Lambda → SNS
- **Cloud Storage** — Reports in S3, data in DynamoDB
- **Monitoring** — All events logged to CloudWatch

## Quick Start

### 1. Clone & Install

```bash
git clone https://github.com/your-repo/stock-platform.git
cd stock-platform

python -m venv venv
source venv/bin/activate  # Linux/Mac
# venv\Scripts\activate   # Windows

pip install -r requirements.txt
pip install -e .  # Install stock_event_engine
```

### 2. Configure Environment

```bash
cp .env.example .env
# Edit .env with your AWS credentials and API keys
```

### 3. Run Migrations & Start Server

```bash
python manage.py makemigrations stocks
python manage.py migrate
python manage.py createsuperuser
python manage.py collectstatic --noinput
python manage.py runserver
```

Visit `http://127.0.0.1:8000/`

## AWS Setup

### Required AWS Resources

All resources are created **programmatically** via boto3:

1. **DynamoDB Tables**: `stock_data`, `user_watchlists`, `alert_rules`, `analytics_results`
2. **S3 Bucket**: `stock-platform-reports`
3. **SQS Queue**: `stock-events-queue`
4. **SNS Topic**: `stock-alerts-topic`
5. **CloudWatch Log Group**: `stock-platform-logs`
6. **Lambda Functions**: `stock_processor`, `alert_handler`

### Initialize AWS Resources

```python
# In Django shell: python manage.py shell
from stocks.services.dynamodb_service import DynamoDBService
from stocks.services.s3_service import S3Service
from stocks.services.sqs_service import SQSService
from stocks.services.sns_service import SNSService
from stocks.services.cloudwatch_service import CloudWatchService

DynamoDBService().create_stock_tables()
S3Service().create_bucket()
SQSService().create_queue()
SNSService().create_topic()
CloudWatchService().create_log_group()
```

## Event-Driven Workflow

```
1. User searches stock → Django fetches data from API
2. Data stored in DynamoDB via boto3
3. Stock update event sent to SQS
4. Lambda (triggered by SQS) processes event
5. stock_event_engine runs analytics
6. Results stored in DynamoDB
7. Alert rules evaluated
8. If triggered → SNS sends email notification
9. Everything logged in CloudWatch
```

## Custom Library: stock_event_engine

```python
from stock_event_engine import StockAnalyzer, SignalDetector, AlertEngine

# Technical Analysis
analyzer = StockAnalyzer(prices=[150, 152, 148, 155, 160], symbol="AAPL")
sma = analyzer.moving_average(window=3)
ema = analyzer.exponential_moving_average(span=3)
vol = analyzer.volatility()

# Signal Detection
detector = SignalDetector(prices, "AAPL")
trend = detector.detect_trend()

# Alert Evaluation
from stock_event_engine.alerts import AlertRule
engine = AlertEngine()
engine.add_rule(AlertRule("AAPL", "PRICE_ABOVE", 180.0, "user1"))
triggered = engine.evaluate({"AAPL": {"price": 185}})
```

## Deployment to AWS EC2

### 1. Launch EC2 Instance

- AMI: Amazon Linux 2 / Ubuntu 22.04
- Instance type: t2.micro (free tier) or t2.small
- Security group: Allow ports 22 (SSH), 80 (HTTP), 443 (HTTPS)

### 2. Install Dependencies

```bash
sudo yum update -y  # Amazon Linux
sudo apt update -y   # Ubuntu

sudo yum install python3 python3-pip nginx git -y
pip3 install gunicorn
```

### 3. Deploy Application

```bash
git clone https://github.com/your-repo/stock-platform.git /app
cd /app
pip3 install -r requirements.txt
pip3 install -e .
cp .env.example .env  # Configure with production values

python3 manage.py migrate
python3 manage.py collectstatic --noinput
```

### 4. Configure Gunicorn & Nginx

```bash
# Start Gunicorn
gunicorn stock_platform.wsgi:application --bind 0.0.0.0:8000 --workers 3 --daemon

# Configure Nginx as reverse proxy
sudo nano /etc/nginx/conf.d/stockplatform.conf
```

```nginx
server {
    listen 80;
    server_name your-ec2-public-ip;

    location /static/ {
        alias /app/staticfiles/;
    }

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```

```bash
sudo systemctl restart nginx
```

## CI/CD

GitHub Actions pipeline (`.github/workflows/ci.yml`):
- Install dependencies
- Run `pylint` linting
- Run Django system checks
- Collect static files
- Optional deploy to EC2 on push to `main`

## Environment Variables

| Variable | Description |
|----------|-------------|
| `DJANGO_SECRET_KEY` | Django secret key |
| `AWS_ACCESS_KEY_ID` | AWS access key |
| `AWS_SECRET_ACCESS_KEY` | AWS secret key |
| `AWS_DEFAULT_REGION` | AWS region (default: us-east-1) |
| `ALPHA_VANTAGE_API_KEY` | Alpha Vantage API key |
| `FINNHUB_API_KEY` | Finnhub API key |

## License

MIT License
