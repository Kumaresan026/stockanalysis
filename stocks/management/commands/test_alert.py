"""
Django management command: test_alert

Tests the complete alert pipeline end-to-end:
  1. Checks AWS credentials
  2. Checks SNS topic exists and lists subscriptions
  3. Sends a test SNS email directly
  4. Checks DynamoDB for active alert rules
  5. Runs evaluate_and_notify() with a test price that deliberately
     triggers PRICE_BELOW rules

Usage (run from EB SSH or local):
    python manage.py test_alert --symbol AMZN --price 175
    python manage.py test_alert --symbol AMZN --price 175 --email you@example.com
    python manage.py test_alert --email you@example.com  (just tests SNS email)
"""

import os
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "End-to-end test of the alert pipeline (credentials → DynamoDB → SNS email)."

    def add_arguments(self, parser):
        parser.add_argument('--symbol',  default='AMZN', help='Stock symbol to test alerts for')
        parser.add_argument('--price',   type=float, default=0, help='Override price (triggers PRICE_BELOW if below threshold)')
        parser.add_argument('--volume',  type=int,   default=50_000_000, help='Override volume')
        parser.add_argument('--change',  type=float, default=-1.0, help='Override change percent')
        parser.add_argument('--email',   default='', help='Send direct SNS test email to this address')

    def handle(self, *args, **options):
        symbol  = options['symbol'].upper()
        price   = options['price']
        volume  = options['volume']
        change  = options['change']
        email   = options['email']

        self.stdout.write(self.style.MIGRATE_HEADING(
            '\n' + '=' * 60 +
            '\n  ALERT PIPELINE TEST\n' +
            '=' * 60
        ))

        # ── Step 1: Credentials ───────────────────────────────────────
        self.stdout.write('\n[1] AWS Credentials')
        from stocks.services.aws_session import check_aws_available, get_boto3_session
        if not check_aws_available():
            self.stdout.write(self.style.ERROR(
                '  ❌ Credentials INVALID — refresh in EB Console then re-run.'
            ))
            return
        self.stdout.write(self.style.SUCCESS('  ✅ Credentials valid'))

        # ── Step 2: SNS Topic & Subscriptions ───────────────────────
        self.stdout.write('\n[2] SNS Email Subscription')
        from stocks.services.sns_service import SNSService
        sns = SNSService()
        if not sns.available:
            self.stdout.write(self.style.ERROR('  ❌ SNS service unavailable'))
        else:
            topic_arn = sns.get_topic_arn()
            if not topic_arn:
                self.stdout.write(self.style.ERROR(
                    f'  ❌ SNS topic not found. Run: python manage.py init_aws_resources'
                ))
            else:
                self.stdout.write(self.style.SUCCESS(f'  ✅ Topic: {topic_arn}'))
                subs = sns.list_subscriptions()
                if not subs:
                    self.stdout.write(self.style.ERROR(
                        '  ❌ NO SUBSCRIPTIONS — no email will ever be sent!\n'
                        '     Fix: Register/login with a valid email address.\n'
                        '     The app calls sns.subscribe(user.email) on registration and login.'
                    ))
                else:
                    for sub in subs:
                        endpoint = sub.get('endpoint', '?')
                        status = sub.get('status', '?')
                        if 'PendingConfirmation' in status:
                            self.stdout.write(self.style.WARNING(
                                f'  ⚠️  {endpoint}: PENDING CONFIRMATION\n'
                                f'     → Check your inbox for "AWS Notification - Subscription Confirmation"\n'
                                f'     → Click the confirmation link in that email\n'
                                f'     → Without confirmation, NO alerts will be emailed to you!'
                            ))
                        else:
                            self.stdout.write(self.style.SUCCESS(
                                f'  ✅ {endpoint}: CONFIRMED ✓'
                            ))

                # Optional: send a direct test email
                if email:
                    self.stdout.write(f'\n  Subscribing {email} to SNS topic...')
                    sns.subscribe(email)
                    self.stdout.write(self.style.WARNING(
                        f'  ⚠️  Check your inbox for AWS confirmation email for {email}\n'
                        f'     You MUST click the link before any alerts are delivered.'
                    ))

                self.stdout.write(f'\n  Sending direct SNS test email...')
                ok = sns.publish(
                    subject=f'TEST: Alert Pipeline Working — {symbol}',
                    message=(
                        f'This is a test notification from python manage.py test_alert.\n'
                        f'If you received this email, SNS is working correctly.\n\n'
                        f'Symbol tested: {symbol}\n'
                        f'Price used:    {price if price else "(not specified)"}\n'
                        f'Time (UTC):    {__import__("datetime").datetime.utcnow()}\n\n'
                        f'— Cloud Stock Market Analysis Platform'
                    )
                )
                if ok:
                    self.stdout.write(self.style.SUCCESS(
                        '  ✅ Direct SNS publish succeeded — check your inbox!'
                    ))
                else:
                    self.stdout.write(self.style.ERROR(
                        '  ❌ Direct SNS publish FAILED — see logs above for reason'
                    ))

        # ── Step 3: DynamoDB Active Alerts ───────────────────────────
        self.stdout.write(f'\n[3] Active Alerts in DynamoDB for {symbol}')
        from stocks.services.dynamodb_service import DynamoDBService
        db = DynamoDBService()
        table_name = os.getenv('DYNAMODB_ALERTS_TABLE', 'alert_rules')
        try:
            table = db.dynamodb.Table(table_name)
            resp = table.scan(
                FilterExpression='symbol = :sym AND #st = :st',
                ExpressionAttributeNames={'#st': 'status'},
                ExpressionAttributeValues={':sym': symbol, ':st': 'active'},
            )
            rules = resp.get('Items', [])
            if not rules:
                self.stdout.write(self.style.WARNING(
                    f'  ⚠️  No active alert rules found for {symbol}.\n'
                    f'     Create alerts in the web UI first.'
                ))
            else:
                self.stdout.write(self.style.SUCCESS(f'  ✅ Found {len(rules)} active rule(s):'))
                for r in rules:
                    self.stdout.write(
                        f'     • {r.get("condition")} ${float(r.get("threshold",0)):.2f} '
                        f'[user_id={r.get("user_id")} username={r.get("username","")}]'
                    )
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'  ❌ DynamoDB scan failed: {e}'))
            rules = []

        # ── Step 4: Evaluate Alerts ───────────────────────────────────
        if price and rules:
            self.stdout.write(f'\n[4] Running evaluate_and_notify({symbol}, price={price})')
            from stocks.services.alert_evaluator import evaluate_and_notify
            triggered = evaluate_and_notify(
                symbol=symbol,
                price=price,
                volume=volume,
                change_percent=change,
                dynamodb_service=db,
                sns_service=sns,
            )
            if triggered:
                self.stdout.write(self.style.SUCCESS(
                    f'  ✅ {len(triggered)} alert(s) TRIGGERED!\n'
                    + '\n'.join(f'     • {t["condition"]} @ {t["threshold"]}' for t in triggered)
                ))
            else:
                current = price
                self.stdout.write(self.style.WARNING(
                    f'  ℹ️  No alerts triggered for {symbol} @ ${current}.\n'
                    f'  Check: are the thresholds set correctly for this price?'
                ))
        elif not price:
            self.stdout.write(self.style.WARNING(
                '\n[4] Skipped evaluate_and_notify (no --price specified).'
            ))

        self.stdout.write('\n' + '=' * 60 + '\n')
