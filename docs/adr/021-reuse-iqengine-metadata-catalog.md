# ADR-021 — Reuse IQEngine's metadata catalog

- **Status:** Proposed — supervisor approval required
- **Date:** 2026-08-26
- **Author:** Camila Nino Francia
- **Relates to:** `docs/pitch-architecture.md`, ADR-001 (storage chokepoint), ADR-003 (metadata and tags), ADR-014 (SigMF Collections), ADR-019 (record/playback division of labour), ADR-020 (S3 portability), ADR-022 (ownership boundaries), ADR-023 (catalog synchronization)
- **Supersedes:** The standalone PostgreSQL/MongoDB catalog proposal previously recorded in ADR-021

## Context

AeroLake needs metadata queries over recordings stored in MinIO. The required
workflow includes filtering by signal type, date, hardware, sample rate, and
frequency, checking recording availability, and returning MinIO keys for
reading or replay. Large IQ files must remain in MinIO; SigMF remains the source
of truth for recording metadata.

IQEngine already provides a MongoDB-backed metadata catalog and supports
MinIO/S3-compatible datasources. It can discover matching `.sigmf-meta` and
`.sigmf-data` objects, validate metadata, calculate sample length from the data
object, and expose catalog/search operations through its API. Creating a second
catalog in AeroLake would duplicate indexing, synchronization, credentials,
backups, and stale-record handling.

The local deployment exposes the backend at `http://localhost:5000` and the
web UI at `http://localhost:3000`. Inside a shared Docker Compose network,
AeroLake must use the IQEngine service name and port, for example
`http://iqengine:5000`. IQEngine documents its API at `/api_docs`,
`/openapi.json`, and `/api/status`.

The plan therefore needs to distinguish between a valid target architecture and
capabilities that still require work in the IQEngine repository. Existing
unversioned endpoints, authentication, synchronization reporting, and deletion
handling must not be treated as a stable production contract without agreement
between the two repositories.

## Proposed architecture

```text
AeroLake producer / ingest
      |
      | validate and publish complete SigMF pairs
      v
MinIO: .sigmf-data + .sigmf-meta + tags
      |
      | IQEngine datasource and synchronization
      v
IQEngine integration API
      |
      +--> IQEngine MongoDB catalog (internal implementation)
      |
      +--> metadata search and recording retrieval
      |
      v
AeroLake consumes catalog results and reads MinIO objects
```

AeroLake owns publishing and object lifecycle. IQEngine owns datasource
configuration, indexing, and catalog search. MongoDB is an IQEngine internal
implementation detail. AeroLake must use the supported IQEngine API and must
never read or write IQEngine MongoDB collections directly.

IQEngine should register a datasource pointing to the AeroLake MinIO endpoint,
bucket, region, credentials, and optional prefix. IQEngine can then synchronize
objects uploaded directly by AeroLake; IQEngine does not need to own or upload
the files itself.

The supported integration operations are datasource lookup, datasource sync,
read-only search, and metadata retrieval. The sync endpoint currently queues a
background job and returns before completion without a job ID or status. Sync
status reporting therefore remains an IQEngine-side contract requirement. IQ
data retrieval is not routed through IQEngine for AeroLake's POC; AeroLake
continues to read the MinIO object directly using its existing reader paths.

## Comparison of catalog options

| Option | Benefits | Costs and risks | Fit for this plan |
|---|---|---|---|
| New PostgreSQL catalog | Native SQL, relational joins, typed constraints | New service, schema and migration work, duplicate indexing and synchronization | Rejected while IQEngine already provides the catalog |
| New MongoDB catalog | Flexible SigMF documents, fewer column migrations | Duplicate database, indexes, backups, synchronization, and query API | Rejected while IQEngine already provides MongoDB cataloging |
| Reuse IQEngine catalog | One index, existing MinIO/SigMF path, existing search API, less infrastructure | Cross-repository API dependency, shared contract and ownership work | Selected |

Reusing IQEngine does not mean that database maintenance disappears. IQEngine
still owns MongoDB operations, indexes, backups, upgrades, and catalog behavior.
AeroLake's workload is smaller because it does not operate a second catalog, but
it must handle API availability, authentication, synchronization freshness, and
degraded behavior.

## Rationale

Reusing IQEngine is the least duplicative architecture because IQEngine already
solves the database-side problem: indexing SigMF metadata stored in an
S3-compatible datasource and serving catalog queries. It also preserves the
correct storage boundary: MinIO serves large objects, while the catalog stores
metadata and references to object keys.

The integration must be API-based. Direct MongoDB access would couple AeroLake
to IQEngine's collections, internal schema, credential storage, and migration
choices. If the current API cannot provide a compatibility guarantee, IQEngine
should add a versioned integration wrapper rather than exposing MongoDB.
The current API expects a Microsoft JWT and uses datasource owners/readers/public
access controls. A dedicated AeroLake service identity, token flow, and scope
must be defined before production use.

## Decision

Reuse IQEngine's MongoDB-backed catalog through a documented, versioned
integration API. Do not add a PostgreSQL or MongoDB catalog to AeroLake.

The proof of concept will validate this path by publishing a valid SigMF pair
to MinIO, synchronizing it through the IQEngine datasource, querying it, and
using the returned MinIO keys for retrieval. It must also test invalid metadata,
missing data/meta pairs, changed metadata, deleted objects, repeated syncs, and
IQEngine unavailability.

The decision is conditional on the shared contract in ADR-022 and the
synchronization behavior in ADR-023 being agreed and implemented. Until then,
AeroLake's existing MinIO tag-based catalog remains available as a degraded
fallback; it is not a second authoritative database catalog.

## Consequences

### Positive

- No duplicate database or catalog service is added to AeroLake.
- IQEngine's existing MinIO/SigMF synchronization and MongoDB indexes are reused.
- MinIO remains the source of truth for `.sigmf-data` and `.sigmf-meta` objects.
- AeroLake can consume search results and continue using its existing readers,
  HTTP Range extraction, and replay paths.
- Database administration is concentrated in the IQEngine deployment.

### Negative / open

- AeroLake depends on IQEngine's availability, API compatibility, and
  authentication service.
- Both repositories must agree on object naming, metadata fields, response
  schemas, sync freshness, and deletion behavior.
- IQEngine's current synchronization must be extended or wrapped to report
  status and handle stale records safely; upsert-only behavior is insufficient.
- A catalog outage produces stale or unavailable search results, although MinIO
  access should continue.
- Production approval still depends on service identity provisioning, TLS,
  credential ownership, backups, monitoring, and deployment responsibilities.

## References

- `docs/pitch-architecture.md` — cataloging and the future query layer
- ADR-003 — object metadata and tag convention
- ADR-014 — SigMF Collections
- ADR-019 — record/playback division of labour
- ADR-020 — S3 portability requirements
- ADR-022 — cross-repository ownership boundaries and API contract
- ADR-023 — synchronization, reconciliation, and reliability
