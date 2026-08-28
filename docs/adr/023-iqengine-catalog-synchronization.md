# ADR-023 — IQEngine catalog synchronization and reconciliation

- **Status:** Accepted for AeroLake orchestration; IQEngine sync/reconciliation contract required
- **Date:** 2026-08-26
- **Author:** Camila Nino Francia
- **Relates to:** ADR-003 (metadata and tags), ADR-021 (IQEngine catalog integration), ADR-022 (ownership boundary)
- **Supersedes:** N/A

## Context

AeroLake publishes recordings to MinIO while IQEngine maintains a derived
MongoDB catalog. IQEngine's current synchronization can discover and upsert
matching SigMF pairs, but the integration also needs freshness reporting,
repeatable operation, changed-object handling, and safe treatment of deleted or
incomplete recordings.

The current IQEngine sync endpoint queues a background synchronization and
returns without a job ID or completion status. It upserts discovered metadata
but does not remove catalog entries for objects deleted from MinIO. These are
known gaps to close in IQEngine before the synchronization policy below can be
considered production-ready.

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
4. A scheduled fallback runs every few hours even when there is no user traffic.
5. Sync start, completion, failure, duration, and object counts are recorded.

The exact interval is configuration, not an API contract. A sync must be safe to
repeat and safe when overlapping requests arrive.

### Reconciliation and deletion

The synchronization process must compare the expected MinIO recording set with
the catalog. A recording is active only when both matching `.sigmf-meta` and
`.sigmf-data` objects exist. Incomplete pairs are reported and are not normally
searchable.

The preferred deletion lifecycle is:

```text
active -> missing -> deleted
```

A missing object first marks the catalog record as `missing`; after an agreed
retention period, confirmed stale records may be marked `deleted` or removed by
a controlled administrative operation. Reconciliation is non-destructive by
default and must report missing catalog rows, missing MinIO objects, changed
metadata, and incomplete pairs.

For each recording, synchronization should retain or compare:

- metadata and data object keys;
- metadata and data ETags or object versions where available;
- last-seen timestamp;
- catalog status; and
- synchronization error details when indexing fails.

### Reliability and degraded behavior

IQEngine and MinIO calls must use bounded timeouts, retries with exponential
backoff for transient failures, structured logs, and metrics. Invalid SigMF
metadata must be rejected and reported for correction rather than silently
indexed. Authentication failures must fail safely and visibly.

If IQEngine is unavailable, AeroLake continues to operate against MinIO where
possible and marks catalog results stale or unavailable. It must not claim that
a catalog search is current when synchronization status is unknown.

## Acceptance criteria

The cross-repository integration is acceptable for the POC when it demonstrates:

- a new valid recording appears after synchronization;
- invalid metadata is rejected and reported;
- a missing data or metadata object is not active in the catalog;
- changed metadata is reflected after synchronization;
- deleted objects become stale or deleted according to the agreed policy;
- repeated and concurrent sync requests do not create unsafe duplicate work;
- freshness, counts, failures, and duration are observable;
- expired or invalid service credentials fail safely; and
- IQEngine downtime does not prevent direct MinIO access.

## Rationale

A scheduled and explicitly observable reconciliation path is appropriate for the
POC because it requires no MinIO event infrastructure and can recover from
missed updates or service downtime. Lazy refresh avoids making users wait for a
full object scan, while the scheduled fallback prevents the catalog from
remaining stale when there are no searches.

## Consequences

### Positive

- IQEngine remains the only catalog indexer.
- The catalog can recover from missed events, worker downtime, and changed or
  deleted MinIO objects.
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
