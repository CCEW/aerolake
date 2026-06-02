"""Integration test: a real round-trip against a LIVE MinIO (not moto).

Skipped unless ``AEROLAKE_RUN_INTEGRATION=1`` so the normal unit suite (which
only uses moto) is unaffected. The CI ``integration`` job spins up a MinIO
container, sets the AEROLAKE_S3_* env, and runs ``pytest -m integration``.

This is the "no longer only mocked" safety net: it exercises the real S3 wire
protocol (multipart, Range, tagging) end to end against an actual server.
"""

from __future__ import annotations

import contextlib
import os

import botocore.exceptions
import numpy as np
import pytest

from aerolake.common.config import Settings
from aerolake.common.storage import StorageClient

pytestmark = pytest.mark.integration

_RUN = os.environ.get("AEROLAKE_RUN_INTEGRATION") == "1"

MIB = 1024 * 1024


@pytest.mark.skipif(
    not _RUN, reason="set AEROLAKE_RUN_INTEGRATION=1 (needs a live MinIO)"
)
def test_real_minio_roundtrip() -> None:
    settings = Settings()  # AEROLAKE_S3_* from the environment
    client = StorageClient(settings)

    # Ensure the bucket exists (the test owns its bucket).
    with contextlib.suppress(botocore.exceptions.ClientError):
        client._client.create_bucket(Bucket=settings.s3_bucket)

    # --- upload_bytes + download + tags + range ---------------------------
    key = "_integration/blob.bin"
    data = (np.arange(100_000) % 256).astype(np.uint8).tobytes()  # ~100 KB
    client.upload_bytes(
        key, data, tags={"signal-type": "test", "quality": "raw"}
    )
    assert client.download_bytes(key) == data
    assert client.get_object_tags(key)["signal-type"] == "test"
    assert client.object_size(key) == len(data)
    assert client.download_range(key, 10, 19) == data[10:20]  # inclusive

    # --- streaming multipart (8 MB in 1 MB chunks → 2 parts) --------------
    mkey = "_integration/multi.bin"
    big = bytes(8 * MIB)
    chunks = [big[i : i + MIB] for i in range(0, len(big), MIB)]
    total = client.upload_multipart(mkey, iter(chunks), part_size=5 * MIB)
    assert total == len(big)
    assert client.download_bytes(mkey) == big

    # --- cleanup ----------------------------------------------------------
    client.delete_object(key)
    client.delete_object(mkey)
    assert not client.object_exists(key)
    assert not client.object_exists(mkey)
