# ADR-023 — IQEngine catalog synchronization and reconciliation

- **Status:** Accepted; local IQEngine sync/reconciliation implementation complete, deployment contract still required
- **Date:** 2026-08-26
- **Author:** Camila Nino Francia
- **Relates to:** ADR-003 (metadata and tags), ADR-021 (IQEngine catalog integration), ADR-022 (ownership boundary)
- **Supersedes:** N/A

## Context

AeroLake publishes recordings to MinIO while IQEngine maintains a derived
MongoDB catalog. The local implementation inventories both SigMF object types,
indexes valid complete pairs, detects changed metadata/data fingerprints, and
reconciles catalog rows that are absent from storage. It exposes a versioned
asynchronous job API with completion state, duration, object counts, and
structured errors.

This documents repository behavior, not a claim that an IQEngine deployment has
been verified against the AeroLake MinIO service. Service identity, deployed
credentials, API compatibility, timeout policy, and operational monitoring
remain deployment concerns.

## Decision

Use IQEngine synchronization as the catalog indexing mechanism. AeroLake owns
orchestrating calls and observing results; it must not implement a second catalog
synchronizer or access MongoDB directly.

### Synchronization policy

AeroLake should use lazy synchronization with a configured freshness interval,
initially proposed as three hours:

1. A search request checks the last successful sync time.
2. If the catalog is stale, the current result is returned with stale status and
   one asynchronous sync is triggered.
3. A single-flight or distributed lock prevents duplicate concurrent syncs.
4. No scheduled fallback is required for the current low-concurrency research
   catalogue. A user-triggered refresh is sufficient when current results are
   needed; a stale result must remain visibly marked until that refresh completes.
5. Sync start, completion, failure, duration, and object counts are recorded.

The exact interval is configuration, not an API contract. A sync must be safe to
repeat and safe when overlapping requests arrive. The current IQEngine backend
does not implement a scheduler; this is an AeroLake/deployment responsibility
if scheduled reconciliation is required. The implemented integration is
on-demand and reports freshness as `current`, `stale`, `sync in progress`,
`sync failed`, or `unavailable`.

### Reconciliation and deletion

The synchronization process compares the discovered object set with the
catalog. A recording is active only when both matching `.sigmf-meta` and
`.sigmf-data` objects exist. A metadata-only pair is reported as `missing_data`
and is not searchable. A data-only object is not indexed, but is not currently
reported as an incomplete pair; that reporting remains open.

The preferred deletion lifecycle is:

```text
active -> missing -> deleted
```

A missing object first marks the catalog record as `missing`; after the
configured retention period (seven days by default), it is marked `deleted`.
The implementation is non-destructive: it does not currently expose a separate
administrative hard-cleanup operation. It reports changed metadata, invalid
metadata, metadata-only incomplete pairs, and missing/deleted catalog rows.

For each recording, synchronization should retain or compare:

- metadata and data object keys;
- metadata and data ETags or object versions where available;
- last-seen timestamp;
- catalog status; and
- synchronization error details when indexing fails.

### Reliability and degraded behavior

The S3 inventory call has bounded retry with exponential backoff; terminal
inventory failures mark the job `failed` and record error details. Equivalent
retry/timeout behavior is not yet applied consistently to every storage path,
and deployed authentication behavior still needs verification. Invalid SigMF
metadata is rejected and reported rather than silently indexed.

If IQEngine is unavailable, AeroLake continues to operate against MinIO where
possible and marks catalog results stale or unavailable. It must not claim that
a catalog search is current when synchronization status is unknown.

## Acceptance criteria

The cross-repository integration is acceptable for the POC when it demonstrates:

- a new valid recording appears after synchronization;
- invalid metadata is rejected and reported;
- a metadata-only pair is reported and not active, while data-only orphan
  reporting remains an open requirement;
- changed metadata is reflected after synchronization;
- deleted objects become stale or deleted according to the agreed policy;
- repeated and concurrent sync requests do not create unsafe duplicate work;
- freshness, counts, failures, and duration are observable;
- expired or invalid service credentials fail safely; and
- IQEngine downtime does not prevent direct MinIO access.

## Rationale

An explicitly observable reconciliation path is appropriate for the POC because
it requires no MinIO event infrastructure and can recover when an operator
triggers a refresh. Lazy refresh avoids making users wait for a full object
scan. A scheduler remains necessary if the catalogue must refresh without user
traffic.

## Consequences

### Positive

- IQEngine remains the only catalog indexer.
- The catalog reconciles changed and absent objects whenever a sync runs.
- Users receive explicit stale-state information instead of misleading results.
- The POC has concrete, cross-repository acceptance tests.

### Negative / open

- Full reconciliation can be expensive as the MinIO object count grows.
- Soft deletion requires retention and administrative cleanup policy.
- Lazy synchronization adds eventual consistency and requires clear UI/CLI
  stale-state behavior.
- Event-driven synchronization may be added later, but it does not replace
  reconciliation.

## References

- ADR-003 — object metadata and tag convention
- ADR-021 — reuse IQEngine's metadata catalog
- ADR-022 — cross-repository ownership boundary and API contract
