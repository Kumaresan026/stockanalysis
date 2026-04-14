"""
AWS Session Helper — credential-chain-aware boto3 session factory.

Supports two credential modes automatically:

  1. EXPLICIT (env vars set) — local dev / AWS Academy:
     Reads AWS_ACCESS_KEY_ID + AWS_SECRET_ACCESS_KEY (+ optional
     AWS_SESSION_TOKEN) from environment and passes them explicitly to
     boto3. Required for AWS Academy whose temporary credentials expire
     every 4-6 hours.

  2. IAM ROLE fallback (env vars absent / placeholder) — Elastic Beanstalk:
     When the env vars are not set (or are set to the default placeholder
     "REPLACE_IN_EB_CONSOLE"), boto3.Session() is constructed without
     explicit credentials. boto3 then walks the standard credential chain:
       env vars → ~/.aws/credentials → EC2 instance profile (LabRole)
     This is the correct approach when the EB instance has LabRole attached.

All service files import from this module only — no direct boto3 calls
outside this file so credential logic stays in one place.
"""

import os
import time
import logging
import boto3
from botocore.exceptions import NoCredentialsError, PartialCredentialsError, ClientError

logger = logging.getLogger('stocks')

# ── Placeholder sentinel ───────────────────────────────────────────────────
# When .ebextensions/03_environment.config has not been overridden in the
# EB Console, these values indicate "use IAM role instead".
_PLACEHOLDERS = {'', 'REPLACE_IN_EB_CONSOLE', 'replace_in_eb_console'}


def _using_explicit_credentials() -> bool:
    """Return True if real explicit credentials are set in the environment."""
    key = os.getenv('AWS_ACCESS_KEY_ID', '').strip()
    secret = os.getenv('AWS_SECRET_ACCESS_KEY', '').strip()
    return key not in _PLACEHOLDERS and secret not in _PLACEHOLDERS


# ── Credential availability cache ──────────────────────────────────────────
# Caches the STS:GetCallerIdentity result for AWS_CACHE_TTL seconds so that
# 6 services initialised in the same request do not each pay a ~2s STS
# round-trip. Cache is auto-invalidated when AWS_SESSION_TOKEN changes (env)
# or when switching between IAM-role and explicit-credential mode.
AWS_CACHE_TTL = 300  # 5 minutes

_aws_cache = {
    'result':     None,   # True | False | None (unset)
    'checked_at': 0.0,    # monotonic timestamp of last successful check
    'cache_key':  None,   # tracks credential identity to detect rotation
}


def _make_cache_key() -> str:
    """Return a short string that uniquely identifies the current credentials."""
    if _using_explicit_credentials():
        # Use first 20 chars of session token (or key id) to detect rotation
        token = os.getenv('AWS_SESSION_TOKEN', '')
        return f"explicit:{token[:20]}"
    return "iam_role"


def _invalidate_aws_cache() -> None:
    """Force the next check_aws_available() call to re-verify with AWS STS."""
    _aws_cache['result'] = None
    _aws_cache['checked_at'] = 0.0
    _aws_cache['cache_key'] = None


# ── Session factory ────────────────────────────────────────────────────────

def _make_iam_role_session(region: str) -> boto3.Session:
    """
    Create a boto3 Session that SKIPS environment variable credentials.

    Even when boto3.Session() is called without explicit credentials, it
    still reads AWS_ACCESS_KEY_ID etc. from os.environ as part of its
    automatic credential chain. If those env vars contain placeholder strings
    (e.g. 'REPLACE_IN_EB_CONSOLE'), every API call fails.

    This function removes the 'env' credential provider from botocore's
    resolver before creating the session, so boto3 skips env vars entirely
    and falls through directly to the EC2 instance profile (LabRole).
    """
    import botocore.session as bc_session

    bc = bc_session.get_session()
    resolver = bc.get_component('credential_provider')
    try:
        resolver.remove('env')      # skip AWS_ACCESS_KEY_ID env var lookup
    except Exception:
        pass                        # safe to ignore if already removed

    logger.debug("IAM role mode: env credential provider removed, using instance profile.")
    return boto3.Session(botocore_session=bc, region_name=region)


def get_boto3_session() -> boto3.Session:
    """
    Return a boto3.Session appropriate for the current environment.

    Mode A — Explicit credentials (local dev / AWS Academy):
      AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY are set to REAL (non-
      placeholder) values. Passes them explicitly including session token.

    Mode B — IAM role (Elastic Beanstalk with LabRole attached):
      Env vars are absent, empty, or set to a placeholder string such as
      'REPLACE_IN_EB_CONSOLE'. Uses _make_iam_role_session() which removes
      the botocore env credential provider so boto3 goes straight to the
      EC2 instance metadata service (LabRole).

    Region always comes from AWS_DEFAULT_REGION (default: us-east-1).
    """
    region = os.getenv('AWS_DEFAULT_REGION', 'us-east-1')

    if _using_explicit_credentials():
        session_token = os.getenv('AWS_SESSION_TOKEN') or None  # '' -> None
        return boto3.Session(
            aws_access_key_id=os.getenv('AWS_ACCESS_KEY_ID'),
            aws_secret_access_key=os.getenv('AWS_SECRET_ACCESS_KEY'),
            aws_session_token=session_token,
            region_name=region,
        )

    # Placeholder / absent credentials — bypass env vars, use IAM role
    return _make_iam_role_session(region)


# ── Credential availability check (cached) ────────────────────────────────

def check_aws_available() -> bool:
    """
    Validate that AWS credentials are usable by calling STS:GetCallerIdentity.

    Works in both explicit-credential mode (env vars) and IAM-role mode
    (EC2 instance profile). The result is cached for AWS_CACHE_TTL seconds.
    Cache is auto-invalidated when the credential identity changes (token
    rotation in AWS Academy, or switch from IAM-role to explicit mode).

    Returns:
        True  — credentials are valid and STS call succeeded.
        False — no usable credentials found (SNS / DynamoDB will be skipped).
    """
    cache_key = _make_cache_key()

    # Invalidate if credential identity has changed
    if cache_key != _aws_cache['cache_key']:
        _invalidate_aws_cache()
        logger.debug(f"AWS credential identity changed ({cache_key}) — cache invalidated.")

    # Serve from cache if still fresh
    now = time.monotonic()
    if _aws_cache['result'] is not None and (now - _aws_cache['checked_at']) < AWS_CACHE_TTL:
        return _aws_cache['result']

    # Cache miss — verify with a real STS call
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
                "and paste them into EB Console → Configuration → Software → "
                "Environment Properties (AWS_ACCESS_KEY_ID / SECRET / SESSION_TOKEN)."
            )
        elif code in ('InvalidClientTokenId', 'AuthFailure', 'InvalidAccessKeyId'):
            logger.error(
                f"AWS credentials INVALID (code={code}). "
                "Check AWS_ACCESS_KEY_ID is correct in the environment."
            )
        elif code == 'AccessDenied':
            # STS GetCallerIdentity is denied but credentials do exist — treat as ok.
            logger.warning(
                "STS:GetCallerIdentity was denied (AccessDenied) — "
                "credentials exist but lack sts:GetCallerIdentity. Assuming available."
            )
            result = True
        else:
            logger.warning(f"STS credential check failed ({code}): {e}")

    except NoCredentialsError:
        if _using_explicit_credentials():
            logger.warning("AWS credentials set in env but boto3 could not load them.")
        else:
            logger.warning(
                "No AWS credentials found. "
                "On EB: ensure LabRole is attached as the EC2 instance profile. "
                "Locally: set AWS_ACCESS_KEY_ID / SECRET in .env."
            )

    except PartialCredentialsError as e:
        logger.warning(f"Incomplete AWS credentials: {e}")

    # Update cache
    _aws_cache['result'] = result
    _aws_cache['checked_at'] = now
    _aws_cache['cache_key'] = cache_key
    logger.debug(f"AWS credential cache updated: available={result}, ttl={AWS_CACHE_TTL}s, mode={'explicit' if _using_explicit_credentials() else 'iam_role'}")
    return result


# ── Helpers used by service classes ───────────────────────────────────────

def get_client(service_name: str):
    """
    Return (boto3_client, True) or (None, False).

    Does NOT make an extra STS call — caller is responsible for ensuring
    check_aws_available() has already passed.
    """
    try:
        return get_boto3_session().client(service_name), True
    except (NoCredentialsError, PartialCredentialsError) as e:
        logger.warning(f"No credentials for {service_name}: {e}")
        return None, False
    except Exception as e:
        logger.warning(f"Failed to create {service_name} client: {e}")
        return None, False


def get_resource(service_name: str):
    """
    Return (boto3_resource, True) or (None, False).

    Does NOT make an extra STS call — caller is responsible for ensuring
    check_aws_available() has already passed.
    """
    try:
        return get_boto3_session().resource(service_name), True
    except (NoCredentialsError, PartialCredentialsError) as e:
        logger.warning(f"No credentials for {service_name} resource: {e}")
        return None, False
    except Exception as e:
        logger.warning(f"Failed to create {service_name} resource: {e}")
        return None, False


def log_aws_error(e: Exception, context: str = '') -> None:
    """
    Log a boto3 ClientError with an actionable human-readable message.

    Args:
        e:       The caught exception.
        context: Short description of the operation (e.g. 'sns.publish').
    """
    prefix = f'[AWS] {context} — ' if context else '[AWS] '

    if not hasattr(e, 'response'):
        logger.error(f'{prefix}{e}')
        return

    code = e.response.get('Error', {}).get('Code', 'Unknown')
    msg  = e.response.get('Error', {}).get('Message', str(e))

    if code in ('ExpiredTokenException', 'ExpiredToken'):
        logger.error(
            f'{prefix}SESSION TOKEN EXPIRED. '
            'Refresh: AWS Academy → AWS Details → AWS CLI → copy all three values → '
            'EB Console → Configuration → Software → Environment Properties.'
        )
    elif code in ('AccessDeniedException', 'AuthorizationError'):
        logger.error(
            f'{prefix}ACCESS DENIED ({code}): {msg}. '
            'Ensure the EB instance profile (LabRole) has the required IAM permission.'
        )
    elif code == 'ResourceNotFoundException':
        logger.error(
            f'{prefix}RESOURCE NOT FOUND: {msg}. '
            'Run: python manage.py init_aws_resources'
        )
    elif code in ('InvalidClientTokenId', 'AuthFailure', 'InvalidAccessKeyId'):
        logger.error(
            f'{prefix}INVALID CREDENTIALS ({code}). '
            'Check AWS_ACCESS_KEY_ID is correct.'
        )
    else:
        logger.error(f'{prefix}ClientError {code}: {msg}')
