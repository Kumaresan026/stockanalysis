"""
AWS Session — IAM Instance Role / Default Credential Provider Chain.

Authentication is delegated entirely to infrastructure.

On Elastic Beanstalk:
  boto3 discovers credentials from the EC2 instance profile (LabRole)
  via the Instance Metadata Service (IMDS). No credentials needed in
  code or environment variables.

Locally:
  boto3 walks its standard credential chain:
  env vars → ~/.aws/credentials → instance profile

ARCHITECTURAL PRINCIPLE:
  "Authentication is delegated to infrastructure (IAM role),
   not embedded in application code."

NEVER pass aws_access_key_id / aws_secret_access_key / aws_session_token
to any boto3 call. Doing so couples the app to short-lived credentials
and causes HTTP 500s when they expire.
"""

import os
import logging

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

logger = logging.getLogger('stocks')

# ── Region ────────────────────────────────────────────────────────────────────
REGION = os.getenv('AWS_DEFAULT_REGION', 'us-east-1')

# ── Short timeouts for resilience ─────────────────────────────────────────────
# Used when callers need to fail fast (e.g. optional background operations).
_FAST_CONFIG = Config(
    connect_timeout=3,
    read_timeout=5,
    retries={'max_attempts': 1},
)


# ── Client / Resource factories ───────────────────────────────────────────────

def get_client(service: str, fast: bool = False):
    """
    Return a boto3 client for *service* using the default credential chain.

    Args:
        service: AWS service name (e.g. 'dynamodb', 'sqs', 'sns').
        fast:    If True, use short connect/read timeouts so the call
                 fails quickly when credentials are unavailable.

    Returns:
        A boto3 service client. Raises on misconfiguration — callers
        should catch exceptions and degrade gracefully.
    """
    config = _FAST_CONFIG if fast else None
    return boto3.client(service, region_name=REGION, config=config)


def get_resource(service: str):
    """
    Return a boto3 resource for *service* using the default credential chain.

    Args:
        service: AWS service name (e.g. 'dynamodb').

    Returns:
        A boto3 service resource.
    """
    return boto3.resource(service, region_name=REGION)


# ── Safe wrapper ──────────────────────────────────────────────────────────────

def safe_aws_call(func, default=None, context: str = ''):
    """
    Execute func(), returning *default* on any exception.

    Ensures AWS failures (network error, missing role, throttling, etc.)
    NEVER propagate to Django views as HTTP 500s.

    Args:
        func:    Zero-argument callable that performs the AWS operation.
        default: Value to return when func raises.
        context: Short label used in the warning log (e.g. 'sns.publish').
    """
    try:
        return func()
    except Exception as exc:
        label = context or getattr(func, '__name__', 'aws_call')
        logger.warning("[AWS] %s suppressed %s: %s", label, type(exc).__name__, exc)
        return default


# ── Structured error logging ──────────────────────────────────────────────────

def log_aws_error(e: Exception, context: str = '') -> None:
    """
    Log a boto3 ClientError with a human-readable, actionable message.

    Args:
        e:       The caught exception.
        context: Short description (e.g. 'dynamodb.put_item').
    """
    prefix = f'[AWS] {context} — ' if context else '[AWS] '

    if not isinstance(e, ClientError) or not hasattr(e, 'response'):
        logger.error('%s%s: %s', prefix, type(e).__name__, e)
        return

    code = e.response.get('Error', {}).get('Code', 'Unknown')
    msg  = e.response.get('Error', {}).get('Message', str(e))

    if code in ('ExpiredTokenException', 'ExpiredToken'):
        logger.error(
            '%sSESSION TOKEN EXPIRED. On EB this should not happen '
            'when LabRole is the instance profile.', prefix
        )
    elif code in ('AccessDeniedException', 'AuthorizationError', 'AccessDenied'):
        logger.error(
            '%sACCESS DENIED (%s): %s. '
            'Ensure the EB EC2 instance profile (LabRole) has the '
            'required IAM permission.', prefix, code, msg
        )
    elif code == 'ResourceNotFoundException':
        logger.error(
            '%sRESOURCE NOT FOUND: %s. '
            'Run: python manage.py init_aws_resources', prefix, msg
        )
    elif code in ('InvalidClientTokenId', 'AuthFailure', 'InvalidAccessKeyId'):
        logger.error(
            '%sINVALID CREDENTIALS (%s). '
            'On EB: LabRole should be the instance profile — '
            'remove any AWS_ACCESS_KEY_ID env var.', prefix, code
        )
    else:
        logger.error('%sClientError %s: %s', prefix, code, msg)
