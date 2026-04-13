"""
AWS Session Helper — creates a boto3 session with support for
AWS Academy temporary credentials (AWS_SESSION_TOKEN).

All service files import from this module to avoid credential
duplication and ensure session token is always included.
"""

import os
import logging
import boto3
from botocore.exceptions import NoCredentialsError, PartialCredentialsError, ClientError

logger = logging.getLogger('stocks')


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

    Unlike a simple env-var check, this catches expired AWS Academy tokens
    (AWS_SESSION_TOKEN) which appear non-empty but are actually invalid.

    Returns:
        True if credentials are valid and not expired, False otherwise.
    """
    if not (os.getenv('AWS_ACCESS_KEY_ID') and os.getenv('AWS_SECRET_ACCESS_KEY')):
        logger.warning("AWS_ACCESS_KEY_ID or AWS_SECRET_ACCESS_KEY not set.")
        return False

    try:
        session = get_boto3_session()
        identity = session.client('sts').get_caller_identity()
        logger.debug(f"AWS identity verified: {identity.get('Arn', 'unknown')}")
        return True
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
            return True
        else:
            logger.warning(f"AWS credential check failed ({code}): {e}")
        return False
    except (NoCredentialsError, PartialCredentialsError) as e:
        logger.warning(f"AWS credentials missing: {e}")
        return False


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

