"""
AWS Session Helper — creates a boto3 session with support for
AWS Academy temporary credentials (AWS_SESSION_TOKEN).

All service files import from this module to avoid credential
duplication and ensure session token is always included.
"""

import os
import logging
import boto3
from botocore.exceptions import NoCredentialsError, PartialCredentialsError

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
        return session.client(service_name), True
    except (NoCredentialsError, PartialCredentialsError):
        logger.warning(f"AWS credentials not configured — {service_name} unavailable.")
        return None, False


def get_resource(service_name: str):
    """Get a boto3 resource for a given AWS service."""
    try:
        session = get_boto3_session()
        return session.resource(service_name), True
    except (NoCredentialsError, PartialCredentialsError):
        logger.warning(f"AWS credentials not configured — {service_name} resource unavailable.")
        return None, False


def check_aws_available() -> bool:
    """Quick check if AWS credentials are configured (non-empty)."""
    return bool(
        os.getenv('AWS_ACCESS_KEY_ID') and
        os.getenv('AWS_SECRET_ACCESS_KEY')
    )
