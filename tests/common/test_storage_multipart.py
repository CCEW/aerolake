"""Tests for streaming multipart upload (upload_multipart)."""

from __future__ import annotations

import pytest

from aerolake.common.storage import StorageClient

MIB = 1024 * 1024


def test_multipart_coalesces_small_chunks_and_roundtrips(
    storage_client: StorageClient,
) -> None:
    # 12 chunks of 1 MiB → with an 8 MiB part_size, that's one 8 MiB part + a
    # 4 MiB final part (both valid: non-last >= 5 MiB, last has no minimum).
    chunks = [bytes([i]) * MIB for i in range(12)]
    expected = b"".join(chunks)

    total = storage_client.upload_multipart("big/blob", iter(chunks), part_size=8 * MIB)

    assert total == len(expected)
    assert storage_client.download_bytes("big/blob") == expected


def test_multipart_single_small_object(storage_client: StorageClient) -> None:
    # Smaller than one part → a single (last) part, no size minimum.
    total = storage_client.upload_multipart("small/blob", iter([b"hello world"]))
    assert total == 11
    assert storage_client.download_bytes("small/blob") == b"hello world"


def test_multipart_attaches_metadata_and_tags(storage_client: StorageClient) -> None:
    storage_client.upload_multipart(
        "tagged/blob",
        iter([b"x" * 100]),
        metadata={"sample-rate": "2000000"},
        tags={"signal-type": "gnss_l1", "quality": "raw"},
    )
    assert storage_client.get_object_metadata("tagged/blob")["sample-rate"] == "2000000"
    assert storage_client.get_object_tags("tagged/blob")["signal-type"] == "gnss_l1"


def test_multipart_aborts_when_chunk_iterator_raises(
    storage_client: StorageClient,
) -> None:
    """A failure mid-stream aborts the upload (no object left behind)."""

    def bad_chunks():
        yield b"a" * MIB
        raise RuntimeError("source died")

    with pytest.raises(RuntimeError, match="source died"):
        storage_client.upload_multipart("doomed/blob", bad_chunks())

    # The upload was aborted → the object must not exist.
    assert not storage_client.object_exists("doomed/blob")
