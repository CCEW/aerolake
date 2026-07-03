"""Unit tests for aerolake.consumer.collection.CollectionBuilder (Palier A).

Everything runs against the moto-mocked ``storage_client`` fixture: we seed a
few Recordings (and some decoys) under a prefix, then check the builder pairs
them up, hashes their metadata, reports orphans, and assembles a conformant
collection object — all without a CLI.
"""

from __future__ import annotations

import hashlib
import json

import pytest

from aerolake.common.storage import StorageClient
from aerolake.consumer.collection import CollectionBuilder
from aerolake.producer.sigmf_writer import SIGMF_VERSION


@pytest.fixture
def builder(storage_client: StorageClient) -> CollectionBuilder:
    """A CollectionBuilder wired to the moto-mocked storage_client."""
    return CollectionBuilder(storage_client=storage_client)


def _seed_recording(
    storage_client: StorageClient,
    data_key: str,
    *,
    sample_rate: float = 2_000_000.0,
) -> bytes:
    """Seed a complete Recording (data + meta) and return the meta bytes.

    The .sigmf-data body is a tiny placeholder (the builder only hashes the
    .sigmf-meta). Returning the meta bytes lets a test recompute the expected
    SHA-512 the exact same way the builder does.
    """
    sigmf_meta = {
        "global": {
            "core:datatype": "cf32_le",
            "core:sample_rate": sample_rate,
            "core:version": SIGMF_VERSION,
        },
        "captures": [{"core:sample_start": 0}],
        "annotations": [],
    }
    meta_bytes = json.dumps(sigmf_meta).encode("utf-8")
    meta_key = data_key[: -len(".sigmf-data")] + ".sigmf-meta"
    storage_client.upload_bytes(meta_key, meta_bytes, content_type="application/json")
    storage_client.upload_bytes(
        data_key, b"\x00\x00\x00\x00", content_type="application/octet-stream"
    )
    return meta_bytes


# --- scan ----------------------------------------------------------------


def test_scan_pairs_complete_and_flags_orphans(
    builder: CollectionBuilder, storage_client: StorageClient
) -> None:
    _seed_recording(storage_client, "gnss_l1/2026-06-17/recA/capture.sigmf-data")
    _seed_recording(storage_client, "gnss_l1/2026-06-17/recB/capture.sigmf-data")
    # Orphan 1: a .sigmf-data with no matching .sigmf-meta.
    storage_client.upload_bytes("gnss_l1/2026-06-17/orphanData/capture.sigmf-data", b"x")
    # Orphan 2: a .sigmf-meta with no matching .sigmf-data.
    storage_client.upload_bytes("gnss_l1/2026-06-17/orphanMeta/capture.sigmf-meta", b"{}")

    complete, orphans = builder.scan("gnss_l1/2026-06-17/")

    assert complete == [
        (
            "gnss_l1/2026-06-17/recA/capture.sigmf-data",
            "gnss_l1/2026-06-17/recA/capture.sigmf-meta",
        ),
        (
            "gnss_l1/2026-06-17/recB/capture.sigmf-data",
            "gnss_l1/2026-06-17/recB/capture.sigmf-meta",
        ),
    ]
    assert orphans == [
        "gnss_l1/2026-06-17/orphanData/capture.sigmf-data",
        "gnss_l1/2026-06-17/orphanMeta/capture.sigmf-meta",
    ]


def test_scan_ignores_non_sigmf_objects(
    builder: CollectionBuilder, storage_client: StorageClient
) -> None:
    """A quality_report.json / pre-existing collection are not orphans."""
    _seed_recording(storage_client, "gnss_l1/2026-06-17/recA/capture.sigmf-data")
    storage_client.upload_bytes("gnss_l1/2026-06-17/recA/quality_report.json", b"{}")
    storage_client.upload_bytes("gnss_l1/2026-06-17/old.sigmf-collection", b"{}")

    complete, orphans = builder.scan("gnss_l1/2026-06-17/")

    assert len(complete) == 1
    assert orphans == []


# --- build ---------------------------------------------------------------


def test_build_hashes_meta_and_builds_relative_names(
    builder: CollectionBuilder, storage_client: StorageClient
) -> None:
    meta_a = _seed_recording(storage_client, "gnss_l1/2026-06-17/recA/capture.sigmf-data")
    meta_b = _seed_recording(storage_client, "gnss_l1/2026-06-17/recB/capture.sigmf-data")

    plan = builder.build(
        prefix="gnss_l1/2026-06-17/",
        name="june campaign",
        description="GNSS L1 campaign",
        author="schmitt",
    )

    # Names are relative to the collection dir, extension stripped.
    names = [s.name for s in plan.streams]
    assert names == ["recA/capture", "recB/capture"]

    # Hash is SHA-512 of the exact stored meta bytes.
    by_name = {s.name: s.meta_hash for s in plan.streams}
    assert by_name["recA/capture"] == hashlib.sha512(meta_a).hexdigest()
    assert by_name["recB/capture"] == hashlib.sha512(meta_b).hexdigest()


def test_build_assembles_conformant_collection_object(
    builder: CollectionBuilder, storage_client: StorageClient
) -> None:
    _seed_recording(storage_client, "gnss_l1/2026-06-17/recA/capture.sigmf-data")

    plan = builder.build(
        prefix="gnss_l1/2026-06-17/",
        name="june",
        description="desc",
        author="schmitt",
    )

    assert set(plan.collection_obj) == {"collection"}
    coll = plan.collection_obj["collection"]
    assert coll["core:version"] == SIGMF_VERSION
    assert coll["core:description"] == "desc"
    assert coll["core:author"] == "schmitt"
    assert coll["core:streams"] == [{"name": "recA/capture", "hash": plan.streams[0].meta_hash}]


def test_build_omits_optional_fields_when_absent(
    builder: CollectionBuilder, storage_client: StorageClient
) -> None:
    _seed_recording(storage_client, "gnss_l1/recA/capture.sigmf-data")

    plan = builder.build(prefix="gnss_l1/", name="x")

    coll = plan.collection_obj["collection"]
    assert "core:description" not in coll
    assert "core:author" not in coll
    assert coll["core:version"] == SIGMF_VERSION


def test_build_collection_key_at_prefix_root_with_slug(
    builder: CollectionBuilder, storage_client: StorageClient
) -> None:
    _seed_recording(storage_client, "gnss_l1/2026-06-17/recA/capture.sigmf-data")

    plan = builder.build(prefix="gnss_l1/2026-06-17/", name="June Campaign #1")

    assert plan.collection_key == "gnss_l1/2026-06-17/June-Campaign-1.sigmf-collection"


def test_build_normalizes_prefix_without_trailing_slash(
    builder: CollectionBuilder, storage_client: StorageClient
) -> None:
    """A prefix missing its trailing slash still lands the file at the root."""
    _seed_recording(storage_client, "gnss_l1/2026-06-17/recA/capture.sigmf-data")

    plan = builder.build(prefix="gnss_l1/2026-06-17", name="x")

    assert plan.prefix == "gnss_l1/2026-06-17/"
    assert plan.collection_key == "gnss_l1/2026-06-17/x.sigmf-collection"
    assert [s.name for s in plan.streams] == ["recA/capture"]


def test_build_empty_prefix_yields_no_streams(
    builder: CollectionBuilder, storage_client: StorageClient
) -> None:
    """A prefix with no Recordings is a normal, empty result (not an error)."""
    plan = builder.build(prefix="gnss_l1/2026-06-17/", name="x")

    assert plan.streams == []
    assert plan.orphans == []
    assert plan.collection_obj["collection"]["core:streams"] == []


# --- write ---------------------------------------------------------------


def test_write_uploads_collection_to_its_key(
    builder: CollectionBuilder, storage_client: StorageClient
) -> None:
    _seed_recording(storage_client, "gnss_l1/2026-06-17/recA/capture.sigmf-data")
    plan = builder.build(prefix="gnss_l1/2026-06-17/", name="june", author="schmitt")

    builder.write(plan)

    raw = storage_client.download_bytes(plan.collection_key)
    written = json.loads(raw.decode("utf-8"))
    assert written == plan.collection_obj
