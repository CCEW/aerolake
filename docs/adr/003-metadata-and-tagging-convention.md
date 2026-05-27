# ADR-003 — Object metadata and tagging convention for captures

- **Status:** Accepted
- **Date:** 2026-05-27
- **Author:** Théo Schmitt
- **Supersedes:** N/A

## Context

The AeroLake bucket needs to support two distinct retrieval patterns:

1. **Fast discovery** — A consumer wants to know "should I read this
   capture?" without downloading the .sigmf-data (which can be hundreds
   of MB) or even the .sigmf-meta JSON. Decision needs to be made on
   technical parameters: sample rate, center frequency, capture time,
   datatype, session id.

2. **Categorical filtering and lifecycle** — An operator (or an
   automated rule) wants to find "all GNSS L1 captures recorded on
   real hardware that are validated". Decision is based on enumerable
   attributes: signal type, recorder identity, hardware identity,
   quality level.

The S3 / MinIO API exposes two distinct mechanisms that map cleanly
onto these two patterns:

- **Object Metadata** (``x-amz-meta-*`` HTTP headers). Returned with
  ``head_object``, which is a cheap, body-less request. Ideal for fast
  per-object inspection. Limit: header size and ASCII-only values.
- **Object Tags**. Returned by ``get_object_tagging``. Indexable on
  the MinIO side, can drive lifecycle policies, and surface in MinIO
  search. Limit: 10 tags per object, keys 1-128 chars, values 0-256
  chars.

We need a convention that decides what goes where, so that consumers
and operators know exactly which API to call to answer their question.

## Decision

**We split discovery information across two channels with a clear
rule: continuous and technical values go into `x-amz-meta-*` headers,
categorical and enumerable values go into S3 tags.**

### x-amz-meta-* headers (attached to ``.sigmf-data`` only)

| Header                       | Type            | Example value          |
|------------------------------|-----------------|------------------------|
| ``x-amz-meta-sample-rate``   | int (Hz)        | ``2000000``            |
| ``x-amz-meta-center-freq``   | int (Hz)        | ``1575420000``         |
| ``x-amz-meta-datetime``      | ISO 8601 UTC    | ``2026-05-27T14:30...``|
| ``x-amz-meta-session-id``    | 8-hex string    | ``90e01e3e``           |
| ``x-amz-meta-datatype``      | SigMF datatype  | ``cf32_le``            |
| ``x-amz-meta-sample-count``  | int             | ``2000000``            |

### S3 tags (attached to ``.sigmf-data`` only)

| Tag             | Type            | Allowed values                              |
|-----------------|-----------------|---------------------------------------------|
| ``signal-type`` | enum / string   | ``gnss_l1``, ``iridium``, ``starlink``, or custom |
| ``recorder``    | string          | producer/script identifier                  |
| ``hardware``    | enum            | ``synthetic``, ``rtlsdr``, ``bladerf``, ``rfsoc`` |
| ``quality``     | enum            | ``raw`` (initial) / ``validated`` / ``archived`` |

### What about the ``.sigmf-meta`` JSON object ?

The ``.sigmf-meta`` is uploaded **without** metadata headers or tags.
Its body **is** the description — duplicating it as headers would be
redundant and create two sources of truth that could diverge.

Anyone reading the ``.sigmf-meta`` already has all the information
they need from the JSON itself.

## Rationale

### Why metadata for technical values

- Continuous numerical fields (sample rate in Hz, frequency in Hz,
  sample count) do not benefit from indexing — a consumer rarely
  filters "captures with exactly 2_000_000 samples". They want to
  check "does this capture have the sample rate my decoder expects ?".
- HEAD requests are essentially free (no body transfer). Perfect for
  per-object inspection.
- The fields are stable per capture and known at upload time.

### Why tags for categorical values

- Enumerable values benefit from indexing. "All ``signal-type=gnss_l1``
  captures" is the kind of query MinIO can resolve efficiently.
- Tags drive lifecycle policies natively. ``quality=raw`` objects
  can be auto-archived after 30 days; ``quality=validated`` retained
  indefinitely. Cannot do this with metadata headers.
- The number of distinct values per tag is small and known in advance,
  which is the typical strength of indexed enumerations.

### Why a "quality" lifecycle

Captures move through three phases:

- **raw**: just uploaded by the producer, not yet read by anyone.
  Eligible for automatic cold-storage / deletion if not promoted.
- **validated**: a consumer (human or automated) has confirmed the
  capture is usable — signal lock achieved, packets decoded, signal
  visible in spectrogram, etc. Retain indefinitely.
- **archived**: kept for long-term reference but not actively read.
  May be moved to a cheaper storage class in a future tier-aware
  setup.

Promotion is done via ``put_object_tagging`` from the consumer side;
it's a single API call that doesn't touch the data bytes.

## Consequences

### Positive

- **Cheap discovery.** A consumer can scan thousands of captures by
  paging ``list_objects`` and HEAD-ing each one. Bytes downloaded :
  zero.
- **Native lifecycle integration.** The MinIO admin can configure a
  rule "if tag.quality == raw and age > 30 days, transition to
  archive class" without any code change.
- **Clear separation of concerns.** Producer attaches initial
  metadata + tags. Consumer never modifies the data, but may update
  tags (quality promotion). The data bytes are immutable.

### Negative

- **Tag count limit (10 max).** We use 4 today, leaving 6 slots for
  future categorical attributes (e.g. ``location``, ``project``,
  ``operator``). If we exceed 10, we'll need to merge values or move
  some to metadata.
- **Two API calls to inspect an object fully.** A consumer that needs
  both metadata and tags must call ``head_object`` + ``get_object_tagging``.
  We mitigated this in ``StorageClient`` with helper methods, but it
  remains two round-trips.
- **No metadata on ``.sigmf-meta`` objects.** A consumer scanning
  only the meta files (e.g. for batch validation) does not see our
  technical metadata via HEAD. They must download the JSON. This is
  acceptable because the JSON is small (~600 bytes) and the consumer
  was going to read it anyway in that scenario.

### Neutral

- The convention can be extended without breaking changes : new
  metadata headers and new tags can be added incrementally, as long
  as existing consumers do not rely on absent values.

## Alternatives considered

### Put everything in tags

Rejected because :
- Tags are limited to 10 per object; technical fields alone (6 today)
  would crowd out future categorical attributes.
- Tag values must be strings 0-256 chars. ISO 8601 datetimes fit, but
  larger fields (sample count as a 12-digit number) waste tag space.
- Tags require a separate API call to read.

### Put everything in metadata headers

Rejected because :
- No native indexing. Filtering "all GNSS captures" would require
  paginating every object and inspecting each HEAD. Does not scale
  beyond a few thousand objects.
- No native lifecycle support based on metadata values. Lifecycle
  rules in MinIO work on tags and prefixes, not on arbitrary
  ``x-amz-meta-*`` values.

### Index in an external database

Considered for a future evolution (Apache Iceberg or a Postgres-backed
catalog). Rejected for now because :
- Adds operational complexity (another service to deploy, secure,
  back up).
- The current scale (hundreds to low thousands of captures) is well
  within reach of MinIO-native tagging.
- Iceberg is on the roadmap (mentioned in the project mandate as a
  future evolution); when it lands, we'll write a dedicated ADR for
  the analytics layer.

## Future work

- **ADR on consumer-side tag promotion**: precise rules for moving
  ``quality=raw`` → ``quality=validated``.
- **ADR on lifecycle policies**: TTL for ``raw``, transition rules.
  Requires input from the project lead on retention expectations.
- **Add ``location`` and ``project`` tags** once we have real hardware
  campaigns with named sites and named projects.

## References

- AWS S3 documentation on Object Metadata and Tagging
- LASSENA-Project_AeroLake.pdf (project mandate), section on
  Object Metadata & Tagging
- ``src/aerolake/common/storage.py`` (implementation)
- ``src/aerolake/producer/orchestrator.py`` (caller convention)
