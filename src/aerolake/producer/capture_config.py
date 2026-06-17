"""Declarative capture configuration: the JSON schema for a capture.

A :class:`CaptureConfig` is the single source of truth describing *what to
capture* and *with which metadata*. It is meant to be loaded from a JSON file
(``aerolake-capture --config x.json``) and validated strictly before any
hardware is touched, so a malformed request fails fast at parse time rather
than after a long acquisition.

This module is intentionally self-contained: it knows how to validate a
config and how to translate the source block into the concrete params objects
the orchestrator already understands (:class:`SyntheticParams` /
:class:`SoapyParams`), but it does not import the orchestrator and does not
perform any I/O. The CLI wiring lives elsewhere (see the loader / CLI palier).

Design notes
------------
* Field placement follows the SigMF v1.2.6 specification. User-supplied
  descriptive fields live here; values the code computes (datatype, version,
  datetime, frequency, sha512, sample_count, extensions) are NOT in the
  config -- they are filled by the encoder/orchestrator at capture time.
* ``geolocation`` is captured as latitude/longitude/altitude for human
  readability, then converted to a standard RFC 7946 GeoJSON Point
  (``[lon, lat, alt]`` order) for the SigMF ``captures`` block.
* The antenna block exposes the scalar fields of the SigMF ``antenna:``
  extension. The 360-value gain-pattern arrays are out of scope for a
  hand-written JSON and are deliberately omitted.
* ``extra="forbid"`` everywhere: an unknown key (typically a typo in the JSON)
  is rejected instead of being silently ignored.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from aerolake.producer.soapy_source import SoapyParams
from aerolake.producer.synthetic import SyntheticParams

# SigMF bounds (core:sample_rate / core:frequency), from the spec.
_FREQ_MAX = 1_000_000_000_000.0  # 1e12 Hz
_FREQ_MIN = -1_000_000_000_000.0


class _StrictModel(BaseModel):
    """Base model rejecting unknown fields (catches JSON typos early)."""

    model_config = ConfigDict(extra="forbid")


class GeolocationConfig(_StrictModel):
    """A point location, validated and convertible to GeoJSON.

    Stored as separate lat/lon/alt for readability; emitted as an RFC 7946
    GeoJSON Point where coordinates are ``[longitude, latitude, altitude]``
    (note the lon-before-lat order the standard requires).
    """

    latitude: Annotated[float, Field(ge=-90.0, le=90.0)]
    longitude: Annotated[float, Field(ge=-180.0, le=180.0)]
    altitude: float | None = None

    def to_geojson(self) -> dict[str, object]:
        """Return a SigMF-ready ``core:geolocation`` GeoJSON Point Object."""
        coords: list[float] = [self.longitude, self.latitude]
        if self.altitude is not None:
            coords.append(self.altitude)
        return {"type": "Point", "coordinates": coords}


class LocationConfig(_StrictModel):
    """Where the capture happened, plus mobility and optional coordinates."""

    name: str
    mobile: bool = False
    geolocation: GeolocationConfig | None = None


class AnnotationConfig(_StrictModel):
    """A single annotation describing the whole capture.

    ``freq_lower_edge``/``freq_upper_edge`` are bound by the SigMF rule that
    both must be present or neither -- using just one is not allowed.
    """

    label: str | None = None
    comment: str | None = None
    freq_lower_edge: Annotated[float, Field(ge=_FREQ_MIN, le=_FREQ_MAX)] | None = None
    freq_upper_edge: Annotated[float, Field(ge=_FREQ_MIN, le=_FREQ_MAX)] | None = None

    @model_validator(mode="after")
    def _both_edges_or_neither(self) -> AnnotationConfig:
        has_lower = self.freq_lower_edge is not None
        has_upper = self.freq_upper_edge is not None
        if has_lower != has_upper:
            raise ValueError(
                "freq_lower_edge and freq_upper_edge must both be provided "
                "or both omitted (SigMF requires the pair, not one alone)."
            )
        if (
            has_lower
            and has_upper
            and self.freq_lower_edge is not None  # narrow for mypy
            and self.freq_upper_edge is not None
            and self.freq_lower_edge > self.freq_upper_edge
        ):
            raise ValueError("freq_lower_edge must not exceed freq_upper_edge.")
        return self


class AntennaConfig(_StrictModel):
    """Scalar fields of the SigMF ``antenna:`` extension.

    Every field is optional, but if an antenna block is supplied at all then
    ``model`` is required -- without it the extension carries no useful
    identity (and the spec marks ``antenna:model`` as the required field of
    the extension). Gain-pattern arrays are out of scope for manual JSON.
    """

    model: str
    type: str | None = None
    low_frequency: float | None = None
    high_frequency: float | None = None
    gain: float | None = None
    horizontal_beam_width: float | None = None
    vertical_beam_width: float | None = None
    cross_polar_discrimination: float | None = None
    voltage_standing_wave_ratio: float | None = None
    cable_loss: float | None = None
    steerable: bool | None = None
    mobile: bool | None = None
    hagl: float | None = None
    # Per SigMF these three belong in Annotations, not the Global antenna
    # block; the encoder is responsible for placing them in the annotation
    # that covers the whole capture.
    polarization: str | None = None
    azimuth_angle: float | None = None
    elevation_angle: float | None = None


class SyntheticSourceConfig(_StrictModel):
    """Source block selecting the synthetic generator."""

    type: Literal["synthetic"] = "synthetic"
    tone_offset_hz: float = 100_000.0
    snr_db: float = 20.0
    seed: int | None = None

    def to_params(self) -> SyntheticParams:
        return SyntheticParams(
            tone_offset_hz=self.tone_offset_hz,
            snr_db=self.snr_db,
            seed=self.seed,
        )


class SoapySourceConfig(_StrictModel):
    """Source block selecting a real SDR via SoapySDR."""

    type: Literal["soapy"]
    driver: str = "rtlsdr"
    agc: bool = True
    antenna: str | None = None

    def to_params(self) -> SoapyParams:
        return SoapyParams(driver=self.driver, agc=self.agc, antenna=self.antenna)


# Discriminated union: the ``type`` field selects the concrete source model,
# so Pydantic validates the right shape and gives clear errors.
SourceConfig = Annotated[
    SyntheticSourceConfig | SoapySourceConfig,
    Field(discriminator="type"),
]


class CaptureConfig(_StrictModel):
    """Top-level capture request loaded from a JSON file.

    Holds everything a user declares for one capture. Values the code derives
    at capture time (datatype, version, datetime, frequency, sha512,
    sample_count, extension declarations) are intentionally absent.
    """

    # --- What to capture --------------------------------------------------
    signal_type: str
    signal_type_detail: str | None = None
    center_freq: Annotated[float, Field(ge=_FREQ_MIN, le=_FREQ_MAX)]
    sample_rate: Annotated[float, Field(gt=0.0, le=_FREQ_MAX)]
    duration_s: Annotated[float, Field(gt=0.0)]

    # --- How (source) -----------------------------------------------------
    source: SourceConfig = Field(default_factory=SyntheticSourceConfig)

    # --- Descriptive metadata (SigMF core:, user-supplied) ----------------
    author: str | None = None
    description: str | None = None
    license: str | None = None
    operator: str | None = None

    # --- Where ------------------------------------------------------------
    location: LocationConfig | None = None

    # --- Optional annotation + antenna extension --------------------------
    annotation: AnnotationConfig | None = None
    antenna: AntennaConfig | None = None

    def source_params(self) -> SyntheticParams | SoapyParams:
        """Translate the source block into the orchestrator's params object."""
        return self.source.to_params()
