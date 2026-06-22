"""Tests for the rich-metadata encoding step (Palier 3a).

These assert that the optional descriptive fields the user can declare in a
capture config land in the right SigMF scope inside the produced .sigmf-meta:
- geolocation -> captures segment (the spec's preferred scope)
- author/description/license + antenna scalars -> Global object
- label/comment/freq edges + polarization/azimuth/elevation -> annotations
and that the antenna extension is declared in core:extensions.

We also confirm that omitting every rich field reproduces the previous output
(pure addition, no behavior change for existing callers).
"""

from __future__ import annotations

import hashlib
import json

from aerolake.producer.sigmf_writer import encode
from aerolake.producer.synthetic import generate_tone

GNSS_L1 = 1_575_420_000.0


def _signal():
    return generate_tone(
        duration_s=0.01,
        sample_rate=2_000_000.0,
        center_freq=GNSS_L1,
        seed=1,
    )


def _meta(capture) -> dict:
    return json.loads(capture.meta_bytes.decode("utf-8"))


def test_baseline_has_no_geolocation_no_annotations() -> None:
    # No rich fields supplied -> annotations empty, no geolocation key.
    meta = _meta(encode(_signal()))
    assert meta["annotations"] == []
    assert "core:geolocation" not in meta["captures"][0]
    # Only the aerolake extension is declared by default.
    names = {e["name"] for e in meta["global"]["core:extensions"]}
    assert names == {"aerolake"}


def test_datetime_uses_z_suffix() -> None:
    meta = _meta(encode(_signal()))
    assert meta["captures"][0]["core:datetime"].endswith("Z")


def test_integrity_and_structural_fields() -> None:
    # core:sha512 (data integrity), num_channels, offset — SigMF global fields.
    cap = encode(_signal())
    g = _meta(cap)["global"]
    assert g["core:num_channels"] == 1
    assert g["core:offset"] == 0
    assert g["core:sha512"] == hashlib.sha512(cap.data_bytes).hexdigest()


def test_geolocation_lands_in_captures_segment() -> None:
    geo = {"type": "Point", "coordinates": [-73.5623, 45.4946, 50.0]}
    meta = _meta(encode(_signal(), geolocation=geo))
    assert meta["captures"][0]["core:geolocation"] == geo


def test_author_description_license_in_global() -> None:
    meta = _meta(
        encode(
            _signal(),
            author="Theo Schmitt",
            description="Capture GPS L1 LASSENA rooftop",
            license="https://creativecommons.org/licenses/by-sa/4.0/",
        )
    )
    g = meta["global"]
    assert g["core:author"] == "Theo Schmitt"
    assert g["core:description"] == "Capture GPS L1 LASSENA rooftop"
    assert g["core:license"] == "https://creativecommons.org/licenses/by-sa/4.0/"


def test_user_description_overrides_signal_description() -> None:
    meta = _meta(encode(_signal(), description="custom text"))
    assert meta["global"]["core:description"] == "custom text"
    # Without a user description, the signal's own description is kept.
    meta2 = _meta(encode(_signal()))
    assert meta2["global"]["core:description"]  # non-empty fallback


def test_antenna_scalars_in_global_and_extension_declared() -> None:
    antenna = {
        "model": "Tallysman TW3742",
        "type": "active GNSS patch",
        "gain": 28.0,
        "cable_loss": 2.0,
        "hagl": 1.5,
    }
    meta = _meta(encode(_signal(), antenna=antenna))
    g = meta["global"]
    assert g["antenna:model"] == "Tallysman TW3742"
    assert g["antenna:gain"] == 28.0
    assert g["antenna:cable_loss"] == 2.0
    assert g["antenna:hagl"] == 1.5
    names = {e["name"] for e in g["core:extensions"]}
    assert "antenna" in names


def test_annotation_fields_land_in_annotations() -> None:
    annotation = {
        "label": "GPS L1 C/A",
        "comment": "active antenna, clear sky",
        "freq_lower_edge": GNSS_L1 - 1_000_000.0,
        "freq_upper_edge": GNSS_L1 + 1_000_000.0,
        "polarization": "right-hand circular",
        "azimuth_angle": 90.0,
        "elevation_angle": 45.0,
    }
    meta = _meta(encode(_signal(), annotation=annotation))
    assert len(meta["annotations"]) == 1
    ann = meta["annotations"][0]
    assert ann["core:sample_start"] == 0
    assert ann["core:sample_count"] > 0
    assert ann["core:label"] == "GPS L1 C/A"
    assert ann["core:comment"] == "active antenna, clear sky"
    assert ann["core:freq_lower_edge"] == GNSS_L1 - 1_000_000.0
    assert ann["core:freq_upper_edge"] == GNSS_L1 + 1_000_000.0
    # Antenna pointing belongs in annotations per the spec.
    assert ann["antenna:polarization"] == "right-hand circular"
    assert ann["antenna:azimuth_angle"] == 90.0
    assert ann["antenna:elevation_angle"] == 45.0


def test_antenna_extension_declared_even_if_only_in_annotation() -> None:
    # Pointing fields in the annotation are antenna:* keys, so the extension
    # must be declared even with no Global antenna block (else non-compliant).
    meta = _meta(
        encode(
            _signal(),
            annotation={"polarization": "right-hand circular", "azimuth_angle": 90.0},
        )
    )
    names = {e["name"] for e in meta["global"]["core:extensions"]}
    assert "antenna" in names


def test_full_rich_capture_is_valid_sigmf() -> None:
    # The whole point: a capture carrying every rich field still validates.
    geo = {"type": "Point", "coordinates": [-73.5623, 45.4946, 50.0]}
    capture = encode(
        _signal(),
        author="Theo",
        description="full",
        license="https://creativecommons.org/licenses/by-sa/4.0/",
        geolocation=geo,
        annotation={
            "label": "L1",
            "freq_lower_edge": GNSS_L1 - 1e6,
            "freq_upper_edge": GNSS_L1 + 1e6,
            "polarization": "right-hand circular",
        },
        antenna={"model": "Tallysman TW3742", "gain": 28.0},
    )
    # encode() runs SigMFFile.validate() internally; reaching here means the
    # structure is spec-compliant. Re-parse to be sure both blocks are present.
    meta = _meta(capture)
    assert meta["captures"][0]["core:geolocation"] == geo
    assert meta["annotations"][0]["core:label"] == "L1"
