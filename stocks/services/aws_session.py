"""
AWS Session Helper — creates a boto3 session with support for
AWS Academy temporary credentials (AWS_SESSION_TOKEN).

All service files import from this module to avoid credential
duplication and ensure session token is always included.
"""

import os
import time
import logging
import boto3
from botocore.exceptions import NoCredentialsError, PartialCredentialsError, ClientError

logger = logging.getLogger('stocks')

# ── Credential availability cache ──────────────────────────────────────
# Caches the result of check_aws_available() for AWS_CACHE_TTL seconds.
# This prevents every service init (SNS, DynamoDB, S3, SQS, etc.) from
# making its own STS call on every request, which caused 10-15s delays.
# When credentials are rotated, the cache is auto-invalidated by
# comparing the current session token against the cached one.
AWS_CACHE_TTL = 300  # 5 minutes

_aws_cache = {
    'result':     None,   # True | False | None (unset)
    'checked_at': 0.0,    # epoch timestamp of last check
    'token_key':  None,   # first 20 chars of AWS_SESSION_TOKEN to detect rotation
}


def _invalidate_aws_cache() -> None:
    """Force the next check_aws_available() call to re-verify credentials."""
    _aws_cache['result'] = None
    _aws_cache['checked_at'] = 0.0
    _aws_cache['token_key'] = None



def get_boto3_session() -> boto3.Session:
    """
    Create a boto3 Session using environment credentials.

    Supports:
    - Standard credentials (access key + secret)
    - AWS Academy / STS temporary credentials (includes session token)

    Returns:
        boto3.Session configured from environment variables.
    """
    session_token = os.getenv('AWS_SESSION_TOKEN') or None  # None if empty string

    return boto3.Session(
        aws_access_key_id=os.getenv('AWS_ACCESS_KEY_ID'),
        aws_secret_access_key=os.getenv('AWS_SECRET_ACCESS_KEY'),
        aws_session_token=session_token,
        region_name=os.getenv('AWS_DEFAULT_REGION', 'us-east-1'),
    )


def get_client(service_name: str):
    """Get a boto3 client for a given AWS service."""
    try:
        session = get_boto3_session()
        # Validate credentials via STS before returning service client
        session.client('sts').get_caller_identity()
        return session.client(service_name), True
    except (NoCredentialsError, PartialCredentialsError) as e:
        logger.warning(f"AWS credentials not configured — {service_name} unavailable: {e}")
        return None, False
    except ClientError as e:
        code = e.response.get('Error', {}).get('Code', '')
        if code in ('ExpiredTokenException', 'ExpiredToken'):
            logger.error(
                "AWS_SESSION_TOKEN EXPIRED. Update credentials in "
                "EB Console → Configuration → Software → Environment Properties."
            )
        else:
            logger.warning(f"AWS credential validation failed for {service_name}: {e}")
        return None, False


def get_resource(service_name: str):
    """Get a boto3 resource for a given AWS service."""
    try:
        session = get_boto3_session()
        # Validate credentials via STS before returning resource
        session.client('sts').get_caller_identity()
        return session.resource(service_name), True
    except (NoCredentialsError, PartialCredentialsError) as e:
        logger.warning(f"AWS credentials not configured — {service_name} resource unavailable: {e}")
        return None, False
    except ClientError as e:
        code = e.response.get('Error', {}).get('Code', '')
        if code in ('ExpiredTokenException', 'ExpiredToken'):
            logger.error(
                "AWS_SESSION_TOKEN EXPIRED. Update credentials in "
                "EB Console → Configuration → Software → Environment Properties."
            )
        else:
            logger.warning(f"AWS credential validation failed for {service_name} resource: {e}")
        return None, False


def check_aws_available() -> bool:
    """
    Validate AWS credentials by making a real STS:GetCallerIdentity call.

    Result is cached for AWS_CACHE_TTL seconds (default 5 min) so that
    every service init in the same request doesn't pay a separate ~2s
    STS round-trip. Cache is auto-invalidated when AWS_SESSION_TOKEN
    changes (credential rotation detected).

    Returns:
        True if credentials are valid and not expired, False otherwise.
    """
    access_key = os.getenv('AWS_ACCESS_KEY_ID')
    secret_key = os.getenv('AWS_SECRET_ACCESS_KEY')

    if not (access_key and secret_key):
        logger.warning("AWS_ACCESS_KEY_ID or AWS_SECRET_ACCESS_KEY not set.")
        return False

    # Token-rotation detection: if the credential was swapped, invalidate cache
    current_token_key = (os.getenv('AWS_SESSION_TOKEN') or '')[:20]
    if current_token_key != _aws_cache['token_key']:
        _invalidate_aws_cache()
        logger.debug("AWS credential rotation detected — cache invalidated.")

    # Serve from cache if still fresh
    now = time.monotonic()
    if _aws_cache['result'] is not None and (now - _aws_cache['checked_at']) < AWS_CACHE_TTL:
        return _aws_cache['result']

    # Cache miss — actually verify with STS
    result = False
    try:
        session = get_boto3_session()
        identity = session.client('sts').get_caller_identity()
        logger.debug(f"AWS identity verified: {identity.get('Arn', 'unknown')}")
        result = True
    except ClientError as e:
        code = e.response.get('Error', {}).get('Code', '')
        if code in ('ExpiredTokenException', 'ExpiredToken'):
            logger.error(
                "AWS_SESSION_TOKEN EXPIRED. "
                "Go to AWS Academy → AWS Details → AWS CLI, copy fresh credentials, "
                "and update them in EB Console → Configuration → Software → Environment Properties."
            )
        elif code in ('InvalidClientTokenId', 'AuthFailure', 'InvalidAccessKeyId'):
            logger.error(f"AWS credentials are INVALID (code={code}). Check AWS_ACCESS_KEY_ID.")
        elif code == 'AccessDenied':
            # Credentials exist but STS GetCallerIdentity is restricted — treat as available
            logger.warning("AWS credentials valid but STS GetCallerIdentity denied — assuming available.")
            result = True
        else:
            logger.warning(f"AWS credential check failed ({code}): {e}")
    except (NoCredentialsError, PartialCredentialsError) as e:
        logger.warning(f"AWS credentials missing: {e}")

    # Store in cache
    _aws_cache['result']     = result
    _aws_cache['checked_at'] = now
    _aws_cache['token_key']  = current_token_key
    logger.debug(f"AWS credential cache updated: available={result}, ttl={AWS_CACHE_TTL}s")
    return result


def log_aws_error(e: Exception, context: str = "") -> None:
    """
    Log a boto3 ClientError with an actionable message.

    Detects the most common EB failure modes:
    - ExpiredTokenException  → AWS Academy token expired
    - AccessDeniedException  → IAM role missing required permission
    - ResourceNotFoundException → Table / queue / bucket doesn't exist yet

    Call this from every service-layer except block so engineers can diagnose
    failures without SSH-ing into the instance.

    Args:
        e: The caught exception (expected ClientError, but handles any Exception).
        context: Short description of what operation was attempted (e.g. 'put_item alert_rules').
    """
    prefix = f"[AWS ERROR] {context} — " if context else "[AWS ERROR] "

    if hasattr(e, 'response'):
        code = e.response.get('Error', {}).get('Code', 'Unknown')
        msg = e.response.get('Error', {}).get('Message', str(e))

        if code in ('ExpiredTokenException', 'ExpiredToken'):
            logger.error(
                f"{prefix}AWS_SESSION_TOKEN EXPIRED. "
                "Refresh credentials: AWS Academy → AWS Details → AWS CLI, "
                "then update in EB Console → Configuration → Software → Environment Properties."
            )
        elif code in ('AccessDeniedException', 'AuthorizationError'):
            logger.error(
                f"{prefix}ACCESS DENIED (code={code}). "
                f"Message: {msg}. "
                "Check the IAM role attached to the EB EC2 instance has the required permissions."
            )
        elif code == 'ResourceNotFoundException':
            logger.error(
                f"{prefix}RESOURCE NOT FOUND (code={code}): {msg}. "
                "Run: python manage.py init_aws_resources"
            )
        elif code in ('InvalidClientTokenId', 'AuthFailure', 'InvalidAccessKeyId'):
            logger.error(
                f"{prefix}INVALID CREDENTIALS (code={code}). "
                "Check AWS_ACCESS_KEY_ID is correct."
            )
        else:
            logger.error(f"{prefix}ClientError code={code}: {msg}")
    else:
        logger.error(f"{prefix}{e}")

