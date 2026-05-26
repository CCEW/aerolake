"""S3 storage client for the AeroLake project.

Thin wrapper around boto3 that works with both AWS S3 and MinIO. When the
``s3_endpoint`` setting is empty, boto3 uses the official AWS endpoint
(also what moto intercepts during tests). When set, boto3 talks to the
custom endpoint (typically a local MinIO instance).
"""

from __future__ import annotations

from collections.abc import Iterator

import boto3
import structlog
from botocore.client import Config
from botocore.exceptions import ClientError, EndpointConnectionError

from aerolake.common.config import Settings, get_settings

logger = structlog.get_logger(__name__)


class StorageError(Exception):
    """Raised for any storage-layer failure the caller should handle."""


class StorageClient:
    """High-level wrapper around the S3 client.

    Provides the operations AeroLake needs (health check, upload, download,
    list, exists, delete) with consistent error handling and structured
    logging. Settings can be injected to ease testing.
    """

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self._client = self._build_client()

    def _build_client(self):
        """Construct the boto3 S3 client.

        When ``s3_endpoint`` is empty, boto3 uses the official AWS S3
        endpoint. This is what we want both for production AWS deployments
        and for testing with moto (which intercepts the default endpoint).

        When ``s3_endpoint`` is set (e.g. ``http://localhost:9000`` for
        local MinIO), we pass it explicitly so boto3 talks to that server
        instead of AWS.

        Other configuration:
        - signature version v4 (required by MinIO, default on AWS)
        - path-style addressing (required by MinIO for arbitrary bucket names)
        - 3 automatic retries with exponential backoff for transient failures
        """
        kwargs: dict = {
            "aws_access_key_id": self._settings.s3_access_key,
            "aws_secret_access_key": self._settings.s3_secret_key.get_secret_value(),
            "region_name": self._settings.s3_region,
            "config": Config(
                signature_version="s3v4",
                s3={"addressing_style": "path"},
                retries={"max_attempts": 3, "mode": "standard"},
            ),
        }
        if self._settings.s3_endpoint:
            kwargs["endpoint_url"] = self._settings.s3_endpoint
        return boto3.client("s3", **kwargs)

    @property
    def bucket(self) -> str:
        """The default bucket name from settings."""
        return self._settings.s3_bucket

    # --- Health and metadata ---------------------------------------------

    def health_check(self) -> bool:
        """Verify the bucket is reachable and accessible.

        Returns True on success, raises StorageError on any failure.
        """
        log = logger.bind(bucket=self.bucket, endpoint=self._settings.s3_endpoint)
        try:
            self._client.head_bucket(Bucket=self.bucket)
        except EndpointConnectionError as exc:
            log.error("storage.healthcheck.unreachable")
            raise StorageError(
                f"Cannot reach S3 endpoint {self._settings.s3_endpoint!r}"
            ) from exc
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "Unknown")
            log.error("storage.healthcheck.failed", error_code=code)
            raise StorageError(
                f"Bucket {self.bucket!r} unreachable: {code}"
            ) from exc
        log.info("storage.healthcheck.ok")
        return True

    def object_exists(self, key: str) -> bool:
        """Return True if an object with the given key exists in the bucket."""
        try:
            self._client.head_object(Bucket=self.bucket, Key=key)
            return True
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in ("404", "NoSuchKey"):
                return False
            raise StorageError(f"head_object failed for {key!r}") from exc

    # --- Upload and download ---------------------------------------------

    def upload_bytes(
        self,
        key: str,
        data: bytes,
        content_type: str = "application/octet-stream",
    ) -> None:
        """Upload raw bytes to the bucket under the given key."""
        log = logger.bind(bucket=self.bucket, key=key, size=len(data))
        try:
            self._client.put_object(
                Bucket=self.bucket,
                Key=key,
                Body=data,
                ContentType=content_type,
            )
        except ClientError as exc:
            log.error("storage.upload.failed", error=str(exc))
            raise StorageError(f"Failed to upload {key!r}") from exc
        log.info("storage.upload.ok")

    def download_bytes(self, key: str) -> bytes:
        """Download an object and return its full content as bytes."""
        log = logger.bind(bucket=self.bucket, key=key)
        try:
            response = self._client.get_object(Bucket=self.bucket, Key=key)
            data: bytes = response["Body"].read()
        except ClientError as exc:
            log.error("storage.download.failed", error=str(exc))
            raise StorageError(f"Failed to download {key!r}") from exc
        log.info("storage.download.ok", size=len(data))
        return data

    # --- Listing and deletion --------------------------------------------

    def list_objects(self, prefix: str = "") -> Iterator[str]:
        """Yield object keys under the given prefix (paginated under the hood)."""
        paginator = self._client.get_paginator("list_objects_v2")
        try:
            for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
                for obj in page.get("Contents", []):
                    yield obj["Key"]
        except ClientError as exc:
            raise StorageError(
                f"list_objects failed for prefix {prefix!r}"
            ) from exc

    def delete_object(self, key: str) -> None:
        """Delete a single object from the bucket."""
        log = logger.bind(bucket=self.bucket, key=key)
        try:
            self._client.delete_object(Bucket=self.bucket, Key=key)
        except ClientError as exc:
            log.error("storage.delete.failed", error=str(exc))
            raise StorageError(f"Failed to delete {key!r}") from exc
        log.info("storage.delete.ok")
