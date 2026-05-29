# ADR-005 — Consumer-side quality tag promotion lifecycle

- **Status:** Accepted
- **Date:** 2026-05-29
- **Author:** Théo Schmitt
- **Supersedes:** N/A (completes ADR-003, applies ADR-004)

## Context

ADR-003 introduced the ``quality`` S3 tag and sketched a ``raw -> validated
-> archived`` lifecycle, but explicitly deferred the *promotion rules* to a
later ADR ("ADR on consumer-side tag promotion: precise rules for moving
``quality=raw`` → ``quality=validated``"). The producer code left the same
marker: every capture is uploaded with ``quality=raw`` and a comment pointing
at "ADR-005 when written".

ADR-004 then reprioritized the project toward a **curated dataset** and made
the quality layer the immediate focus. It extended the lifecycle informally
with a ``rejected`` state and shipped the implementation
(``CaptureReader.validate`` + the ``aerolake-validate`` CLI). What is still
missing is the *decision record* that pins down the rules this code follows:
who sets the tag, what the allowed transitions are, and how the tag is written
without clobbering the others.

This ADR closes that gap. It documents a decision already implemented; it does
not introduce new behaviour.

## Decision

**The ``quality`` tag is owned by the producer at creation and promoted by the
consumer after a measured quality assessment. The allowed states and
transitions are:**

```
              (producer, at upload)
                     │
                     ▼
                   raw
                     │  CaptureReader.validate() — measured verdict
        ┌────────────┴────────────┐
        ▼                         ▼
   validated                  rejected
        │
        ▼  manual (operator), when no longer actively read
   archived
```

- **raw** — set by the producer at upload time. Means "ingested, not yet
  assessed". This is the only state the producer ever writes.
- **validated** — set by ``validate()`` when the ``QualityReport`` verdict is
  ``is_valid is True`` (every threshold check passed).
- **rejected** — set by ``validate()`` when the verdict is
  ``is_valid is False`` (at least one check failed). This is the state added
  by ADR-004 so bad captures are explicitly marked rather than silently left
  as ``raw``.
- **archived** — set **manually** by an operator (per ADR-004: archival is
  manual, there is no automated lifecycle policy).

### Promotion mechanics (the read → merge → write rule)

The S3 ``PutObjectTagging`` API (``StorageClient.update_tags``) **replaces the
entire tag set** of an object. Therefore promotion is always:

1. read the current tags (``get_object_tags``),
2. merge the new ``quality`` value into a copy,
3. write the merged set back (``update_tags``).

Skipping the read/merge would wipe ``signal-type``, ``recorder`` and
``hardware``. ``CaptureReader.validate`` implements exactly this sequence.

### Evidence artifact

Every promotion (validated *or* rejected) writes a ``quality_report.json``
next to the capture (same session prefix), holding the raw metrics and the
list of failed checks. The verdict is thus auditable, not a bare tag.

### Dry run

``validate(..., store_report=False, promote_tag=False)`` (exposed as
``aerolake-validate --dry-run``) computes the verdict without touching the
bucket — no tag write, no report. Used to preview a curation pass.

## Rationale

- **Single writer per phase.** The producer only ever writes ``raw``; the
  consumer only ever moves ``raw`` forward. No two actors fight over the tag,
  and the data bytes are never mutated — only the tag and a sidecar report.
- **Measured, not guessed.** Promotion is driven by the ``QualityChecker``
  verdict against configurable thresholds, so ``validated`` means "passed
  objective checks", which is the whole point of a *curated* dataset (ADR-004).
- **Explicit rejection.** A distinct ``rejected`` state (vs. leaving bad
  captures as ``raw``) lets us tell apart "not yet assessed" from "assessed and
  failed" — essential when filtering the bucket down to a clean subset.
- **Re-runnable.** Because promotion reads-merges-writes and is idempotent on
  the verdict, re-validating a capture simply recomputes and overwrites the
  ``quality`` value; there is no illegal-transition bookkeeping to maintain.

## Consequences

### Positive

- The bucket can be filtered to a curated subset (``quality=validated``) with
  confidence that the label reflects a measured verdict backed by a report.
- ``raw`` acts as a clear work queue: "captures awaiting validation".
- No data mutation; promotion is a single cheap tagging call.

### Negative

- Re-validating with stricter thresholds can flip ``validated -> rejected``
  (or vice-versa). That is intended, but it means ``quality`` reflects the
  *last* run's thresholds, not a frozen historical verdict. The
  ``quality_report.json`` mitigates this by recording what was measured.
- ``archived`` has no enforcement: nothing prevents reading an archived
  capture, and the transition is a manual operator action.

### Neutral

- The transition graph is a convention enforced in code paths, not by S3
  itself — S3 will happily set ``quality`` to any string. The producer and
  ``validate()`` are the only writers, so the convention holds in practice.

## Alternatives considered

### Leave failed captures as ``raw``

Rejected: ``raw`` would then conflate "not assessed" with "assessed and bad",
making the work queue and the curated filter ambiguous. The explicit
``rejected`` state (ADR-004) is cheap and removes the ambiguity.

### Encode the verdict in object metadata instead of a tag

Rejected: per ADR-003, categorical/enumerable values belong in tags (indexable,
lifecycle-capable), not in ``x-amz-meta-*`` headers. ``quality`` is exactly
such an enumeration.

### Automated lifecycle (auto-archive raw after N days)

Rejected per ADR-004: the project lead stated archival is manual; automating it
would be speculative work against an explicit "not needed" signal.

## References

- ADR-003 (metadata and tagging convention) — defined the ``quality`` tag and
  deferred these promotion rules to this ADR
- ADR-004 (prioritize data quality over streaming) — added the ``rejected``
  state and shipped the implementation
- ``src/aerolake/consumer/reader.py`` (``CaptureReader.validate``)
- ``src/aerolake/scripts/validate.py`` (``aerolake-validate`` batch CLI)
- ``src/aerolake/producer/orchestrator.py`` (sets the initial ``quality=raw``)
