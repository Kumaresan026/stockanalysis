"""
S3 Service — IAM role authentication via default credential chain.

No credential injection. boto3 uses the EC2 instance profile (LabRole) on EB.
All methods catch exceptions and fail gracefully.
"""

import os
import json
import logging
from datetime import datetime
from typing import Optional, List, Dict, Any

from botocore.exceptions import ClientError
from stocks.services.aws_session import get_client, log_aws_error

logger = logging.getLogger('stocks')


class S3Service:
    """
    S3 file storage — uses LabRole instance profile on EB.
    Zero credential injection; boto3 handles auth automatically.
    """

    def __init__(self):
        """Lightweight init — no AWS calls."""
        self.bucket_name = os.getenv('S3_BUCKET_NAME', 'stock-platform-reports')
        self.region      = os.getenv('AWS_DEFAULT_REGION', 'us-east-1')
        self._client     = None   # lazy

    # ── Lazy boto3 client ─────────────────────────────────────────────────────

    @property
    def client(self):
        if self._client is None:
            self._client = get_client('s3')
        return self._client

    # ── Bucket Management ─────────────────────────────────────────────────────

    def create_bucket(self) -> bool:
        """Create the S3 bucket if it doesn't exist."""
        try:
            if self.region == 'us-east-1':
                self.client.create_bucket(Bucket=self.bucket_name)
            else:
                self.client.create_bucket(
                    Bucket=self.bucket_name,
                    CreateBucketConfiguration={'LocationConstraint': self.region},
                )
            logger.info(f"S3 bucket '{self.bucket_name}' created.")
            return True
        except ClientError as e:
            if e.response['Error']['Code'] in ('BucketAlreadyExists',
                                                'BucketAlreadyOwnedByYou'):
                logger.info(f"Bucket '{self.bucket_name}' already exists.")
                return True
            log_aws_error(e, f"s3.create_bucket bucket={self.bucket_name}")
            return False
        except Exception as e:
            logger.warning(f"[S3] create_bucket failed: {e}")
            return False

    # ── Upload Operations ─────────────────────────────────────────────────────

    def upload_file(self, file_content: bytes, key: str,
                    content_type: str = 'application/json') -> bool:
        """
        Upload file content to S3.

        Returns True on success, False on any failure (never raises).
        """
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
            log_aws_error(e, f"s3.upload_file key={key}")
            return False
        except Exception as e:
            logger.warning(f"[S3] upload_file failed for key={key}: {e}")
            return False

    def upload_json_report(self, data: Dict[str, Any], symbol: str,
                           report_type: str = 'analytics') -> bool:
        """Upload a JSON report to S3."""
        timestamp = datetime.utcnow().strftime('%Y%m%d_%H%M%S')
        key       = f"reports/{symbol.upper()}/{report_type}_{timestamp}.json"
        content   = json.dumps(data, indent=2, default=str).encode('utf-8')
        return self.upload_file(content, key, 'application/json')

    def upload_csv_report(self, csv_content: str, symbol: str,
                          report_type: str = 'data') -> bool:
        """Upload a CSV report to S3."""
        timestamp = datetime.utcnow().strftime('%Y%m%d_%H%M%S')
        key       = f"reports/{symbol.upper()}/{report_type}_{timestamp}.csv"
        return self.upload_file(csv_content.encode('utf-8'), key, 'text/csv')

    def upload_chart_image(self, image_bytes: bytes, symbol: str,
                           chart_type: str = 'price') -> Optional[str]:
        """Upload a chart image to S3 and return its key, or None on failure."""
        timestamp = datetime.utcnow().strftime('%Y%m%d_%H%M%S')
        key       = f"charts/{symbol.upper()}/{chart_type}_{timestamp}.png"
        if self.upload_file(image_bytes, key, 'image/png'):
            return key
        return None

    # ── URL Generation ────────────────────────────────────────────────────────

    def generate_presigned_url(self, key: str,
                                expiration: int = 3600) -> Optional[str]:
        """Generate a presigned URL for an S3 object. Returns None on failure."""
        try:
            url = self.client.generate_presigned_url(
                'get_object',
                Params={'Bucket': self.bucket_name, 'Key': key},
                ExpiresIn=expiration,
            )
            logger.info(f"Generated presigned URL for: {key}")
            return url
        except Exception as e:
            logger.warning(f"[S3] generate_presigned_url failed for key={key}: {e}")
            return None

    # ── List / Delete ─────────────────────────────────────────────────────────

    def list_objects(self, prefix: str = '', max_keys: int = 100) -> List[Dict[str, Any]]:
        """List objects in the S3 bucket. Returns [] on failure."""
        try:
            response = self.client.list_objects_v2(
                Bucket=self.bucket_name,
                Prefix=prefix,
                MaxKeys=max_keys,
            )
            return [
                {
                    'key':           obj['Key'],
                    'size':          obj['Size'],
                    'last_modified': obj['LastModified'].isoformat(),
                }
                for obj in response.get('Contents', [])
            ]
        except ClientError as e:
            log_aws_error(e, f"s3.list_objects prefix={prefix}")
            return []
        except Exception as e:
            logger.warning(f"[S3] list_objects failed: {e}")
            return []

    def delete_object(self, key: str) -> bool:
        """Delete an object from S3. Returns False on failure."""
        try:
            self.client.delete_object(Bucket=self.bucket_name, Key=key)
            logger.info(f"Deleted S3 object: {key}")
            return True
        except Exception as e:
            logger.warning(f"[S3] delete_object failed for key={key}: {e}")
            return False
