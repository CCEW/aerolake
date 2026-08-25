"""Tests for ingesting existing IQ files (producer.ingest + the CLI).

We write a small raw file to a tmp path, ingest it into moto-backed storage,
then read it back through the consumer to prove the round trip and the cf32
conversion.
"""

from __future__ import annotations

import hashlib
import json
import os
import re

import numpy as np
import pytest

import aerolake.producer.ingest as ingest_module
import aerolake.scripts.ingest as ingest_script
from aerolake.common.storage import StorageClient
from aerolake.consumer.reader import CaptureReader
from aerolake.producer.ingest import ingest_file, ingest_files, ingest_sigmf_pair
from aerolake.scripts.ingest import _resolve_files, main
from aerolake.scripts.iqengine_artifacts import generate_artifacts


def test_ingest_cf32_file_roundtrips(storage_client: StorageClient, tmp_path) -> None:
    # A cf32 file (what GNU Radio's File Sink writes).
    samples = (np.arange(1000) + 1j * np.arange(1000)).astype(np.complex64)
    path = tmp_path / "capture.sigmf-data"
    path.write_bytes(samples.tobytes())

    result = ingest_file(
        file_path=str(path),
        signal_type="gnss_l1",
        sample_rate=2_000_000,
        center_freq=1_575_420_000,
        hardware="bladerf",
        storage_client=storage_client,
    )

    assert result.sample_count == 1000
    # Read it back through the normal consumer path.
    reader = CaptureReader(storage_client)
    content = reader.read(result.data_key)
    np.testing.assert_array_equal(content.samples, samples)
    # Metadata + tags landed correctly.
    info = reader.inspect(result.data_key)
    assert info.tags["signal-type"] == "gnss_l1"
    assert info.tags["hardware"] == "bladerf"
    assert info.metadata["sample-rate"] == "2000000"
    g = content.sigmf_meta["global"]
    assert g["core:datatype"] == "cf32_le"
    assert g["core:num_channels"] == 1
    assert g["core:offset"] == 0
    assert g["core:sha512"] == hashlib.sha512(samples.tobytes()).hexdigest()


def test_ingest_cu8_is_converted_to_normalised_cf32(
    storage_client: StorageClient, tmp_path
) -> None:
    # RTL-SDR style: unsigned 8-bit interleaved I,Q. 255 -> ~+1, 0 -> ~-1.
    raw = np.array([255, 0, 127, 128], dtype=np.uint8)  # one I=+1,Q=-1 ; one ~0
    path = tmp_path / "dump.iq"
    path.write_bytes(raw.tobytes())

    result = ingest_file(
        file_path=str(path),
        signal_type="iridium",
        sample_rate=2_000_000,
        center_freq=1_626_000_000,
        datatype="cu8",
        hardware="rtlsdr",
        storage_client=storage_client,
    )

    assert result.sample_count == 2  # 4 uint8 = 2 complex samples
    content = CaptureReader(storage_client).read(result.data_key)
    assert content.samples.dtype == np.complex64
    # First sample: I=(255-127.5)/127.5=+1.0, Q=(0-127.5)/127.5=-1.0
    assert content.samples[0].real == 1.0
    assert content.samples[0].imag == -1.0
    # Second sample is near zero.
    assert abs(content.samples[1]) < 0.02


def test_ingest_ci16_le_is_converted_to_normalised_cf32(
    storage_client: StorageClient, tmp_path
) -> None:
    # SigMF ci16_le spelling: signed int16 little-endian interleaved I,Q.
    raw = np.array([32767, -32768, 0, 16384], dtype="<i2")  # 2 complex samples
    path = tmp_path / "capture.sigmf-data"
    path.write_bytes(raw.tobytes())

    result = ingest_file(
        file_path=str(path),
        signal_type="gnss_l1",
        sample_rate=2_000_000,
        center_freq=1_575_420_000,
        datatype="ci16_le",
        hardware="bladerf",
        storage_client=storage_client,
    )

    assert result.sample_count == 2
    content = CaptureReader(storage_client).read(result.data_key)
    assert content.samples.dtype == np.complex64
    assert content.samples[0].real == pytest.approx(32767 / 32768)
    assert content.samples[0].imag == pytest.approx(-1.0)
    assert content.samples[1].real == pytest.approx(0.0)
    assert content.samples[1].imag == pytest.approx(0.5)


def test_ingest_meta_uploaded_before_data_is_complete(
    storage_client: StorageClient, tmp_path
) -> None:
    """Both objects must exist and form a complete capture."""
    path = tmp_path / "capture.sigmf-data"
    path.write_bytes(np.zeros(16, dtype=np.complex64).tobytes())

    result = ingest_file(
        file_path=str(path),
        signal_type="gnss_l1",
        sample_rate=2_000_000,
        center_freq=1_575_420_000,
        storage_client=storage_client,
    )

    assert storage_client.object_exists(result.meta_key)
    assert storage_client.object_exists(result.data_key)
    # The meta is valid JSON with the expected fields.
    meta = json.loads(storage_client.download_bytes(result.meta_key))
    assert meta["captures"][0]["core:frequency"] == 1_575_420_000.0


def test_ingest_uses_human_readable_datetime_hardware_folder(
    storage_client: StorageClient, tmp_path
) -> None:
    path = tmp_path / "capture.sigmf-data"
    path.write_bytes(np.zeros(16, dtype=np.complex64).tobytes())

    result = ingest_file(
        file_path=str(path),
        signal_type="iridium",
        sample_rate=2_000_000,
        center_freq=1_622_000_000,
        hardware="blade rf",
        storage_client=storage_client,
    )

    pattern = (
        r"^iridium/\d{4}-\d{2}-\d{2}/"
        r"\d{4}-\d{2}-\d{2}_\d{2}h\d{2}m\d{2}_blade_rf_"
        rf"{result.session_id}/capture\.sigmf-data$"
    )
    assert re.match(pattern, result.data_key)


def test_ingest_generates_iqengine_artifacts_next_to_sigmf_pair(
    storage_client: StorageClient, tmp_path
) -> None:
    path = tmp_path / "capture.sigmf-data"
    path.write_bytes(np.exp(1j * np.linspace(0, 8 * np.pi, 4096)).astype(np.complex64).tobytes())

    result = ingest_file(
        file_path=str(path),
        signal_type="gnss_l1",
        sample_rate=2_000_000,
        center_freq=1_575_420_000,
        iqengine=True,
        storage_client=storage_client,
    )

    base_key = result.data_key[: -len(".sigmf-data")]
    assert result.sidecar_keys == (
        f"{base_key}.jpg",
        f"{base_key}.preview.jpg",
        f"{base_key}.minimap",
    )
    assert storage_client.download_bytes(result.sidecar_keys[0])[:2] == b"\xff\xd8"
    assert storage_client.download_bytes(result.sidecar_keys[1])[:2] == b"\xff\xd8"
    assert len(storage_client.download_bytes(result.sidecar_keys[2])) == 102_400
    assert storage_client.get_object_metadata(result.sidecar_keys[0])["role"] == (
        "iqengine-artifact"
    )
    assert path.with_suffix(".jpg").exists()
    assert path.with_suffix(".preview.jpg").exists()
    assert path.with_suffix(".minimap").exists()


def test_ingest_reuses_existing_iqengine_sidecars(
    storage_client: StorageClient, tmp_path
) -> None:
    path = tmp_path / "capture.sigmf-data"
    path.write_bytes(np.exp(1j * np.linspace(0, 8 * np.pi, 4096)).astype(np.complex64).tobytes())
    path.with_suffix(".jpg").write_bytes(b"existing-iqengine-jpg")
    path.with_suffix(".preview.jpg").write_bytes(b"existing-preview-jpg")
    path.with_suffix(".minimap").write_bytes(b"existing-minimap")

    result = ingest_file(
        file_path=str(path),
        signal_type="gnss_l1",
        sample_rate=2_000_000,
        center_freq=1_575_420_000,
        iqengine=True,
        storage_client=storage_client,
    )

    assert storage_client.download_bytes(result.sidecar_keys[0]) == b"existing-iqengine-jpg"
    assert storage_client.download_bytes(result.sidecar_keys[1]) == b"existing-preview-jpg"
    assert storage_client.download_bytes(result.sidecar_keys[2]) == b"existing-minimap"


def test_ingest_iqengine_redo_regenerates_existing_sidecars(
    storage_client: StorageClient, tmp_path
) -> None:
    path = tmp_path / "capture.sigmf-data"
    path.write_bytes(np.exp(1j * np.linspace(0, 8 * np.pi, 4096)).astype(np.complex64).tobytes())
    path.with_suffix(".jpg").write_bytes(b"stale-iqengine-jpg")
    path.with_suffix(".preview.jpg").write_bytes(b"stale-preview-jpg")
    path.with_suffix(".minimap").write_bytes(b"stale-minimap")

    result = ingest_file(
        file_path=str(path),
        signal_type="gnss_l1",
        sample_rate=2_000_000,
        center_freq=1_575_420_000,
        iqengine="redo",
        storage_client=storage_client,
    )

    assert storage_client.download_bytes(result.sidecar_keys[0])[:2] == b"\xff\xd8"
    assert storage_client.download_bytes(result.sidecar_keys[1])[:2] == b"\xff\xd8"
    assert len(storage_client.download_bytes(result.sidecar_keys[2])) == 102_400
    assert path.with_suffix(".jpg").read_bytes()[:2] == b"\xff\xd8"
    assert path.with_suffix(".preview.jpg").read_bytes()[:2] == b"\xff\xd8"
    assert path.with_suffix(".minimap").stat().st_size == 102_400


def test_ingest_iridium_annotation_is_preserved_after_hash_rewrite(
    storage_client: StorageClient, tmp_path, monkeypatch
) -> None:
    path = tmp_path / "capture.sigmf-data"
    samples = np.ones(100, dtype=np.complex64)
    path.write_bytes(samples.tobytes())

    def fake_annotate(**kwargs) -> bytes:
        meta = json.loads(kwargs["meta_bytes"])
        meta["annotations"] = [{"core:sample_start": 12, "core:label": "Iridium frame"}]
        return json.dumps(meta, indent=2, sort_keys=True).encode("utf-8")

    monkeypatch.setattr(ingest_module, "_apply_iridium_annotations", fake_annotate)

    result = ingest_files(
        file_paths=[str(path)],
        signal_type="iridium",
        sample_rate=10_000_000,
        center_freq=1_622_000_000,
        iridium_annotate=True,
        storage_client=storage_client,
    )

    uploaded = json.loads(storage_client.download_bytes(result.meta_key))
    assert uploaded["annotations"] == [{"core:sample_start": 12, "core:label": "Iridium frame"}]
    assert uploaded["global"]["core:sha512"] == hashlib.sha512(samples.tobytes()).hexdigest()


def test_iqengine_artifact_command_preserves_existing_jpeg_as_preview(tmp_path) -> None:
    base = tmp_path / "capture"
    samples = np.exp(1j * np.linspace(0, 8 * np.pi, 4096)).astype(np.complex64)
    base.with_suffix(".sigmf-data").write_bytes(samples.tobytes())
    base.with_suffix(".sigmf-meta").write_text(
        json.dumps(
            {
                "global": {
                    "core:datatype": "cf32",
                    "core:sample_rate": 2_000_000,
                },
                "captures": [{"core:sample_start": 0, "core:frequency": 1_575_420_000}],
                "annotations": [],
            }
        )
    )
    old_jpeg = b"old-aerolake-preview"
    base.with_suffix(".jpg").write_bytes(old_jpeg)

    jpg_path, preview_path, minimap_path = generate_artifacts(base)

    assert jpg_path.read_bytes()[:2] == b"\xff\xd8"
    assert preview_path.read_bytes() == old_jpeg
    assert minimap_path.stat().st_size == 102_400


def test_ingest_sigmf_pair_preserves_existing_meta(
    storage_client: StorageClient, tmp_path
) -> None:
    samples = np.arange(128, dtype=np.complex64)
    data_path = tmp_path / "capture.sigmf-data"
    meta_path = tmp_path / "capture.sigmf-meta"
    data_path.write_bytes(samples.tobytes())
    meta = {
        "global": {
            "core:datatype": "cf32_le",
            "core:sample_rate": 2_000_000,
            "core:hw": "PlutoSDR",
            "core:recorder": "SDRangel",
            "core:version": "1.2.3",
            "aerolake:signal_type": "nr5g",
        },
        "captures": [
            {
                "core:sample_start": 0,
                "core:frequency": 1_876_954_000,
                "core:datetime": "2023-07-20T11:39:48.680Z",
            }
        ],
        "annotations": [
            {
                "core:sample_start": 10,
                "core:sample_count": 20,
                "core:label": "PSS",
            }
        ],
    }
    meta_bytes = json.dumps(meta, indent=2).encode("utf-8")
    meta_path.write_bytes(meta_bytes)

    result = ingest_sigmf_pair(file_path=str(data_path), storage_client=storage_client)

    assert result.data_key.startswith("nr5g/2023-07-20/")
    uploaded_meta = json.loads(storage_client.download_bytes(result.meta_key))
    assert uploaded_meta["global"]["core:sha512"] == hashlib.sha512(
        samples.tobytes()
    ).hexdigest()
    assert storage_client.download_bytes(result.data_key) == samples.tobytes()
    info = CaptureReader(storage_client).inspect(result.data_key)
    assert info.tags["signal-type"] == "nr5g"
    assert info.tags["hardware"] == "PlutoSDR"
    assert info.tags["recorder"] == "SDRangel"
    assert info.metadata["datetime"] == "2023-07-20T11:39:48.680000+00:00"
    assert info.metadata["sample-count"] == "128"


def test_ingest_sigmf_pair_normalizes_ci16_le_and_updates_metadata(
    storage_client: StorageClient, tmp_path
) -> None:
    raw = np.array([32767, -32768, 0, 16384], dtype="<i2")
    data_path = tmp_path / "capture.sigmf-data"
    meta_path = tmp_path / "capture.sigmf-meta"
    data_path.write_bytes(raw.tobytes())
    meta_path.write_text(
        json.dumps(
            {
                "global": {
                    "core:datatype": "ci16_le",
                    "core:sample_rate": 2_000_000,
                    "aerolake:signal_type": "gnss_l1",
                },
                "captures": [{"core:sample_start": 0, "core:frequency": 1_575_420_000}],
                "annotations": [],
            }
        )
    )

    result = ingest_sigmf_pair(file_path=str(data_path), storage_client=storage_client)

    content = CaptureReader(storage_client).read(result.data_key)
    assert content.sigmf_meta["global"]["core:datatype"] == "cf32_le"
    np.testing.assert_allclose(
        content.samples,
        np.array([32767 / 32768 - 1j, 0 + 0.5j], dtype=np.complex64),
    )
    stored_data = storage_client.download_bytes(result.data_key)
    assert hashlib.sha512(stored_data).hexdigest() == content.sigmf_meta["global"]["core:sha512"]


def test_ingest_sigmf_pair_can_add_missing_sha512(
    storage_client: StorageClient, tmp_path
) -> None:
    samples = np.arange(128, dtype=np.complex64)
    data_path = tmp_path / "capture.sigmf-data"
    meta_path = tmp_path / "capture.sigmf-meta"
    data_path.write_bytes(samples.tobytes())
    meta = {
        "global": {
            "core:datatype": "cf32_le",
            "core:sample_rate": 2_000_000,
            "aerolake:signal_type": "nr5g",
        },
        "captures": [{"core:sample_start": 0, "core:frequency": 1_876_954_000}],
        "annotations": [],
    }
    original_meta_bytes = json.dumps(meta, indent=2).encode("utf-8")
    meta_path.write_bytes(original_meta_bytes)

    result = ingest_sigmf_pair(
        file_path=str(data_path),
        ensure_sha512=True,
        storage_client=storage_client,
    )

    uploaded_meta = json.loads(storage_client.download_bytes(result.meta_key))
    assert uploaded_meta["global"]["core:sha512"] == hashlib.sha512(
        samples.tobytes()
    ).hexdigest()
    assert meta_path.read_bytes() == original_meta_bytes


def test_ingest_sigmf_pair_requires_signal_type(
    storage_client: StorageClient, tmp_path
) -> None:
    data_path = tmp_path / "capture.sigmf-data"
    data_path.write_bytes(np.ones(128, dtype=np.complex64).tobytes())
    data_path.with_suffix(".sigmf-meta").write_text(
        json.dumps(
            {
                "global": {
                    "core:datatype": "cf32_le",
                    "core:sample_rate": 2_000_000,
                },
                "captures": [{"core:sample_start": 0, "core:frequency": 1_000_000}],
                "annotations": [],
            }
        )
    )

    with pytest.raises(ValueError, match="missing global.aerolake:signal_type"):
        ingest_sigmf_pair(file_path=str(data_path), storage_client=storage_client)


def test_ingest_sigmf_pair_rejects_mismatched_sha512(
    storage_client: StorageClient, tmp_path
) -> None:
    samples = np.arange(128, dtype=np.complex64)
    data_path = tmp_path / "capture.sigmf-data"
    meta_path = tmp_path / "capture.sigmf-meta"
    data_path.write_bytes(samples.tobytes())
    meta_path.write_text(
        json.dumps(
            {
                "global": {
                    "core:datatype": "cf32_le",
                    "core:sample_rate": 2_000_000,
                    "core:sha512": "0" * 128,
                    "aerolake:signal_type": "gnss_l1",
                },
                "captures": [{"core:sample_start": 0, "core:frequency": 1_876_954_000}],
                "annotations": [],
            }
        )
    )

    with pytest.raises(ValueError, match="core:sha512 does not match"):
        ingest_sigmf_pair(
            file_path=str(data_path),
            ensure_sha512=True,
            storage_client=storage_client,
        )


def test_ingest_sigmf_pair_generates_iqengine_sidecars(
    storage_client: StorageClient, tmp_path
) -> None:
    data_path = tmp_path / "capture.sigmf-data"
    meta_path = tmp_path / "capture.sigmf-meta"
    samples = np.exp(1j * np.linspace(0, 8 * np.pi, 4096)).astype(np.complex64)
    data_path.write_bytes(samples.tobytes())
    meta_path.write_text(
        json.dumps(
            {
                "global": {
                    "core:datatype": "cf32_le",
                    "core:sample_rate": 2_000_000,
                    "core:version": "1.2.3",
                    "aerolake:signal_type": "gnss_l1",
                },
                "captures": [{"core:sample_start": 0, "core:frequency": 1_575_420_000}],
                "annotations": [],
            }
        )
    )

    result = ingest_sigmf_pair(
        file_path=str(data_path),
        iqengine=True,
        storage_client=storage_client,
    )

    base_key = result.data_key[: -len(".sigmf-data")]
    assert result.sidecar_keys == (
        f"{base_key}.jpg",
        f"{base_key}.preview.jpg",
        f"{base_key}.minimap",
    )
    assert storage_client.download_bytes(result.sidecar_keys[0])[:2] == b"\xff\xd8"
    assert storage_client.download_bytes(result.sidecar_keys[1])[:2] == b"\xff\xd8"
    assert len(storage_client.download_bytes(result.sidecar_keys[2])) == 102_400
    assert data_path.with_suffix(".jpg").exists()
    assert data_path.with_suffix(".preview.jpg").exists()
    assert data_path.with_suffix(".minimap").exists()


# --- CLI -----------------------------------------------------------------


def test_ingest_cli_happy_path(storage_client: StorageClient, tmp_path, capsys) -> None:
    path = tmp_path / "capture.sigmf-data"
    path.write_bytes(np.zeros(100, dtype=np.complex64).tobytes())

    code = main(
        [
            str(path),
            "--signal-type",
            "gnss_l1",
            "--sample-rate",
            "2e6",
            "--center-freq",
            "1575.42e6",
            "--hardware",
            "bladerf",
        ],
        storage_client=storage_client,
    )

    assert code == 0
    assert "ingested" in capsys.readouterr().out.lower()


def test_ingest_cli_generates_iqengine_artifacts(
    storage_client: StorageClient, tmp_path, capsys
) -> None:
    path = tmp_path / "capture.sigmf-data"
    path.write_bytes(np.ones(4096, dtype=np.complex64).tobytes())

    code = main(
        [
            str(path),
            "--signal-type",
            "gnss_l1",
            "--sample-rate",
            "2e6",
            "--center-freq",
            "1575.42e6",
            "--iqengine",
        ],
        storage_client=storage_client,
    )

    assert code == 0
    out = capsys.readouterr().out
    assert "Sidecars" in out
    assert "Uploaded" in out


def test_ingest_cli_iqengine_creates_artifacts_without_local_sidecars(
    storage_client: StorageClient, tmp_path, capsys
) -> None:
    path = tmp_path / "capture.sigmf-data"
    path.write_bytes(np.ones(4096, dtype=np.complex64).tobytes())
    assert not (tmp_path / "capture.jpg").exists()
    assert not (tmp_path / "capture.minimap").exists()

    code = main(
        [
            str(path),
            "--signal-type",
            "gnss_l1",
            "--sample-rate",
            "2e6",
            "--center-freq",
            "1575.42e6",
            "--iqengine",
        ],
        storage_client=storage_client,
    )

    assert code == 0
    out = capsys.readouterr().out
    assert "Sidecars" in out
    assert "Uploaded" in out


def test_ingest_cli_iqengine_redo_is_accepted(
    storage_client: StorageClient, tmp_path, capsys
) -> None:
    path = tmp_path / "capture.sigmf-data"
    path.write_bytes(np.ones(4096, dtype=np.complex64).tobytes())

    code = main(
        [
            str(path),
            "--signal-type",
            "gnss_l1",
            "--sample-rate",
            "2e6",
            "--center-freq",
            "1575.42e6",
            "--iqengine",
            "redo",
        ],
        storage_client=storage_client,
    )

    assert code == 0
    out = capsys.readouterr().out
    assert "Sidecars" in out
    assert path.with_suffix(".jpg").exists()
    assert path.with_suffix(".preview.jpg").exists()
    assert path.with_suffix(".minimap").exists()


def test_ingest_cli_passes_iridium_annotation_options(
    storage_client: StorageClient, tmp_path, monkeypatch
) -> None:
    path = tmp_path / "capture.sigmf-data"
    path.write_bytes(np.ones(100, dtype=np.complex64).tobytes())
    captured: dict[str, object] = {}

    def fake_ingest_files(**kwargs):
        captured.update(kwargs)
        return ingest_module.IngestResult(
            session_id="abcd1234",
            data_key="iridium/2026-01-01/session/capture.sigmf-data",
            meta_key="iridium/2026-01-01/session/capture.sigmf-meta",
            sidecar_keys=(),
            sample_count=100,
            bytes_uploaded=800,
        )

    monkeypatch.setattr(ingest_script, "ingest_files", fake_ingest_files)

    code = main(
        [
            str(path),
            "--signal-type",
            "iridium",
            "--sample-rate",
            "10e6",
            "--center-freq",
            "1622e6",
            "--iridium-annotate",
            "--iridium-parser",
            str(tmp_path / "iridium-parser.py"),
            "--iridium-extractor",
            "custom-extractor",
            "--pypy",
            "custom-pypy",
        ],
        storage_client=storage_client,
    )

    assert code == 0
    assert captured["iridium_annotate"] is True
    assert captured["iridium_parser"] == str(tmp_path / "iridium-parser.py")
    assert captured["iridium_extractor"] == "custom-extractor"
    assert captured["pypy"] == "custom-pypy"


def test_ingest_cli_uses_existing_sigmf_meta_when_no_metadata_flags(
    storage_client: StorageClient, tmp_path, capsys
) -> None:
    path = tmp_path / "capture.sigmf-data"
    meta_path = tmp_path / "capture.sigmf-meta"
    path.write_bytes(np.ones(4096, dtype=np.complex64).tobytes())
    meta_path.write_text(
        json.dumps(
            {
                "global": {
                    "core:datatype": "cf32_le",
                    "core:sample_rate": 2_000_000,
                    "core:version": "1.2.3",
                    "aerolake:signal_type": "gnss_l1",
                },
                "captures": [{"core:sample_start": 0, "core:frequency": 1_575_420_000}],
                "annotations": [{"core:sample_start": 0, "core:label": "kept"}],
            }
        )
    )

    code = main([str(path), "--iqengine"], storage_client=storage_client)

    assert code == 0
    assert "existing SigMF pair" in capsys.readouterr().out
    data_key = next(
        key for key in storage_client.list_objects(prefix="gnss_l1/") if key.endswith(".sigmf-data")
    )
    meta_key = data_key[: -len(".sigmf-data")] + ".sigmf-meta"
    uploaded_meta = json.loads(storage_client.download_bytes(meta_key))
    assert uploaded_meta["annotations"][0]["core:label"] == "kept"


def test_ingest_cli_can_ensure_sha512_for_existing_sigmf_pair(
    storage_client: StorageClient, tmp_path
) -> None:
    path = tmp_path / "capture.sigmf-data"
    meta_path = tmp_path / "capture.sigmf-meta"
    samples = np.ones(256, dtype=np.complex64)
    path.write_bytes(samples.tobytes())
    meta_path.write_text(
        json.dumps(
            {
                "global": {
                    "core:datatype": "cf32_le",
                    "core:sample_rate": 2_000_000,
                    "aerolake:signal_type": "gnss_l1",
                },
                "captures": [{"core:sample_start": 0, "core:frequency": 1_575_420_000}],
                "annotations": [],
            }
        )
    )

    code = main([str(path), "--ensure-sha512"], storage_client=storage_client)

    assert code == 0
    data_key = next(
        key for key in storage_client.list_objects(prefix="gnss_l1/") if key.endswith(".sigmf-data")
    )
    meta_key = data_key[: -len(".sigmf-data")] + ".sigmf-meta"
    uploaded_meta = json.loads(storage_client.download_bytes(meta_key))
    assert uploaded_meta["global"]["core:sha512"] == hashlib.sha512(
        samples.tobytes()
    ).hexdigest()


def test_ingest_cli_rejects_ensure_sha512_with_generated_meta_mode(
    storage_client: StorageClient, tmp_path
) -> None:
    path = tmp_path / "capture.sigmf-data"
    path.write_bytes(np.ones(100, dtype=np.complex64).tobytes())

    code = main(
        [
            str(path),
            "--signal-type",
            "gnss_l1",
            "--sample-rate",
            "2e6",
            "--center-freq",
            "1575.42e6",
            "--ensure-sha512",
        ],
        storage_client=storage_client,
    )

    assert code == 2


def test_ingest_cli_partial_metadata_flags_return_two(
    storage_client: StorageClient, tmp_path
) -> None:
    path = tmp_path / "capture.sigmf-data"
    path.write_bytes(np.ones(100, dtype=np.complex64).tobytes())

    code = main([str(path), "--signal-type", "gnss_l1"], storage_client=storage_client)

    assert code == 2


def test_ingest_cli_missing_file_returns_two(storage_client: StorageClient) -> None:
    code = main(
        [
            "/nope/does-not-exist.iq",
            "--signal-type",
            "x",
            "--sample-rate",
            "2e6",
            "--center-freq",
            "1e9",
        ],
        storage_client=storage_client,
    )
    assert code == 2


# --- cs32 (RFSoC int32) + multi-file ingestion ---------------------------


def test_ingest_cs32_is_normalised(storage_client: StorageClient, tmp_path) -> None:
    # int32 interleaved I,Q -> normalised by 2**31.
    raw = np.array([2**30, -(2**31), 0, 2**31 - 1], dtype="<i4")  # 2 complex
    path = tmp_path / "rfsoc.bin"
    path.write_bytes(raw.tobytes())

    result = ingest_file(
        file_path=str(path),
        signal_type="iridium",
        sample_rate=400e3,
        center_freq=1626.271e6,
        datatype="cs32",
        hardware="rfsoc",
        storage_client=storage_client,
    )

    assert result.sample_count == 2
    content = CaptureReader(storage_client).read(result.data_key)
    assert content.samples[0].real == pytest.approx(0.5)  # 2**30 / 2**31
    assert content.samples[0].imag == pytest.approx(-1.0)  # -2**31 / 2**31


def test_ingest_files_concatenates_in_order(storage_client: StorageClient, tmp_path) -> None:
    a = np.arange(50, dtype=np.complex64)
    b = np.arange(50, 90, dtype=np.complex64)
    (tmp_path / "a.bin").write_bytes(a.tobytes())
    (tmp_path / "b.bin").write_bytes(b.tobytes())

    result = ingest_files(
        file_paths=[str(tmp_path / "a.bin"), str(tmp_path / "b.bin")],
        signal_type="iridium",
        sample_rate=400e3,
        center_freq=1626e6,
        storage_client=storage_client,
    )

    content = CaptureReader(storage_client).read(result.data_key)
    np.testing.assert_array_equal(content.samples, np.concatenate([a, b]))


def test_resolve_files_sorts_packets_numerically(tmp_path) -> None:
    for n in (1, 2, 10):  # lexical sort would wrongly put pkt_10 before pkt_2
        (tmp_path / f"pkt_{n}.bin").write_bytes(b"x" * 8)
    files = _resolve_files(str(tmp_path), "pkt_*.bin")
    assert [os.path.basename(f) for f in files] == ["pkt_1.bin", "pkt_2.bin", "pkt_10.bin"]


def test_ingest_cli_directory_of_packets(storage_client: StorageClient, tmp_path, capsys) -> None:
    for n in (1, 2, 3):
        (tmp_path / f"pkt_{n}.bin").write_bytes(np.zeros(10, dtype=np.complex64).tobytes())
    code = main(
        [
            str(tmp_path),
            "--glob",
            "pkt_*.bin",
            "--signal-type",
            "iridium",
            "--sample-rate",
            "400e3",
            "--center-freq",
            "1626.271e6",
            "--datatype",
            "cf32",
            "--hardware",
            "rfsoc",
        ],
        storage_client=storage_client,
    )
    assert code == 0
    assert "ingested" in capsys.readouterr().out.lower()
