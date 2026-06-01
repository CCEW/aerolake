"""Tests for the partial-read storage primitives (HTTP Range + object size)."""

from __future__ import annotations

from aerolake.common.storage import StorageClient


def test_download_range_returns_inclusive_slice(storage_client: StorageClient) -> None:
    storage_client.upload_bytes("blob", bytes(range(100)))  # bytes 0,1,...,99
    # Range is inclusive on both ends: bytes 10..19 -> 10 bytes.
    out = storage_client.download_range("blob", 10, 19)
    assert out == bytes(range(10, 20))


def test_download_range_open_ended_reads_to_end(storage_client: StorageClient) -> None:
    storage_client.upload_bytes("blob", b"abcdefghij")
    assert storage_client.download_range("blob", 5) == b"fghij"


def test_object_size_returns_byte_length(storage_client: StorageClient) -> None:
    storage_client.upload_bytes("blob", b"x" * 1234)
    assert storage_client.object_size("blob") == 1234
