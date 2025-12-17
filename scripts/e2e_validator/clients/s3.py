"""
S3 Client Wrapper
==================

Wrapper around boto3 for cleaner S3 API interactions.
"""

import boto3
from typing import List, Dict, Optional


class S3Client:
    """Wrapper around boto3 for cleaner API"""

    def __init__(self, endpoint_url: str, access_key: str, secret_key: str,
                 region: str = 'us-east-1', verify: bool = False):
        """Initialize S3 client

        Args:
            endpoint_url: S3 endpoint URL
            access_key: AWS access key ID
            secret_key: AWS secret access key
            region: AWS region (default: us-east-1)
            verify: Verify SSL certificates (default: False for self-signed)
        """
        self.endpoint = endpoint_url
        self.s3 = boto3.client(
            's3',
            endpoint_url=endpoint_url,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name=region,
            verify=verify
        )

    def create_bucket(self, bucket: str):
        """Create S3 bucket

        Args:
            bucket: Bucket name
        """
        try:
            self.s3.create_bucket(Bucket=bucket)
        except self.s3.exceptions.BucketAlreadyOwnedByYou:
            pass  # Bucket already exists, that's fine
        except self.s3.exceptions.BucketAlreadyExists:
            pass  # Bucket exists, that's fine

    def bucket_exists(self, bucket: str) -> bool:
        """Check if bucket exists

        Args:
            bucket: Bucket name

        Returns:
            True if bucket exists
        """
        try:
            self.s3.head_bucket(Bucket=bucket)
            return True
        except:
            return False

    def upload_file(self, local_path: str, bucket: str, key: str):
        """Upload file to S3

        Args:
            local_path: Local file path
            bucket: Bucket name
            key: S3 object key
        """
        with open(local_path, 'rb') as f:
            self.s3.put_object(Bucket=bucket, Key=key, Body=f.read())

    def upload_bytes(self, data: bytes, bucket: str, key: str):
        """Upload bytes to S3

        Args:
            data: Bytes to upload
            bucket: Bucket name
            key: S3 object key
        """
        self.s3.put_object(Bucket=bucket, Key=key, Body=data)

    def list_objects(self, bucket: str, prefix: str = '') -> List[Dict]:
        """List objects in bucket

        Args:
            bucket: Bucket name
            prefix: Key prefix filter

        Returns:
            List of object metadata dicts
        """
        response = self.s3.list_objects_v2(Bucket=bucket, Prefix=prefix)
        return response.get('Contents', [])

    def object_exists(self, bucket: str, key: str) -> bool:
        """Check if object exists

        Args:
            bucket: Bucket name
            key: S3 object key

        Returns:
            True if object exists
        """
        try:
            self.s3.head_object(Bucket=bucket, Key=key)
            return True
        except:
            return False

    def get_object(self, bucket: str, key: str) -> bytes:
        """Get object from S3

        Args:
            bucket: Bucket name
            key: S3 object key

        Returns:
            Object data as bytes
        """
        response = self.s3.get_object(Bucket=bucket, Key=key)
        return response['Body'].read()

    def delete_object(self, bucket: str, key: str):
        """Delete object from S3

        Args:
            bucket: Bucket name
            key: S3 object key
        """
        self.s3.delete_object(Bucket=bucket, Key=key)

    def delete_objects(self, bucket: str, keys: List[str]):
        """Delete multiple objects from S3

        Args:
            bucket: Bucket name
            keys: List of S3 object keys
        """
        if not keys:
            return

        objects = [{'Key': key} for key in keys]
        self.s3.delete_objects(
            Bucket=bucket,
            Delete={'Objects': objects}
        )

