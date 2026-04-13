"""
Django management command: check_aws

Runs a full, non-destructive diagnostic of all AWS services used by the
Stock Analysis Platform.  Run this any time you suspect credential or
connectivity issues — especially after deploying to Elastic Beanstalk or
after refreshing AWS Academy credentials.

Usage:
    python manage.py check_aws
    python manage.py check_aws --verbose
"""

import os
import json
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Run a full AWS connectivity diagnostic for all platform services."

    def add_arguments(self, parser):
        parser.add_argument(
            '--verbose', action='store_true',
            help='Show detailed output including ARNs and resource metadata.'
        )

    def handle(self, *args, **options):
        verbose = options.get('verbose', False)

        self.stdout.write('\n' + self.style.MIGRATE_HEADING(
            '=' * 65 + '\n'
            '  AWS CONNECTIVITY DIAGNOSTIC — Stock Analysis Platform\n'
            '=' * 65
        ))

        results = {}

        # ── 1. Credentials ────────────────────────────────────────────
        results['credentials'] = self._check_credentials(verbose)

        # Only proceed with service checks if credentials are valid
        if not results['credentials']:
            self._print_credential_fix_guide()
            self._print_summary(results)
            return

        # ── 2. DynamoDB ───────────────────────────────────────────────
        results['dynamodb'] = self._check_dynamodb(verbose)

        # ── 3. S3 ─────────────────────────────────────────────────────
        results['s3'] = self._check_s3(verbose)

        # ── 4. SQS ────────────────────────────────────────────────────
        results['sqs'] = self._check_sqs(verbose)

        # ── 5. SNS ────────────────────────────────────────────────────
        results['sns'] = self._check_sns(verbose)

        # ── 6. Lambda ─────────────────────────────────────────────────
        results['lambda'] = self._check_lambda(verbose)

        # ── Summary ───────────────────────────────────────────────────
        self._print_summary(results)

    # ── Check Helpers ─────────────────────────────────────────────────────

    def _check_credentials(self, verbose):
        self.stdout.write('\n[1/6] AWS Credentials')
        key_id = os.getenv('AWS_ACCESS_KEY_ID', '')
        secret = os.getenv('AWS_SECRET_ACCESS_KEY', '')
        token = os.getenv('AWS_SESSION_TOKEN', '')
        region = os.getenv('AWS_DEFAULT_REGION', 'us-east-1')

        self.stdout.write(f'  AWS_ACCESS_KEY_ID    : {"SET (" + key_id[:6] + "...)" if key_id else "MISSING ❌"}')
        self.stdout.write(f'  AWS_SECRET_ACCESS_KEY: {"SET" if secret else "MISSING ❌"}')
        self.stdout.write(f'  AWS_SESSION_TOKEN    : {"SET" if token else "NOT SET (ok if using IAM role)"}')
        self.stdout.write(f'  AWS_DEFAULT_REGION   : {region}')

        try:
            from stocks.services.aws_session import get_boto3_session
            session = get_boto3_session()
            identity = session.client('sts').get_caller_identity()
            arn = identity.get('Arn', 'unknown')
            self.stdout.write(self.style.SUCCESS(f'  ✅ Credentials VALID'))
            if verbose:
                self.stdout.write(f'     Identity: {arn}')
                self.stdout.write(f'     Account : {identity.get("Account", "?")}')
            return True
        except Exception as e:
            code = getattr(getattr(e, 'response', {}), 'get', lambda k, d=None: d)(
                'Error', {}
            ).get('Code', '') if hasattr(e, 'response') else ''
            if code in ('ExpiredTokenException', 'ExpiredToken'):
                self.stdout.write(self.style.ERROR(
                    '  ❌ Credentials EXPIRED — AWS Academy tokens last ~4-6 hours.\n'
                    '     Fix: AWS Academy → AWS Details → AWS CLI → copy credentials\n'
                    '          EB Console → Configuration → Software → update all 3 vars'
                ))
            else:
                self.stdout.write(self.style.ERROR(f'  ❌ Credential check failed: {e}'))
            return False

    def _check_dynamodb(self, verbose):
        self.stdout.write('\n[2/6] DynamoDB Tables')
        from stocks.services.dynamodb_service import DynamoDBService
        db = DynamoDBService()
        if not db.available:
            self.stdout.write(self.style.ERROR('  ❌ DynamoDB service unavailable'))
            return False

        tables = [
            os.getenv('DYNAMODB_ALERTS_TABLE', 'alert_rules'),
            os.getenv('DYNAMODB_STOCKS_TABLE', 'stock_data'),
            os.getenv('DYNAMODB_WATCHLIST_TABLE', 'user_watchlists'),
            os.getenv('DYNAMODB_ANALYTICS_TABLE', 'analytics_results'),
        ]
        all_ok = True
        for table in tables:
            try:
                resp = db.client.describe_table(TableName=table)
                status = resp['Table']['TableStatus']
                item_count = resp['Table'].get('ItemCount', '?')
                ttl_resp = db.client.describe_time_to_live(TableName=table)
                ttl_status = ttl_resp.get('TimeToLiveDescription', {}).get('TimeToLiveStatus', 'DISABLED')
                ttl_warn = ' ⚠️  TTL ENABLED — items may auto-delete!' if ttl_status == 'ENABLED' else ''
                self.stdout.write(self.style.SUCCESS(
                    f'  ✅ {table}: {status}, ~{item_count} items{ttl_warn}'
                ))
                if verbose:
                    arn = resp['Table'].get('TableArn', '')
                    self.stdout.write(f'     ARN: {arn}')
            except Exception as e:
                code = getattr(getattr(e, 'response', {}), 'get', lambda k, d=None: d)(
                    'Error', {}
                ).get('Code', '') if hasattr(e, 'response') else ''
                if code == 'ResourceNotFoundException':
                    self.stdout.write(self.style.ERROR(
                        f'  ❌ {table}: TABLE NOT FOUND → run: python manage.py init_aws_resources'
                    ))
                else:
                    self.stdout.write(self.style.ERROR(f'  ❌ {table}: {e}'))
                all_ok = False

        # Write/read test on alert_rules
        try:
            ok = db.store_alert_rule('diagnostic-test-001', 'system', 'TEST', 'PRICE_ABOVE', 0.01)
            if ok:
                self.stdout.write(self.style.SUCCESS('  ✅ Write test (alert_rules): OK'))
                db.delete_item(os.getenv('DYNAMODB_ALERTS_TABLE', 'alert_rules'), {'alert_id': 'diagnostic-test-001'})
            else:
                self.stdout.write(self.style.ERROR('  ❌ Write test (alert_rules): FAILED — check IAM permissions'))
                all_ok = False
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'  ❌ Write test error: {e}'))
            all_ok = False

        return all_ok

    def _check_s3(self, verbose):
        self.stdout.write('\n[3/6] S3')
        from stocks.services.s3_service import S3Service
        s3 = S3Service()
        bucket = s3.bucket_name
        self.stdout.write(f'  Bucket: {bucket}')
        if not s3.available:
            self.stdout.write(self.style.ERROR('  ❌ S3 service unavailable'))
            return False

        try:
            s3.client.head_bucket(Bucket=bucket)
            self.stdout.write(self.style.SUCCESS(f'  ✅ Bucket exists and accessible'))

            # Check lifecycle rules
            try:
                s3.client.get_bucket_lifecycle_configuration(Bucket=bucket)
                self.stdout.write(self.style.WARNING(
                    '  ⚠️  Lifecycle rules detected — objects may be auto-deleted!\n'
                    '     Check: AWS Console → S3 → bucket → Management → Lifecycle rules'
                ))
            except Exception as lc_e:
                lc_code = getattr(getattr(lc_e, 'response', {}), 'get', lambda k, d=None: d)(
                    'Error', {}
                ).get('Code', '') if hasattr(lc_e, 'response') else ''
                if lc_code == 'NoSuchLifecycleConfiguration':
                    self.stdout.write(self.style.SUCCESS('  ✅ No lifecycle rules (data safe)'))

            # Upload test
            ok = s3.upload_file(b'diagnostic-check', 'diagnostics/check.txt', 'text/plain')
            if ok:
                self.stdout.write(self.style.SUCCESS('  ✅ Upload test: OK'))
                s3.delete_object('diagnostics/check.txt')
            else:
                self.stdout.write(self.style.ERROR('  ❌ Upload test FAILED — check s3:PutObject IAM permission'))
                return False

            # List recent objects
            if verbose:
                objects = s3.list_objects(max_keys=5)
                self.stdout.write(f'  Recent objects ({len(objects)}): {[o["key"] for o in objects]}')

            return True
        except Exception as e:
            code = getattr(getattr(e, 'response', {}), 'get', lambda k, d=None: d)(
                'Error', {}
            ).get('Code', '') if hasattr(e, 'response') else ''
            if code == '404':
                self.stdout.write(self.style.ERROR(
                    f'  ❌ Bucket "{bucket}" NOT FOUND → run: python manage.py init_aws_resources'
                ))
            else:
                self.stdout.write(self.style.ERROR(f'  ❌ S3 error: {e}'))
            return False

    def _check_sqs(self, verbose):
        self.stdout.write('\n[4/6] SQS')
        from stocks.services.sqs_service import SQSService
        sqs = SQSService()
        if not sqs.available:
            self.stdout.write(self.style.ERROR('  ❌ SQS service unavailable'))
            return False

        url = sqs.get_queue_url()
        if not url:
            self.stdout.write(self.style.ERROR(
                f'  ❌ Queue "{sqs.queue_name}" NOT FOUND → run: python manage.py init_aws_resources'
            ))
            return False

        self.stdout.write(self.style.SUCCESS(f'  ✅ Queue exists: {sqs.queue_name}'))
        if verbose:
            self.stdout.write(f'     URL: {url}')

        attrs = sqs.get_queue_attributes()
        if attrs:
            pending = attrs.get('ApproximateNumberOfMessages', '?')
            in_flight = attrs.get('ApproximateNumberOfMessagesNotVisible', '?')
            delayed = attrs.get('ApproximateNumberOfMessagesDelayed', '?')
            self.stdout.write(f'  Queue depth: {pending} pending, {in_flight} in-flight, {delayed} delayed')
            if int(pending) > 100:
                self.stdout.write(self.style.WARNING(
                    '  ⚠️  High message count — worker may not be running (check: ps aux | grep poll_sqs)'
                ))

        # Send a test message
        ok = sqs.send_message('DIAGNOSTIC', {'source': 'check_aws', 'test': True})
        if ok:
            self.stdout.write(self.style.SUCCESS('  ✅ Send test: OK'))
        else:
            self.stdout.write(self.style.ERROR('  ❌ Send test FAILED — check sqs:SendMessage IAM permission'))
            return False

        return True

    def _check_sns(self, verbose):
        self.stdout.write('\n[5/6] SNS')
        from stocks.services.sns_service import SNSService
        sns = SNSService()
        if not sns.available:
            self.stdout.write(self.style.ERROR('  ❌ SNS service unavailable'))
            return False

        arn = sns.get_topic_arn()
        if not arn:
            self.stdout.write(self.style.ERROR(
                f'  ❌ Topic "{sns.topic_name}" NOT FOUND → run: python manage.py init_aws_resources'
            ))
            return False

        self.stdout.write(self.style.SUCCESS(f'  ✅ Topic exists: {sns.topic_name}'))
        if verbose:
            self.stdout.write(f'     ARN: {arn}')

        # Check subscriptions
        subs = sns.list_subscriptions()
        if not subs:
            self.stdout.write(self.style.WARNING(
                '  ⚠️  No subscriptions — no one will receive alert emails!\n'
                '     Fix: Register a user with an email address in the app.'
            ))
        else:
            pending = [s for s in subs if 'PendingConfirmation' in s.get('status', '')]
            confirmed = [s for s in subs if 'PendingConfirmation' not in s.get('status', '')]
            if pending:
                self.stdout.write(self.style.WARNING(
                    f'  ⚠️  {len(pending)} subscription(s) pending email confirmation!\n'
                    '     Check your inbox and click the confirmation link.'
                ))
            if confirmed:
                self.stdout.write(self.style.SUCCESS(f'  ✅ {len(confirmed)} confirmed subscription(s)'))

        return True

    def _check_lambda(self, verbose):
        self.stdout.write('\n[6/6] Lambda Functions')
        from stocks.services.lambda_service import LambdaService
        lsvc = LambdaService()
        if not lsvc.available:
            self.stdout.write(self.style.ERROR('  ❌ Lambda service unavailable'))
            return False

        functions = [
            os.getenv('LAMBDA_STOCK_PROCESSOR', 'stock_processor'),
            os.getenv('LAMBDA_ALERT_HANDLER', 'alert_handler'),
        ]
        all_ok = True
        for fn in functions:
            try:
                resp = lsvc.client.get_function(FunctionName=fn)
                config = resp['Configuration']
                state = config.get('State', 'Unknown')
                runtime = config.get('Runtime', '?')
                last_mod = config.get('LastModified', '?')
                if state == 'Active':
                    self.stdout.write(self.style.SUCCESS(
                        f'  ✅ {fn}: {state} ({runtime})'
                    ))
                else:
                    self.stdout.write(self.style.WARNING(f'  ⚠️  {fn}: state={state}'))
                if verbose:
                    self.stdout.write(f'     ARN: {config.get("FunctionArn", "?")}')
                    self.stdout.write(f'     Last modified: {last_mod}')
            except Exception as e:
                code = getattr(getattr(e, 'response', {}), 'get', lambda k, d=None: d)(
                    'Error', {}
                ).get('Code', '') if hasattr(e, 'response') else ''
                if code == 'ResourceNotFoundException':
                    self.stdout.write(self.style.WARNING(
                        f'  ⚠️  {fn}: NOT DEPLOYED → run: python manage.py init_aws_resources'
                    ))
                else:
                    self.stdout.write(self.style.ERROR(f'  ❌ {fn}: {e}'))
                all_ok = False

        return all_ok

    # ── Reporting ─────────────────────────────────────────────────────────────

    def _print_credential_fix_guide(self):
        self.stdout.write(self.style.ERROR(
            '\n  ⛔ Stopping — no valid credentials. Other checks skipped.\n'
            '\n  HOW TO FIX:\n'
            '  1. Go to AWS Academy → Start Lab → AWS Details → AWS CLI\n'
            '  2. Copy the three export lines\n'
            '  3. In EB Console → Configuration → Software → Environment Properties:\n'
            '       AWS_ACCESS_KEY_ID     = <paste>\n'
            '       AWS_SECRET_ACCESS_KEY = <paste>\n'
            '       AWS_SESSION_TOKEN     = <paste>\n'
            '  4. Click Apply — EB will restart (~60 seconds)\n'
            '  5. Run this command again: python manage.py check_aws\n'
        ))

    def _print_summary(self, results):
        self.stdout.write('\n' + '=' * 65)
        self.stdout.write(' SUMMARY')
        self.stdout.write('=' * 65)
        icons = {True: '✅', False: '❌', None: '⏭️ '}
        for service, ok in results.items():
            label = service.upper().ljust(16)
            icon = icons.get(ok, '?')
            status = 'PASS' if ok else 'FAIL'
            line = f'  {icon} {label} {status}'
            writer = self.style.SUCCESS if ok else self.style.ERROR
            self.stdout.write(writer(line))

        all_passed = all(results.values())
        self.stdout.write('=' * 65)
        if all_passed:
            self.stdout.write(self.style.SUCCESS(
                '\n  🎉 All checks passed — platform is fully operational!\n'
            ))
        else:
            self.stdout.write(self.style.ERROR(
                '\n  ❌ Some checks failed — see messages above for fix instructions.\n'
            ))
