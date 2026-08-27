"""Tests for the AeroLake discovery metadata fields.

These cover the lab-specific ``aerolake:`` namespace added to SigMF metadata
(signal type, operator, location, mobile, derived duration) and the matching
S3 tags the orchestrator attaches for fast search.
"""

from __future__ import annotations

import json

from aerolake.common.storage import StorageClient
from aerolake.consumer.reader import CaptureReader
from aerolake.producer.orchestrator import capture_and_upload
from aerolake.producer.sigmf_writer import encode
from aerolake.producer.synthetic import generate_tone


def _tone():
    return generate_tone(
        duration_s=0.01,
        sample_rate=2_000_000,
        center_freq=1_575_420_000,
        seed=1,
    )


# ---------------------------------------------------------------------------
# Writer: the aerolake: namespace
# ---------------------------------------------------------------------------


def test_encode_writes_aerolake_fields() -> None:
    cap = encode(
        _tone(),
        author="Camila Nino Francia",
        signal_type="gnss_l1",
        operator="schmitt",
        location="roof",
        mobile=True,
    )
    g = json.loads(cap.meta_bytes)["global"]
    assert g["aerolake:signal_type"] == "gnss_l1"
    assert g["aerolake:operator"] == "Camila Nino Francia"
    assert g["aerolake:location"] == "roof"
    assert g["aerolake:mobile"] is True


def test_encode_derives_duration_from_samples() -> None:
    cap = encode(_tone(), signal_type="gnss_l1")
    g = json.loads(cap.meta_bytes)["global"]
    assert g["aerolake:sample_count"] == 20_000
    assert abs(g["aerolake:duration_s"] - 0.01) < 1e-9


def test_encode_omits_absent_optional_fields() -> None:
    cap = encode(_tone(), signal_type="iridium", operator="schmitt")
    g = json.loads(cap.meta_bytes)["global"]
    assert "aerolake:location" not in g
    assert "aerolake:signal_type_detail" not in g


def test_encode_keeps_other_detail_for_unknown_signal() -> None:
    cap = encode(
        _tone(),
        signal_type="other",
        signal_type_detail="unknown 1.2 GHz blip",
    )
    g = json.loads(cap.meta_bytes)["global"]
    assert g["aerolake:signal_type"] == "other"
    assert g["aerolake:signal_type_detail"] == "unknown 1.2 GHz blip"


def test_encode_still_valid_sigmf_with_namespace() -> None:
    cap = encode(_tone(), signal_type="gnss_l1", operator="schmitt")
    meta = json.loads(cap.meta_bytes)
    assert meta["global"]["core:datatype"] == "cf32_le"


# ---------------------------------------------------------------------------
# Orchestrator: operator defaulting + search tags
# ---------------------------------------------------------------------------


def test_capture_defaults_operator_to_author(storage_client: StorageClient, monkeypatch) -> None:
    monkeypatch.setattr("getpass.getuser", lambda: "alice")
    result = capture_and_upload(
        signal_type="gnss_l1",
        duration_s=0.01,
        sample_rate=2_000_000,
        center_freq=1_575_420_000,
        storage_client=storage_client,
    )
    reader = CaptureReader(storage_client)
    tags = reader.inspect(result.data_key).tags
    assert tags["operator"] == "AeroLake"


def test_capture_operator_matches_author(storage_client: StorageClient, monkeypatch) -> None:
    monkeypatch.setattr("getpass.getuser", lambda: "alice")
    result = capture_and_upload(
        signal_type="gnss_l1",
        duration_s=0.01,
        sample_rate=2_000_000,
        center_freq=1_575_420_000,
        operator="bob",
        storage_client=storage_client,
    )
    reader = CaptureReader(storage_client)
    tags = reader.inspect(result.data_key).tags
    assert tags["operator"] == "AeroLake"


def test_capture_writes_mobile_tag(
    storage_client: StorageClient,
) -> None:
    result = capture_and_upload(
        signal_type="gnss_l1",
        duration_s=0.01,
        sample_rate=2_000_000,
        center_freq=1_575_420_000,
        mobile=True,
        storage_client=storage_client,
    )
    reader = CaptureReader(storage_client)
    tags = reader.inspect(result.data_key).tags
    assert tags["mobile"] == "true"
    assert tags["signal-type"] == "gnss_l1"


def test_capture_metadata_reaches_sigmf(
    storage_client: StorageClient,
) -> None:
    result = capture_and_upload(
        signal_type="iridium",
        duration_s=0.01,
        sample_rate=2_000_000,
        center_freq=1_626_270_000,
        operator="schmitt",
        location="lab_a",
        mobile=False,
        storage_client=storage_client,
    )
    raw = storage_client.download_bytes(result.meta_key)
    g = json.loads(raw)["global"]
    assert g["aerolake:signal_type"] == "iridium"
    assert g["aerolake:operator"] == "AeroLake"
    assert g["aerolake:location"] == "lab_a"
    assert g["aerolake:mobile"] is False


# ---------------------------------------------------------------------------
# Palier 3: rich metadata end-to-end (orchestrator -> MinIO)
# ---------------------------------------------------------------------------


def test_rich_metadata_reaches_sigmf_and_tags(
    storage_client: StorageClient,
) -> None:
    from aerolake.producer.orchestrator import RichMetadata

    rich = RichMetadata(
        author="Theo Schmitt",
        description="Capture GPS L1 LASSENA rooftop",
        license="https://creativecommons.org/licenses/by-sa/4.0/",
        geolocation={"type": "Point", "coordinates": [-73.5623, 45.4946, 50.0]},
        annotation={
            "label": "GPS L1 C/A",
            "freq_lower_edge": 1_574_420_000.0,
            "freq_upper_edge": 1_576_420_000.0,
            "polarization": "right-hand circular",
        },
        antenna={"model": "Tallysman TW3742", "gain": 28.0},
    )
    result = capture_and_upload(
        signal_type="gnss_l1",
        duration_s=0.01,
        sample_rate=2_000_000,
        center_freq=1_575_420_000,
        operator="schmitt",
        location="LASSENA rooftop",
        mobile=False,
        rich=rich,
        storage_client=storage_client,
    )

    meta = json.loads(storage_client.download_bytes(result.meta_key))
    g = meta["global"]
    assert g["core:author"] == "Theo Schmitt"
    assert g["core:description"] == "Capture GPS L1 LASSENA rooftop"
    assert g["core:license"] == "https://creativecommons.org/licenses/by-sa/4.0/"
    assert g["antenna:model"] == "Tallysman TW3742"
    assert g["antenna:gain"] == 28.0
    # geolocation in the capture segment, annotation populated.
    assert meta["captures"][0]["core:geolocation"]["coordinates"] == [
        -73.5623,
        45.4946,
        50.0,
    ]
    ann = meta["annotations"][0]
    assert ann["core:label"] == "GPS L1 C/A"
    assert ann["antenna:polarization"] == "right-hand circular"
    # The two new searchable tags.
    reader = CaptureReader(storage_client)
    tags = reader.inspect(result.data_key).tags
    assert tags["location"] == "LASSENA rooftop"
    assert tags["antenna-model"] == "Tallysman TW3742"


def test_no_rich_metadata_keeps_capture_minimal(
    storage_client: StorageClient,
) -> None:
    # Without rich metadata, no antenna/geolocation tags or blocks appear.
    result = capture_and_upload(
        signal_type="gnss_l1",
        duration_s=0.01,
        sample_rate=2_000_000,
        center_freq=1_575_420_000,
        storage_client=storage_client,
    )
    meta = json.loads(storage_client.download_bytes(result.meta_key))
    assert meta["annotations"] == []
    assert "core:geolocation" not in meta["captures"][0]
    reader = CaptureReader(storage_client)
    tags = reader.inspect(result.data_key).tags
    assert "antenna-model" not in tags
