"""Unit tests for the aerolake-list catalog CLI.

main() is invoked directly with an injected, moto-backed CaptureReader. We
seed captures with tiny placeholder bodies (the catalog never downloads the
sample data — it only reads tags + metadata via HEAD-class requests), so the
bytes are irrelevant here.
"""

from __future__ import annotations

import json

from aerolake.common.storage import StorageClient
from aerolake.consumer.reader import CaptureReader
from aerolake.scripts.catalog import main


def _seed(
    storage_client: StorageClient,
    data_key: str,
    *,
    signal_type: str = "gnss_l1",
    hardware: str = "synthetic",
    sample_rate: int = 2_000_000,
    center_freq: int = 1_575_420_000,
) -> None:
    """Seed a complete capture (data + meta) with tags and metadata.

    The bodies are placeholders: list/inspect only touch tags + metadata.
    """
    meta_key = data_key[: -len(".sigmf-data")] + ".sigmf-meta"
    storage_client.upload_bytes(meta_key, b"{}", content_type="application/json")
    storage_client.upload_bytes(
        data_key,
        b"x",
        metadata={
            "sample-rate": str(sample_rate),
            "center-freq": str(center_freq),
            "session-id": "abc12345",
        },
        tags={"signal-type": signal_type, "hardware": hardware},
    )


def _json_out(capsys) -> dict:
    return json.loads(capsys.readouterr().out.strip())


# --- Listing -------------------------------------------------------------


def test_list_all_returns_every_capture(storage_client, capsys) -> None:
    _seed(storage_client, "gnss_l1/A/capture.sigmf-data")
    _seed(storage_client, "iridium/B/capture.sigmf-data", signal_type="iridium")
    reader = CaptureReader(storage_client)

    exit_code = main(["--json"], reader=reader)

    assert exit_code == 0
    report = _json_out(capsys)
    assert report["total"] == 2
    keys = {c["data_key"] for c in report["captures"]}
    assert keys == {"gnss_l1/A/capture.sigmf-data", "iridium/B/capture.sigmf-data"}


def test_prefix_scopes_the_listing(storage_client, capsys) -> None:
    _seed(storage_client, "gnss_l1/A/capture.sigmf-data")
    _seed(storage_client, "iridium/B/capture.sigmf-data", signal_type="iridium")
    reader = CaptureReader(storage_client)

    exit_code = main(["--prefix", "gnss_l1/", "--json"], reader=reader)

    assert exit_code == 0
    report = _json_out(capsys)
    assert report["total"] == 1
    assert report["captures"][0]["data_key"] == "gnss_l1/A/capture.sigmf-data"


# --- Filtering -----------------------------------------------------------


def test_filter_by_hardware(storage_client, capsys) -> None:
    _seed(storage_client, "gnss_l1/ok/capture.sigmf-data", hardware="bladerf")
    _seed(storage_client, "gnss_l1/other/capture.sigmf-data", hardware="synthetic")
    reader = CaptureReader(storage_client)

    exit_code = main(["--hardware", "bladerf", "--json"], reader=reader)

    assert exit_code == 0
    report = _json_out(capsys)
    assert report["total"] == 1
    assert report["captures"][0]["data_key"] == "gnss_l1/ok/capture.sigmf-data"


def test_filters_combine_with_and(storage_client, capsys) -> None:
    # Matches both filters.
    _seed(storage_client, "match/capture.sigmf-data", signal_type="iridium", hardware="bladerf")
    # Right signal-type, wrong hardware.
    _seed(storage_client, "nomatch/capture.sigmf-data", signal_type="iridium", hardware="synthetic")
    reader = CaptureReader(storage_client)

    exit_code = main(
        ["--signal-type", "iridium", "--hardware", "bladerf", "--json"],
        reader=reader,
    )

    assert exit_code == 0
    report = _json_out(capsys)
    assert report["total"] == 1
    assert report["captures"][0]["data_key"] == "match/capture.sigmf-data"


def test_generic_tag_filter(storage_client, capsys) -> None:
    _seed(storage_client, "gnss_l1/A/capture.sigmf-data", hardware="rtlsdr")
    _seed(storage_client, "gnss_l1/B/capture.sigmf-data", hardware="synthetic")
    reader = CaptureReader(storage_client)

    exit_code = main(["--tag", "hardware=rtlsdr", "--json"], reader=reader)

    assert exit_code == 0
    report = _json_out(capsys)
    assert report["total"] == 1
    assert report["captures"][0]["data_key"] == "gnss_l1/A/capture.sigmf-data"


# --- Edge cases ----------------------------------------------------------


def test_no_matches_returns_zero(storage_client, capsys) -> None:
    _seed(storage_client, "gnss_l1/A/capture.sigmf-data")
    reader = CaptureReader(storage_client)

    exit_code = main(["--signal-type", "nonexistent"], reader=reader)

    assert exit_code == 0
    assert "no captures found" in capsys.readouterr().out.lower()


def test_malformed_tag_returns_two(storage_client, capsys) -> None:
    reader = CaptureReader(storage_client)

    exit_code = main(["--tag", "broken"], reader=reader)

    assert exit_code == 2
    assert "key=value" in capsys.readouterr().out.lower()
