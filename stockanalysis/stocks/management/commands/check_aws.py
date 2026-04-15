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

        self.stdout.write('\n' + '=' * 65)
        self.stdout.write('  AWS CONNECTIVITY DIAGNOSTIC - Stock Analysis Platform')
        self.stdout.write('=' * 65)

        results = {}

        # -- 1. Credentials -----------------------------------------------
        results['credentials'] = self._check_credentials(verbose)

        if not results['credentials']:
            self._print_credential_fix_guide()
            self._print_summary(results)
            return

        # -- 2. DynamoDB --------------------------------------------------
        results['dynamodb'] = self._check_dynamodb(verbose)

        # -- 3. S3 --------------------------------------------------------
        results['s3'] = self._check_s3(verbose)

        # -- 4. SQS -------------------------------------------------------
        results['sqs'] = self._check_sqs(verbose)

        # -- 5. SNS -------------------------------------------------------
        results['sns'] = self._check_sns(verbose)

        # -- 6. Lambda ----------------------------------------------------
        results['lambda'] = self._check_lambda(verbose)

        # -- Summary ------------------------------------------------------
        self._print_summary(results)

    # -------------------------------------------------------------------------

    def _check_credentials(self, verbose):
        self.stdout.write('\n[1/6] AWS Identity (IAM Role)')
        region = os.getenv('AWS_DEFAULT_REGION', 'us-east-1')
        self.stdout.write(f'  AWS_DEFAULT_REGION: {region}')
        self.stdout.write(
            '  Auth method: EC2 Instance Profile (LabRole) — '
            'no credentials in environment variables'
        )

        try:
            import boto3
            sts      = boto3.client('sts', region_name=region)
            identity = sts.get_caller_identity()
            arn      = identity.get('Arn', 'unknown')
            self.stdout.write(self.style.SUCCESS('  [OK] IAM Role VALID'))
            if verbose:
                self.stdout.write(f'       Identity: {arn}')
                self.stdout.write(f'       Account : {identity.get("Account", "?")}')
            return True
        except Exception as e:
            code = ''
            if hasattr(e, 'response'):
                code = e.response.get('Error', {}).get('Code', '')
            if code in ('ExpiredTokenException', 'ExpiredToken'):
                self.stdout.write(self.style.ERROR(
                    '  [X] Token EXPIRED\n'
                    '      On EB: This should not happen with LabRole instance profile.\n'
                    '      Check: EB Console → Configuration → Security → EC2 instance profile'
                ))
            else:
                self.stdout.write(self.style.ERROR(f'  [X] IAM identity check failed: {e}'))
            return False

    def _check_dynamodb(self, verbose):
        self.stdout.write('\n[2/6] DynamoDB Tables')
        from stocks.services.dynamodb_service import DynamoDBService
        db = DynamoDBService()
        tables = [
            os.getenv('DYNAMODB_ALERTS_TABLE',    'alert_rules'),
            os.getenv('DYNAMODB_STOCKS_TABLE',     'stock_data'),
            os.getenv('DYNAMODB_WATCHLIST_TABLE',  'user_watchlists'),
            os.getenv('DYNAMODB_ANALYTICS_TABLE',  'analytics_results'),
        ]
        all_ok = True
        for table in tables:
            try:
                resp   = db.client.describe_table(TableName=table)
                status = resp['Table']['TableStatus']
                count  = resp['Table'].get('ItemCount', '?')
                self.stdout.write(self.style.SUCCESS(
                    f'  [OK] {table}: {status}, ~{count} item(s)'
                ))
                if verbose:
                    self.stdout.write(f'       ARN: {resp["Table"].get("TableArn", "")}')
            except Exception as e:
                code = e.response.get('Error', {}).get('Code', '') if hasattr(e, 'response') else ''
                if code == 'ResourceNotFoundException':
                    self.stdout.write(self.style.ERROR(
                        f'  [X] {table}: NOT FOUND -> run: python manage.py init_aws_resources'
                    ))
                else:
                    self.stdout.write(self.style.ERROR(f'  [X] {table}: {e}'))
                all_ok = False

        # Write/read test
        try:
            ok = db.store_alert_rule(
                'diagnostic-test-001', 'system', 'system',
                'TEST', 'PRICE_ABOVE', 0.01,
            )
            if ok:
                self.stdout.write(self.style.SUCCESS('  [OK] Write test (alert_rules): PASS'))
                db.delete_item(
                    os.getenv('DYNAMODB_ALERTS_TABLE', 'alert_rules'),
                    {'alert_id': 'diagnostic-test-001'},
                )
            else:
                self.stdout.write(self.style.ERROR('  [X] Write test FAILED - check IAM permissions'))
                all_ok = False
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'  [X] Write test error: {e}'))
            all_ok = False

        return all_ok

    def _check_s3(self, verbose):
        self.stdout.write('\n[3/6] S3')
        from stocks.services.s3_service import S3Service
        s3     = S3Service()
        bucket = s3.bucket_name
        self.stdout.write(f'  Bucket: {bucket}')
        try:
            s3.client.head_bucket(Bucket=bucket)
            self.stdout.write(self.style.SUCCESS('  [OK] Bucket exists and is accessible'))

            # Upload test
            ok = s3.upload_file(b'diagnostic-check', 'diagnostics/check.txt', 'text/plain')
            if ok:
                self.stdout.write(self.style.SUCCESS('  [OK] Upload test: PASS'))
                s3.delete_object('diagnostics/check.txt')
            else:
                self.stdout.write(self.style.ERROR('  [X] Upload test FAILED - check s3:PutObject IAM permission'))
                return False

            if verbose:
                objects = s3.list_objects(max_keys=5)
                self.stdout.write(f'  Recent objects ({len(objects)}): {[o["key"] for o in objects]}')

            return True
        except Exception as e:
            code = e.response.get('Error', {}).get('Code', '') if hasattr(e, 'response') else ''
            if code == '404':
                self.stdout.write(self.style.ERROR(
                    f'  [X] Bucket "{bucket}" NOT FOUND -> run: python manage.py init_aws_resources'
                ))
            else:
                self.stdout.write(self.style.ERROR(f'  [X] S3 error: {e}'))
            return False

    def _check_sqs(self, verbose):
        self.stdout.write('\n[4/6] SQS')
        from stocks.services.sqs_service import SQSService
        sqs = SQSService()
        url = sqs.get_queue_url()
        if not url:
            self.stdout.write(self.style.ERROR(
                f'  [X] Queue "{sqs.queue_name}" NOT FOUND -> run: python manage.py init_aws_resources'
            ))
            return False

        self.stdout.write(self.style.SUCCESS(f'  [OK] Queue exists: {sqs.queue_name}'))
        if verbose:
            self.stdout.write(f'       URL: {url}')

        attrs   = sqs.get_queue_attributes()
        pending = attrs.get('ApproximateNumberOfMessages', '?')
        flight  = attrs.get('ApproximateNumberOfMessagesNotVisible', '?')
        self.stdout.write(f'  Queue depth: {pending} pending, {flight} in-flight')

        # Send test message
        ok = sqs.send_message('DIAGNOSTIC', {'source': 'check_aws', 'test': True})
        if ok:
            self.stdout.write(self.style.SUCCESS('  [OK] Send test: PASS'))
        else:
            self.stdout.write(self.style.ERROR('  [X] Send test FAILED - check sqs:SendMessage IAM permission'))
            return False

        return True

    def _check_sns(self, verbose):
        self.stdout.write('\n[5/6] SNS')
        from stocks.services.sns_service import SNSService
        sns = SNSService()
        if not sns.available:
            self.stdout.write(self.style.ERROR('  [X] SNS service unavailable'))
            return False

        arn = sns.get_topic_arn()
        if not arn:
            self.stdout.write(self.style.ERROR(
                f'  [X] Topic "{sns.topic_name}" NOT FOUND -> run: python manage.py init_aws_resources'
            ))
            return False

        self.stdout.write(self.style.SUCCESS(f'  [OK] Topic exists: {sns.topic_name}'))
        if verbose:
            self.stdout.write(f'       ARN: {arn}')

        subs      = sns.list_subscriptions()
        pending   = [s for s in subs if 'PendingConfirmation' in s.get('status', '')]
        confirmed = [s for s in subs if 'PendingConfirmation' not in s.get('status', '')]

        if not subs:
            self.stdout.write(self.style.WARNING(
                '  [!] No email subscriptions - no alerts will be emailed.\n'
                '      Fix: Login/register user with a real email address.'
            ))
        else:
            if pending:
                self.stdout.write(self.style.WARNING(
                    f'  [!] {len(pending)} subscription(s) PENDING confirmation.\n'
                    '      Check inbox and click the AWS confirmation link!'
                ))
            if confirmed:
                for s in confirmed:
                    self.stdout.write(self.style.SUCCESS(
                        f'  [OK] {s.get("endpoint", "?")} CONFIRMED'
                    ))

        return True

    def _check_lambda(self, verbose):
        self.stdout.write('\n[6/6] Lambda Functions')
        from stocks.services.lambda_service import LambdaService
        lsvc = LambdaService()
        functions = [
            os.getenv('LAMBDA_STOCK_PROCESSOR', 'stock_processor'),
            os.getenv('LAMBDA_ALERT_HANDLER',   'alert_handler'),
        ]
        all_ok = True
        for fn in functions:
            try:
                resp    = lsvc.client.get_function(FunctionName=fn)
                config  = resp['Configuration']
                state   = config.get('State', 'Unknown')
                runtime = config.get('Runtime', '?')
                if state == 'Active':
                    self.stdout.write(self.style.SUCCESS(f'  [OK] {fn}: {state} ({runtime})'))
                else:
                    self.stdout.write(self.style.WARNING(f'  [!] {fn}: state={state}'))
                if verbose:
                    self.stdout.write(f'       ARN: {config.get("FunctionArn", "?")}')
                    self.stdout.write(f'       Last modified: {config.get("LastModified", "?")}')
            except Exception as e:
                code = e.response.get('Error', {}).get('Code', '') if hasattr(e, 'response') else ''
                if code == 'ResourceNotFoundException':
                    self.stdout.write(self.style.WARNING(
                        f'  [!] {fn}: NOT DEPLOYED -> run: python manage.py init_aws_resources'
                    ))
                else:
                    self.stdout.write(self.style.ERROR(f'  [X] {fn}: {e}'))
                all_ok = False

        return all_ok

    # -------------------------------------------------------------------------

    def _print_credential_fix_guide(self):
        self.stdout.write(self.style.ERROR(
            '\n  [STOP] IAM role credentials unavailable. All other checks skipped.\n'
            '\n  HOW TO FIX (Elastic Beanstalk):\n'
            '  1. EB Console → Configuration → Security\n'
            '  2. Ensure EC2 instance profile is set to: LabRole\n'
            '  3. Redeploy the application\n'
            '\n  HOW TO FIX (Local development):\n'
            '  1. Run: aws configure\n'
            '     or set AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY in .env\n'
        ))

    def _print_summary(self, results):
        self.stdout.write('\n' + '=' * 65)
        self.stdout.write('  SUMMARY')
        self.stdout.write('=' * 65)
        for service, ok in results.items():
            label  = service.upper().ljust(16)
            icon   = '[OK]' if ok else '[X] '
            status = 'PASS' if ok else 'FAIL'
            line   = f'  {icon} {label} {status}'
            self.stdout.write(self.style.SUCCESS(line) if ok else self.style.ERROR(line))

        self.stdout.write('=' * 65)
        if all(results.values()):
            self.stdout.write(self.style.SUCCESS(
                '\n  ALL CHECKS PASSED - platform is fully operational!\n'
            ))
        else:
            self.stdout.write(self.style.ERROR(
                '\n  SOME CHECKS FAILED - see messages above.\n'
                '  Run: python manage.py init_aws_resources   (to create missing resources)\n'
                '  Run: python manage.py deploy_lambda         (to deploy Lambda functions)\n'
            ))
