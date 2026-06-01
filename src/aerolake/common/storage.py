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

    def get_object_metadata(self, key: str) -> dict[str, str]:
        """Return the ``x-amz-meta-*`` metadata of an object (HEAD request).

        Does NOT download the object body. Cheap and fast — useful for
        consumers that need to check parameters before deciding to read.
        """
        try:
            response = self._client.head_object(Bucket=self.bucket, Key=key)
        except ClientError as exc:
            raise StorageError(f"head_object failed for {key!r}") from exc
        # boto3 normalizes 'x-amz-meta-foo' headers into the 'Metadata' dict
        # with lowercase keys and the prefix stripped.
        return dict(response.get("Metadata", {}))

    def get_object_tags(self, key: str) -> dict[str, str]:
        """Return the S3 object tags as a dict."""
        try:
            response = self._client.get_object_tagging(
                Bucket=self.bucket, Key=key
            )
        except ClientError as exc:
            raise StorageError(f"get_object_tagging failed for {key!r}") from exc
        return {item["Key"]: item["Value"] for item in response.get("TagSet", [])}

    def update_tags(self, key: str, tags: dict[str, str]) -> None:
        """Replace ALL tags on an existing object with the given dict.

        IMPORTANT — this is a REPLACE, not a merge. The S3 PutObjectTagging
        API overwrites the entire tag set of the object. So if an object has
        tags {a, b, c} and you call update_tags with {a, b}, then c is
        DELETED. To preserve existing tags, read them first with
        get_object_tags(), merge in your changes, then call this.

        We deliberately keep this method "dumb" (full replace) because that
        mirrors exactly what the underlying S3 API does. The merge logic,
        when needed, lives in the caller where the intent is explicit.

        Parameters
        ----------
        key
            S3 object key whose tags should be replaced.
        tags
            The complete new tag set. Keys 1-128 chars, values 0-256 chars,
            max 10 tags per object.
        """
        log = logger.bind(bucket=self.bucket, key=key, n_tags=len(tags))

        # The S3 PutObjectTagging API expects a specific structure:
        # a TagSet which is a list of {"Key": ..., "Value": ...} dicts.
        # This differs from the upload-time "Tagging" parameter which uses
        # a URL-encoded string. Same concept, different wire format —
        # one of S3's little inconsistencies.
        tag_set = [{"Key": k, "Value": v} for k, v in tags.items()]

        try:
            self._client.put_object_tagging(
                Bucket=self.bucket,
                Key=key,
                Tagging={"TagSet": tag_set},
            )
        except ClientError as exc:
            log.error("storage.update_tags.failed", error=str(exc))
            raise StorageError(f"Failed to update tags on {key!r}") from exc

        log.info("storage.update_tags.ok")

    # --- Upload and download ---------------------------------------------

    def upload_bytes(
        self,
        key: str,
        data: bytes,
        content_type: str = "application/octet-stream",
        *,
        metadata: dict[str, str] | None = None,
        tags: dict[str, str] | None = None,
    ) -> None:
        """Upload raw bytes to the bucket under the given key.

        Parameters
        ----------
        key
            S3 object key under the configured bucket.
        data
            Raw bytes to upload.
        content_type
            HTTP Content-Type header (default ``application/octet-stream``).
        metadata
            Optional dict of key/value strings stored as ``x-amz-meta-*``
            HTTP headers. Accessible via head_object without downloading
            the body. Keys must be ASCII; values are coerced to str.
        tags
            Optional dict of key/value strings attached as S3 object tags.
            Tags are indexable on the MinIO side and can drive lifecycle
            rules. Up to 10 tags per object; keys 1-128 chars,
            values 0-256 chars.
        """
        log = logger.bind(bucket=self.bucket, key=key, size=len(data))
        put_kwargs: dict = {
            "Bucket": self.bucket,
            "Key": key,
            "Body": data,
            "ContentType": content_type,
        }
        if metadata:
            # boto3 expects str -> str. Coerce defensively in case someone
            # passes numbers.
            put_kwargs["Metadata"] = {k: str(v) for k, v in metadata.items()}
        if tags:
            # S3 tags are passed as URL-encoded query string at upload time.
            # boto3 takes a plain string here; we build it manually.
            put_kwargs["Tagging"] = "&".join(
                f"{k}={v}" for k, v in tags.items()
            )

        try:
            self._client.put_object(**put_kwargs)
        except ClientError as exc:
            log.error("storage.upload.failed", error=str(exc))
            raise StorageError(f"Failed to upload {key!r}") from exc
        log.info(
            "storage.upload.ok",
            has_metadata=bool(metadata),
            has_tags=bool(tags),
        )

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

    def download_range(self, key: str, start: int, end: int | None = None) -> bytes:
        """Download only bytes ``[start, end]`` of an object (HTTP Range request).

        This is the mechanism behind **partial / seeked reads**: instead of
        pulling a whole (possibly multi-GB) capture, we fetch just the slice we
        need. It maps to the S3 ``Range: bytes=start-end`` header, served by both
        AWS and MinIO. ``end`` is **inclusive** (the S3 convention); if ``end``
        is None, we read to the end of the object.

        The caller computes the byte offsets from sample maths, e.g. to start at
        t = 200 s of a cf32 capture: ``start = 200 * sample_rate * 8``.
        """
        byte_range = f"bytes={start}-{'' if end is None else end}"
        log = logger.bind(bucket=self.bucket, key=key, byte_range=byte_range)
        try:
            response = self._client.get_object(
                Bucket=self.bucket, Key=key, Range=byte_range
            )
            data: bytes = response["Body"].read()
        except ClientError as exc:
            log.error("storage.download_range.failed", error=str(exc))
            raise StorageError(
                f"Failed to range-download {key!r} ({byte_range})"
            ) from exc
        log.info("storage.download_range.ok", size=len(data))
        return data

    def object_size(self, key: str) -> int:
        """Return an object's size in bytes via a HEAD request (no body)."""
        try:
            response = self._client.head_object(Bucket=self.bucket, Key=key)
        except ClientError as exc:
            raise StorageError(f"head_object failed for {key!r}") from exc
        return int(response["ContentLength"])

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
