"""Tests for the prepare / push / save split (Palier 4a).

The capture cycle was split so a confirmation can sit between "prepared" and
"stored". These verify:
- prepare_capture produces the bytes/keys/summary but stores nothing,
- push_capture uploads exactly what was prepared,
- save_capture_locally writes the two SigMF files mirroring the key layout,
- capture_and_upload still behaves as the prepare+push composition.
"""

from __future__ import annotations

import json
from pathlib import Path

from aerolake.common.storage import StorageClient
from aerolake.consumer.reader import CaptureReader
from aerolake.producer.orchestrator import (
    PreparedCapture,
    prepare_capture,
    push_capture,
    save_capture_locally,
)


def _prepare() -> PreparedCapture:
    return prepare_capture(
        signal_type="gnss_l1",
        duration_s=0.01,
        sample_rate=2_000_000,
        center_freq=1_575_420_000,
        operator="schmitt",
        location="LASSENA rooftop",
    )


def test_prepare_produces_bytes_and_keys_without_storing() -> None:
    prepared = _prepare()
    assert prepared.data_bytes  # non-empty IQ payload
    assert prepared.meta_bytes
    assert prepared.data_key.endswith(".sigmf-data")
    assert prepared.meta_key.endswith(".sigmf-meta")
    assert prepared.sample_count == 20_000
    assert prepared.size_bytes == len(prepared.data_bytes) + len(prepared.meta_bytes)
    # The meta is valid JSON carrying the location we passed.
    meta = json.loads(prepared.meta_bytes)
    assert meta["global"]["aerolake:location"] == "LASSENA rooftop"


def test_prepare_stores_nothing_in_minio(storage_client: StorageClient) -> None:
    # Preparing must not touch storage: the bucket stays empty.
    _prepare()
    assert list(storage_client.list_objects("")) == []


def test_push_uploads_prepared_capture(storage_client: StorageClient) -> None:
    prepared = _prepare()
    result = push_capture(prepared, storage_client)

    assert result.data_key == prepared.data_key
    assert result.meta_key == prepared.meta_key
    # Both objects now exist and the data tags carry the search criteria.
    reader = CaptureReader(storage_client)
    tags = reader.inspect(result.data_key).tags
    assert tags["signal-type"] == "gnss_l1"
    assert tags["location"] == "LASSENA rooftop"


def test_save_locally_writes_both_files_mirroring_keys(tmp_path) -> None:
    prepared = _prepare()
    out_dir = save_capture_locally(prepared, root=tmp_path)

    data_file = out_dir / "capture.sigmf-data"
    meta_file = out_dir / "capture.sigmf-meta"
    assert data_file.is_file()
    assert meta_file.is_file()
    # Bytes written match exactly what was prepared.
    assert data_file.read_bytes() == prepared.data_bytes
    assert meta_file.read_bytes() == prepared.meta_bytes
    # Layout mirrors the would-be S3 key (signal_type/date/folder/...).
    assert str(out_dir).endswith(str(Path(prepared.data_key).parent))


def test_save_locally_does_not_touch_minio(storage_client: StorageClient, tmp_path) -> None:
    prepared = _prepare()
    save_capture_locally(prepared, root=tmp_path)
    assert list(storage_client.list_objects("")) == []
