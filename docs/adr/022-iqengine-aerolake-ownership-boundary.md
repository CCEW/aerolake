# ADR-022 — IQEngine and AeroLake ownership boundary

- **Status:** Accepted for AeroLake implementation; IQEngine contract agreement required
- **Date:** 2026-08-26
- **Author:** Camila Nino Francia
- **Relates to:** ADR-019 (record/playback division of labour), ADR-021 (IQEngine catalog integration), ADR-023 (catalog synchronization)
- **Supersedes:** N/A

## Context

The catalog integration spans two repositories. Without an explicit boundary,
both projects could implement storage, metadata indexing, synchronization, or
search behavior independently. That would create duplicate work and competing
sources of truth.

## Decision

Use the following ownership model:

| Responsibility | AeroLake | IQEngine |
|---|---|---|
| Produce and validate SigMF captures | Owns | Consumes |
| Publish `.sigmf-meta` and `.sigmf-data` to MinIO | Owns | Reads |
| MinIO object lifecycle and source-of-truth files | Owns | Does not own |
| IQEngine datasource configuration | Supplies required values | Owns |
| Catalog indexing and MongoDB persistence | Does not implement | Owns |
| Catalog search and metadata retrieval API | Consumes | Owns |
| Synchronization implementation and status | Triggers and observes | Owns |
| Reconciliation and stale-record handling | Requests/observes results | Owns catalog behavior |
| IQEngine MongoDB collections and credentials | Never accesses | Owns internally |
| Capture reading, HTTP Range, and replay | Owns | May expose retrieval API |

AeroLake must not write to IQEngine MongoDB or update catalog metadata through
IQEngine's metadata write API. Metadata changes are made in MinIO by AeroLake
and become visible in IQEngine after synchronization. IQEngine remains a
**derived, read-only catalog from AeroLake's perspective**.

AeroLake uses a dedicated service identity to call IQEngine. The integration
API must expose only the operations required for datasource lookup, read-only
search, metadata retrieval, and synchronization triggering/status. If existing
`/api/...` routes cannot provide a compatibility guarantee, IQEngine must add a
versioned integration wrapper.

## Shared contract

Before implementation, both repositories must agree on:

- MinIO endpoint, bucket, region, prefix, credentials, and least-privilege
  permissions;
- matching `.sigmf-meta` and `.sigmf-data` naming rules;
- required SigMF fields and validation behavior;
- API routes, authentication, permissions, response fields, and error codes;
- sync cooldown, freshness definition, timeout, retry, and concurrency rules;
- soft-deletion status and retention policy; and
- ownership of every field and operation.

The API contract is the boundary. Internal IQEngine MongoDB collections,
credential encryption, and implementation-specific models are not part of the
AeroLake contract.

## Rationale

This division avoids duplicate implementation while allowing each repository to
evolve independently. MinIO is the authoritative location for the files, IQEngine
is authoritative for catalog behavior, and AeroLake orchestrates publication and
consumes search results. API access preserves that boundary; direct database
access would make every IQEngine schema change an AeroLake change.

## Consequences

### Positive

- Each repository has a single owner for each responsibility.
- AeroLake can change its storage and ingestion implementation without owning
  IQEngine's database schema.
- IQEngine can change MongoDB internals without breaking AeroLake, provided the
  integration API remains compatible.
- The boundary gives supervisors and contributors a clear division of work.

### Negative / open

- The repositories require coordinated contract changes and integration tests.
- A service identity, permissions, TLS, and API compatibility policy must be
  operated across deployments.
- AeroLake's catalog experience depends on IQEngine availability; MinIO access
  remains the fallback when the catalog is unavailable.

## References

- ADR-019 — record/playback division of labour
- ADR-021 — reuse IQEngine's metadata catalog
- ADR-023 — synchronization, reconciliation, and reliability
