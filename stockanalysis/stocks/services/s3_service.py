"""
S3 Service — boto3 integration for Amazon S3.

Provides file upload, presigned URL generation, and object listing
for stock reports, chart images, and cached datasets.
"""

import os
import io
import json
import logging
from datetime import datetime
from typing import Optional, List, Dict, Any

from botocore.exceptions import ClientError, NoCredentialsError
from stocks.services.aws_session import get_boto3_session, check_aws_available, log_aws_error

logger = logging.getLogger('stocks')


class S3Service:
    """
    Service class for Amazon S3 operations via boto3.
    Supports AWS Academy temporary credentials.
    """

    def __init__(self):
        """Initialize boto3 S3 client with env credentials."""
        self.bucket_name = os.getenv('S3_BUCKET_NAME', 'stock-platform-reports')
        self.available = check_aws_available()
        if not self.available:
            logger.warning("AWS credentials not configured. S3 unavailable.")
            return
        try:
            session = get_boto3_session()
            self.client = session.client('s3')
            logger.info("S3 service initialized successfully.")
        except (NoCredentialsError, Exception) as e:
            self.available = False
            logger.warning(f"S3 init failed: {e}")

    def create_bucket(self) -> bool:
        """Create the S3 bucket if it doesn't exist."""
        if not self.available:
            return False

        try:
            region = os.getenv('AWS_DEFAULT_REGION', 'us-east-1')
            if region == 'us-east-1':
                self.client.create_bucket(Bucket=self.bucket_name)
            else:
                self.client.create_bucket(
                    Bucket=self.bucket_name,
                    CreateBucketConfiguration={'LocationConstraint': region},
                )
            logger.info(f"S3 bucket '{self.bucket_name}' created.")
            return True
        except ClientError as e:
            if e.response['Error']['Code'] in ('BucketAlreadyExists',
                                                  'BucketAlreadyOwnedByYou'):
                logger.info(f"Bucket '{self.bucket_name}' already exists.")
                return True
            logger.error(f"Error creating bucket: {e}")
            return False

    # ── Upload Operations ─────────────────────────────────────────────

    def upload_file(self, file_content: bytes, key: str,
                    content_type: str = 'application/json') -> bool:
        """
        Upload file content to S3.

        Args:
            file_content: Raw bytes to upload.
            key: S3 object key (path).
            content_type: MIME type of the file.

        Returns:
            True on success, False on failure.
        """
        if not self.available:
            logger.warning("S3 unavailable — upload skipped.")
            return False

        try:
            self.client.put_object(
                Bucket=self.bucket_name,
                Key=key,
                Body=file_content,
                ContentType=content_type,
            )
            logger.info(f"Uploaded to S3: s3://{self.bucket_name}/{key}")
            return True
        except ClientError as e:
            log_aws_error(e, f"upload_file s3://{self.bucket_name}/{key}")
            return False

    def upload_json_report(self, data: Dict[str, Any], symbol: str,
                           report_type: str = 'analytics') -> bool:
        """Upload a JSON report to S3."""
        timestamp = datetime.utcnow().strftime('%Y%m%d_%H%M%S')
        key = f"reports/{symbol.upper()}/{report_type}_{timestamp}.json"
        content = json.dumps(data, indent=2, default=str).encode('utf-8')
        return self.upload_file(content, key, 'application/json')

    def upload_csv_report(self, csv_content: str, symbol: str,
                          report_type: str = 'data') -> bool:
        """Upload a CSV report to S3."""
        timestamp = datetime.utcnow().strftime('%Y%m%d_%H%M%S')
        key = f"reports/{symbol.upper()}/{report_type}_{timestamp}.csv"
        return self.upload_file(csv_content.encode('utf-8'), key, 'text/csv')

    def upload_chart_image(self, image_bytes: bytes, symbol: str,
                           chart_type: str = 'price') -> Optional[str]:
        """
        Upload a chart image to S3 and return its key.

        Returns:
            S3 key on success, None on failure.
        """
        timestamp = datetime.utcnow().strftime('%Y%m%d_%H%M%S')
        key = f"charts/{symbol.upper()}/{chart_type}_{timestamp}.png"
        if self.upload_file(image_bytes, key, 'image/png'):
            return key
        return None

    # ── URL Generation ────────────────────────────────────────────────

    def generate_presigned_url(self, key: str,
                                expiration: int = 3600) -> Optional[str]:
        """
        Generate a presigned URL for accessing an S3 object.

        Args:
            key: S3 object key.
            expiration: URL expiration in seconds (default 1 hour).

        Returns:
            Presigned URL string or None.
        """
        if not self.available:
            return None

        try:
            url = self.client.generate_presigned_url(
                'get_object',
                Params={'Bucket': self.bucket_name, 'Key': key},
                ExpiresIn=expiration,
            )
            logger.info(f"Generated presigned URL for: {key}")
            return url
        except ClientError as e:
            logger.error(f"Error generating presigned URL: {e}")
            return None

    # ── List Operations ───────────────────────────────────────────────

    def list_objects(self, prefix: str = '', max_keys: int = 100) -> List[Dict[str, Any]]:
        """
        List objects in the S3 bucket.

        Args:
            prefix: Filter by key prefix.
            max_keys: Maximum objects to return.

        Returns:
            List of object metadata dicts.
        """
        if not self.available:
            return []

        try:
            response = self.client.list_objects_v2(
                Bucket=self.bucket_name,
                Prefix=prefix,
                MaxKeys=max_keys,
            )
            objects = []
            for obj in response.get('Contents', []):
                objects.append({
                    'key': obj['Key'],
                    'size': obj['Size'],
                    'last_modified': obj['LastModified'].isoformat(),
                })
            return objects
        except ClientError as e:
            log_aws_error(e, f"list_objects s3://{self.bucket_name}/{prefix}")
            return []

    def delete_object(self, key: str) -> bool:
        """Delete an object from S3."""
        if not self.available:
            return False

        try:
            self.client.delete_object(Bucket=self.bucket_name, Key=key)
            logger.info(f"Deleted S3 object: {key}")
            return True
        except ClientError as e:
            logger.error(f"Error deleting S3 object: {e}")
            return False
