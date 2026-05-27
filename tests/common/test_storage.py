"""Unit tests for aerolake.common.storage.StorageClient.

Tests cover the 6 public methods (health_check, object_exists,
upload_bytes, download_bytes, list_objects, delete_object) and the
StorageError exception contract.
"""

from __future__ import annotations

import pytest

from aerolake.common.config import Settings
from aerolake.common.storage import StorageClient, StorageError


# --- health_check --------------------------------------------------------

def test_health_check_returns_true_when_bucket_exists(storage_client: StorageClient) -> None:
    """A reachable, existing bucket should make health_check return True."""
    assert storage_client.health_check() is True


def test_health_check_raises_when_bucket_missing(test_settings: Settings, mock_s3) -> None:
    """A non-existent bucket should raise StorageError."""
    bad_settings = test_settings.model_copy(update={"s3_bucket": "does-not-exist"})
    client = StorageClient(bad_settings)
    with pytest.raises(StorageError, match="unreachable"):
        client.health_check()


# --- upload_bytes / download_bytes round-trip -----------------------------

def test_upload_and_download_roundtrip(storage_client: StorageClient) -> None:
    """Uploaded bytes should be byte-identical when downloaded."""
    payload = b"hello aerolake, this is test content"
    storage_client.upload_bytes("test/hello.txt", payload, content_type="text/plain")

    downloaded = storage_client.download_bytes("test/hello.txt")
    assert downloaded == payload


def test_download_missing_key_raises(storage_client: StorageClient) -> None:
    """Downloading a non-existent key should raise StorageError."""
    with pytest.raises(StorageError):
        storage_client.download_bytes("nonexistent/key.bin")


# --- object_exists --------------------------------------------------------

def test_object_exists_returns_true_after_upload(storage_client: StorageClient) -> None:
    """object_exists should return True for an object that was just uploaded."""
    storage_client.upload_bytes("present.txt", b"x")
    assert storage_client.object_exists("present.txt") is True


def test_object_exists_returns_false_for_missing_key(storage_client: StorageClient) -> None:
    """object_exists should return False (not raise) for a non-existent key."""
    assert storage_client.object_exists("absolutely/missing.txt") is False


# --- list_objects --------------------------------------------------------

def test_list_objects_filters_by_prefix(storage_client: StorageClient) -> None:
    """Only objects matching the prefix should be yielded."""
    storage_client.upload_bytes("foo/a.txt", b"1")
    storage_client.upload_bytes("foo/b.txt", b"2")
    storage_client.upload_bytes("bar/c.txt", b"3")

    foo_keys = sorted(storage_client.list_objects(prefix="foo/"))
    assert foo_keys == ["foo/a.txt", "foo/b.txt"]


def test_list_objects_empty_prefix_returns_all(storage_client: StorageClient) -> None:
    """An empty prefix should yield every key in the bucket."""
    storage_client.upload_bytes("a.txt", b"1")
    storage_client.upload_bytes("nested/b.txt", b"2")

    all_keys = sorted(storage_client.list_objects())
    assert all_keys == ["a.txt", "nested/b.txt"]


# --- delete_object --------------------------------------------------------

def test_delete_object_removes_it(storage_client: StorageClient) -> None:
    """After delete_object, object_exists should return False."""
    storage_client.upload_bytes("to_delete.txt", b"bye")
    assert storage_client.object_exists("to_delete.txt") is True

    storage_client.delete_object("to_delete.txt")
    assert storage_client.object_exists("to_delete.txt") is False


# --- bucket property ------------------------------------------------------

def test_bucket_property_returns_configured_bucket(storage_client: StorageClient) -> None:
    """The bucket property should return the value from settings."""
    assert storage_client.bucket == "test-bucket"


# --- Metadata and tags ---------------------------------------------------

def test_upload_bytes_stores_metadata(storage_client) -> None:
    """Metadata passed to upload_bytes is retrievable via head_object."""
    storage_client.upload_bytes(
        "with_meta.txt",
        b"hello",
        metadata={"sample-rate": "2000000", "session-id": "abc12345"},
    )
    fetched = storage_client.get_object_metadata("with_meta.txt")
    assert fetched["sample-rate"] == "2000000"
    assert fetched["session-id"] == "abc12345"


def test_upload_bytes_stores_tags(storage_client) -> None:
    """Tags passed to upload_bytes are retrievable via get_object_tagging."""
    storage_client.upload_bytes(
        "with_tags.txt",
        b"hello",
        tags={"signal-type": "gnss_l1", "quality": "raw"},
    )
    fetched = storage_client.get_object_tags("with_tags.txt")
    assert fetched["signal-type"] == "gnss_l1"
    assert fetched["quality"] == "raw"


def test_upload_bytes_without_metadata_returns_empty_dict(storage_client) -> None:
    """An object uploaded without metadata returns an empty metadata dict."""
    storage_client.upload_bytes("no_meta.txt", b"hello")
    assert storage_client.get_object_metadata("no_meta.txt") == {}


def test_upload_bytes_without_tags_returns_empty_dict(storage_client) -> None:
    """An object uploaded without tags returns an empty tag dict."""
    storage_client.upload_bytes("no_tags.txt", b"hello")
    assert storage_client.get_object_tags("no_tags.txt") == {}
