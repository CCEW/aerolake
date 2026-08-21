"""S3 storage client for the AeroLake project.

Thin wrapper around boto3 that works with both AWS S3 and MinIO. When the
``s3_endpoint`` setting is empty, boto3 uses the official AWS endpoint
(also what moto intercepts during tests). When set, boto3 talks to the
custom endpoint (typically a local MinIO instance).
"""

from __future__ import annotations

import contextlib
import re
from collections.abc import Iterable, Iterator
from urllib.parse import quote, urlencode

import boto3
import structlog
from botocore.client import Config
from botocore.exceptions import ClientError, EndpointConnectionError

from aerolake.common.config import Settings, get_settings

logger = structlog.get_logger(__name__)

# S3 object tags accept only this set in a tag VALUE: letters, digits, space,
# and + - = . _ : / @. Anything else (commas, accents, …) makes S3 reject the
# request with "InvalidTag". We sanitise values before tagging — the full,
# verbatim value is always preserved in the SigMF metadata; the tag is just a
# searchable, sanitised key.
_S3_TAG_INVALID = re.compile(r"[^A-Za-z0-9 +\-=._:/@]")


def _safe_tag_value(value: object) -> str:
    """Reduce a value to S3 tag-safe characters (invalid ones -> '_')."""
    return _S3_TAG_INVALID.sub("_", str(value))


def _tagging_header(tags: dict[str, str]) -> str:
    """Build the ``Tagging`` query string for put_object / create_multipart.

    Each value is sanitised then URL-encoded, so spaces and special characters
    travel safely (the previous manual ``k=v&…`` join did neither, which broke
    uploads whenever a value contained a space or comma — e.g. a location).
    """
    return urlencode({k: _safe_tag_value(v) for k, v in tags.items()}, quote_via=quote)


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
        # TLS verification (see Settings): a CA bundle path takes precedence
        # (the clean fix for endpoints signed by an internal CA, e.g. FAST's
        # "ETS Montreal Root CA"); otherwise the verify flag applies. boto3's
        # `verify` accepts True (system store), False, or a CA bundle path.
        if self._settings.s3_ca_bundle:
            kwargs["verify"] = self._settings.s3_ca_bundle
        elif not self._settings.s3_verify_ssl:
            kwargs["verify"] = False
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
            raise StorageError(f"Cannot reach S3 endpoint {self._settings.s3_endpoint!r}") from exc
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "Unknown")
            log.error("storage.healthcheck.failed", error_code=code)
            raise StorageError(f"Bucket {self.bucket!r} unreachable: {code}") from exc
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
        # S3-compatible backends are not perfectly consistent about metadata
        # key casing. Normalize here so consumers can always use lower-case
        # x-amz-meta-* names such as "sample-rate" and "center-freq".
        return {str(k).lower(): str(v) for k, v in response.get("Metadata", {}).items()}

    def get_object_tags(self, key: str) -> dict[str, str]:
        """Return the S3 object tags as a dict."""
        try:
            response = self._client.get_object_tagging(Bucket=self.bucket, Key=key)
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
        tag_set = [{"Key": k, "Value": _safe_tag_value(v)} for k, v in tags.items()]

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
            # S3 tags travel as a URL-encoded query string; values are sanitised
            # + encoded so spaces/commas/etc. cannot break the upload.
            put_kwargs["Tagging"] = _tagging_header(tags)

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

    def upload_multipart(
        self,
        key: str,
        chunks: Iterable[bytes],
        content_type: str = "application/octet-stream",
        *,
        metadata: dict[str, str] | None = None,
        tags: dict[str, str] | None = None,
        part_size: int = 8 * 1024 * 1024,
    ) -> int:
        """Upload an object from a STREAM of byte chunks, via S3 multipart upload.

        Unlike :meth:`upload_bytes` (which needs the whole object in memory at
        once), this consumes an *iterator* of byte chunks and ships them part by
        part — so a multi-GB capture can be uploaded **as it is produced/read,
        without ever holding it all in RAM**. This is the mandate's "multipart
        upload to bypass local RAM constraints" (the upload half of ADR-002,
        complementing the Range-read half in ADR-009).

        Incoming chunks may be any size: we **coalesce** them into parts of
        ``part_size`` bytes. S3 requires every part *except the last* to be at
        least 5 MiB, so keep ``part_size >= 5 MiB`` (the 8 MiB default is safe).

        Metadata and tags are attached at creation time (same convention as
        upload_bytes). Returns the total bytes uploaded. On any failure the
        upload is **aborted**, so no dangling partial object is left behind.
        """
        log = logger.bind(bucket=self.bucket, key=key, part_size=part_size)

        # CreateMultipartUpload takes the same Metadata/Tagging as put_object.
        create_kwargs: dict = {
            "Bucket": self.bucket,
            "Key": key,
            "ContentType": content_type,
        }
        if metadata:
            create_kwargs["Metadata"] = {k: str(v) for k, v in metadata.items()}
        if tags:
            create_kwargs["Tagging"] = _tagging_header(tags)

        try:
            upload_id = self._client.create_multipart_upload(**create_kwargs)["UploadId"]
        except ClientError as exc:
            log.error("storage.multipart.create_failed", error=str(exc))
            raise StorageError(f"Failed to start multipart upload of {key!r}") from exc

        parts: list[dict] = []
        buffer = bytearray()
        part_number = 1
        total = 0

        def _flush(body: bytes) -> None:
            """Upload one part and remember its (PartNumber, ETag)."""
            nonlocal part_number
            resp = self._client.upload_part(
                Bucket=self.bucket,
                Key=key,
                PartNumber=part_number,
                UploadId=upload_id,
                Body=body,
            )
            parts.append({"PartNumber": part_number, "ETag": resp["ETag"]})
            part_number += 1

        try:
            for chunk in chunks:
                buffer.extend(chunk)
                total += len(chunk)
                # Emit full-size parts as soon as enough is buffered.
                while len(buffer) >= part_size:
                    _flush(bytes(buffer[:part_size]))
                    del buffer[:part_size]
            # Final part: the remainder (may be < 5 MiB — allowed for the last
            # part). Always upload at least one part so 'complete' is valid.
            if buffer or not parts:
                _flush(bytes(buffer))
            self._client.complete_multipart_upload(
                Bucket=self.bucket,
                Key=key,
                UploadId=upload_id,
                MultipartUpload={"Parts": parts},
            )
        except Exception as exc:
            # Abort so MinIO/S3 doesn't keep the orphaned parts around.
            with contextlib.suppress(ClientError):
                self._client.abort_multipart_upload(Bucket=self.bucket, Key=key, UploadId=upload_id)
            log.error("storage.multipart.failed", error=str(exc))
            if isinstance(exc, ClientError):
                raise StorageError(f"Multipart upload of {key!r} failed") from exc
            raise  # a non-storage error from the chunk iterator: propagate as-is

        log.info("storage.multipart.ok", parts=len(parts), size=total)
        return total

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
            response = self._client.get_object(Bucket=self.bucket, Key=key, Range=byte_range)
            data: bytes = response["Body"].read()
        except ClientError as exc:
            log.error("storage.download_range.failed", error=str(exc))
            raise StorageError(f"Failed to range-download {key!r} ({byte_range})") from exc
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
            raise StorageError(f"list_objects failed for prefix {prefix!r}") from exc

    def delete_object(self, key: str) -> None:
        """Delete a single object from the bucket."""
        log = logger.bind(bucket=self.bucket, key=key)
        try:
            self._client.delete_object(Bucket=self.bucket, Key=key)
        except ClientError as exc:
            log.error("storage.delete.failed", error=str(exc))
            raise StorageError(f"Failed to delete {key!r}") from exc
        log.info("storage.delete.ok")
