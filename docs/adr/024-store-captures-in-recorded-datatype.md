# ADR-024 — Store captures in their recorded datatype (no ingest normalization)

- **Status:** Accepted
- **Date:** 2026-09-04
- **Author:** Camila Nino Francia
- **Relates to:** ADR-003 (key layout and metadata), ADR-009 (Range reads), ADR-010 (multipart upload), ADR-019 (GNU Radio owns the RF edges)

## Context

Until now, ingest converted every capture to **normalised `cf32_le`** on the
way into the lake. `ingest_files` converted `cu8`/`cs16`/`cs32` from raw dumps,
and `ingest_sigmf_pair` converted a declared `ci16_le` pair. The stated reason
was homogeneity: with one datatype in the bucket, consumers never branch, and
the (since removed, ADR-018) quality metrics could assume normalised cf32.

That reasoning no longer holds up against the cost:

- **Conversion doubles or quadruples the archive.** `ci16_le` → `cf32_le` is
  4 → 8 bytes per complex sample. A 10.5 GB Iridium flight capture became
  ~21 GB in MinIO. `cu8` → `cf32_le` is 2 → 8 bytes: four times the storage.
- **It adds no information.** int16 → float32 is lossless but not *informative*:
  the extra bytes encode precision the ADC never produced.
- **The quality layer that wanted normalised cf32 is gone** (ADR-018).
- **It breaks archival fidelity.** The stored object was no longer
  bit-identical to what the recorder wrote, so `core:sha512` described
  AeroLake's re-encoding rather than the original recording. An operator could
  not verify a stored capture against the file on their disk.
- **SigMF already solves this.** `core:datatype` exists precisely so a reader
  can interpret whatever format a recording is in. Normalising discards that
  and pretends the field has one legal value.

The homogeneity argument was really about *consumer convenience*, and that can
be paid once in the reader instead of forever in storage.

## Decision

**Captures are stored byte-for-byte in the datatype they were recorded in.**
Ingest never converts sample data.

1. `ingest_sigmf_pair` uploads the `.sigmf-data` unchanged and keeps the
   metadata's declared `core:datatype`. The stored object is bit-identical to
   the local file, so `core:sha512` is the hash of the operator's own file.
2. `ingest_files` uploads raw source bytes unchanged and declares the matching
   SigMF datatype (`cs16` → `ci16_le`, `cs32` → `ci32_le`, `cf32` → `cf32_le`,
   `cu8` → `cu8`), since the CLI's source-datatype names are not all spelled
   the way SigMF spells them.
3. **`CaptureReader` absorbs the complexity.** It decodes every datatype the
   lake accepts into normalised `complex64`, so `read()`, `read_segment()`,
   playback and streaming still hand callers uniform samples. Byte offsets for
   Range requests are computed from the stored datatype's width, not a fixed 8.

Consumers therefore see the same normalised complex64 they always did; only the
bytes at rest changed.

## Consequences

- A `ci16_le` capture occupies **half** the space it used to; `cu8` a quarter.
- Stored captures are verifiable against the operator's source file: the
  archived object and the local `.sigmf-data` have the same SHA-512.
- Ingest reads the source file once fewer, since the stored digest *is* the
  source digest — there is no second pass to hash converted bytes.
- The bucket now holds several datatypes. Anything reading `.sigmf-data` bytes
  directly (rather than through `CaptureReader`) **must** honour
  `core:datatype`. GNU Radio flowgraphs that assumed a bare complex File Source
  need the matching source type, or a converter block, for non-cf32 captures —
  this is the main cost of the change, and it lands on the GNU Radio side of
  the ADR-019 boundary.
- Captures ingested before this ADR remain `cf32_le` in the bucket and keep
  working; nothing is rewritten retroactively.
- Preview/thumbnail rendering still converts to float internally
  (`_iter_cf32_chunks`), which is a rendering detail, not a storage one.

## Alternatives considered

| Option | Why not |
|---|---|
| Keep normalising everything to `cf32_le` | The status quo: 2–4× storage for zero added information, and the archive is not bit-faithful to the recording. |
| Normalise only integer types below 16-bit | Arbitrary threshold, still lossy of fidelity, and still forces consumers to handle more than one datatype. |
| Store both the original and a cf32 rendition | Worst of both: more storage than today, and two objects to keep consistent. |
| Preserve bytes but leave the reader cf32-only | Consumers would break on every non-cf32 capture; the convenience has to be paid somewhere. |
