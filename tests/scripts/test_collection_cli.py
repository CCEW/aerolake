"""Unit tests for the aerolake-collection CLI (Palier B).

main() is invoked directly with an injected, moto-backed CollectionBuilder.
We seed Recordings with placeholder data bodies (the CLI only hashes the
.sigmf-meta), then assert on the printed summary / JSON and on what actually
landed in the bucket.
"""

from __future__ import annotations

import json

from aerolake.common.storage import StorageClient
from aerolake.consumer.collection import CollectionBuilder
from aerolake.scripts.collection import main


def _seed_recording(storage_client: StorageClient, data_key: str) -> None:
    """Seed a complete Recording (data + meta) with placeholder bodies."""
    meta_key = data_key[: -len(".sigmf-data")] + ".sigmf-meta"
    storage_client.upload_bytes(meta_key, b'{"global": {}}', content_type="application/json")
    storage_client.upload_bytes(
        data_key, b"\x00\x00\x00\x00", content_type="application/octet-stream"
    )


def _json_out(capsys) -> dict:
    return json.loads(capsys.readouterr().out.strip())


# --- Happy path ----------------------------------------------------------


def test_creates_collection_and_writes_it(storage_client, capsys) -> None:
    _seed_recording(storage_client, "gnss_l1/2026-06-17/recA/capture.sigmf-data")
    _seed_recording(storage_client, "gnss_l1/2026-06-17/recB/capture.sigmf-data")
    builder = CollectionBuilder(storage_client)

    exit_code = main(
        [
            "--prefix",
            "gnss_l1/2026-06-17/",
            "--name",
            "june",
            "--description",
            "June campaign",
            "--author",
            "schmitt",
            "--json",
        ],
        builder=builder,
    )

    assert exit_code == 0
    report = _json_out(capsys)
    assert report["written"] is True
    assert report["n_streams"] == 2
    assert report["streams"] == ["recA/capture", "recB/capture"]

    # The .sigmf-collection actually landed at the prefix root.
    key = "gnss_l1/2026-06-17/june.sigmf-collection"
    assert report["collection_key"] == key
    written = json.loads(storage_client.download_bytes(key).decode("utf-8"))
    coll = written["collection"]
    assert coll["core:description"] == "June campaign"
    assert coll["core:author"] == "schmitt"
    assert [s["name"] for s in coll["core:streams"]] == [
        "recA/capture",
        "recB/capture",
    ]


def test_author_defaults_to_system_login(storage_client, capsys, monkeypatch) -> None:
    monkeypatch.setattr("aerolake.scripts.collection.getpass.getuser", lambda: "loginuser")
    _seed_recording(storage_client, "gnss_l1/recA/capture.sigmf-data")
    builder = CollectionBuilder(storage_client)

    exit_code = main(["--prefix", "gnss_l1/", "--name", "x", "--json"], builder=builder)

    assert exit_code == 0
    key = _json_out(capsys)["collection_key"]
    coll = json.loads(storage_client.download_bytes(key).decode("utf-8"))["collection"]
    assert coll["core:author"] == "loginuser"


# --- Orphans & empties ---------------------------------------------------


def test_orphans_are_reported_but_excluded(storage_client, capsys) -> None:
    _seed_recording(storage_client, "gnss_l1/2026-06-17/recA/capture.sigmf-data")
    storage_client.upload_bytes("gnss_l1/2026-06-17/orphan/capture.sigmf-data", b"x")
    builder = CollectionBuilder(storage_client)

    exit_code = main(
        ["--prefix", "gnss_l1/2026-06-17/", "--name", "x", "--json"],
        builder=builder,
    )

    assert exit_code == 0
    report = _json_out(capsys)
    assert report["n_streams"] == 1
    assert report["orphans"] == ["gnss_l1/2026-06-17/orphan/capture.sigmf-data"]


def test_no_recordings_writes_nothing(storage_client, capsys) -> None:
    builder = CollectionBuilder(storage_client)

    exit_code = main(["--prefix", "empty/", "--name", "x", "--json"], builder=builder)

    assert exit_code == 0
    report = _json_out(capsys)
    assert report["written"] is False
    assert report["n_streams"] == 0
    # Nothing was written to the bucket.
    assert list(storage_client.list_objects(prefix="empty/")) == []


# --- Dry run -------------------------------------------------------------


def test_dry_run_plans_without_writing(storage_client, capsys) -> None:
    _seed_recording(storage_client, "gnss_l1/2026-06-17/recA/capture.sigmf-data")
    builder = CollectionBuilder(storage_client)

    exit_code = main(
        ["--prefix", "gnss_l1/2026-06-17/", "--name", "x", "--dry-run", "--json"],
        builder=builder,
    )

    assert exit_code == 0
    report = _json_out(capsys)
    assert report["written"] is False
    assert report["n_streams"] == 1
    # The .sigmf-collection must NOT exist after a dry run.
    assert not storage_client.object_exists(report["collection_key"])
