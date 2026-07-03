# ADR-020 — MinIO community EOL: stay portable, SeaweedFS as the validated fallback

- **Status:** Accepted
- **Date:** 2026-07-03
- **Author:** Théo Schmitt
- **Relates to:** ADR-001 (boto3 + StorageClient chokepoint), ADR-003 (tags), ADR-009 (Range), ADR-010 (multipart)

## Context

MinIO, the S3-compatible store AeroLake uses (locally and on the lab's FAST
server), has effectively ended its free edition: the admin console was stripped
from the community build (Feb 2025), the commercial AIStor offering starts
around $96k/year, and the open-source repository was **archived in February
2026** — no more maintenance or security patches. "MinIO may become paid" is
therefore understated: the free MinIO is end-of-life.

AeroLake was designed for exactly this risk. ADR-001 put **all** S3 access
behind one chokepoint (`StorageClient`) with the endpoint injected via `.env`,
so the storage backend is a deployment detail — *provided* the replacement
supports the four S3 features the pipeline depends on:

1. **Object tagging** (ADR-003 discovery: `aerolake-list`, tag filters)
2. **HTTP Range reads** (ADR-009 partial/seeked reads)
3. **Multipart upload** (ADR-010 RAM-bounded streaming)
4. **`x-amz-meta-*` object metadata** (cheap HEAD-side technical values)

Candidates surveyed (2026-07):

| Store | License | Tagging | Notes |
|---|---|---|---|
| **SeaweedFS** | Apache-2.0 | ✅ | Active, lightweight, S3 gateway |
| OpenMaxIO (MinIO fork) | AGPL | ✅ | Drop-in by construction; young fork |
| Garage | AGPL | ❌ **missing** | Would break ADR-003 discovery as-is |
| Ceph RGW | LGPL | ✅ | Full-featured but heavy to operate |

## Decision

1. **Short term: stay on pinned MinIO** (local dev + FAST, which already runs
   it). It works; the storage choice on FAST belongs to the FAST admins anyway.
2. **Recommended replacement: SeaweedFS.** Portability was **proven
   empirically** on 2026-07-03: the real-server integration suite
   (`pytest -m integration` — upload+tagging, download, tag read, object size,
   Range read, 8 MiB multipart, delete) **passed unchanged** against a
   SeaweedFS container (`chrislusf/seaweedfs server -s3`, S3 gateway :8333),
   with only `AEROLAKE_S3_*` environment variables pointing at it.
3. **Any future store must support the four features above.** Garage is
   excluded until it implements object tagging (or discovery is reworked).

## Consequences

### Positive
- Migration cost is configuration, not code: `.env` endpoint/keys, the
  `docker/docker-compose.yml` image, and the CI integration-job image.
- The integration suite doubles as a **portability conformance test** for any
  candidate store — run it against a container before adopting.

### Negative / open
- Staying on archived MinIO accrues security-patch debt; revisit before any
  internet-exposed deployment.
- SeaweedFS anonymous-mode defaults differ from MinIO's key-based setup; a real
  deployment on the NAS must configure S3 credentials explicitly.
- The lab/FAST decision is outside this repo; this ADR records what AeroLake
  needs, not what FAST must run.

## References

- Integration suite: `tests/integration/test_minio_roundtrip.py`
- MinIO community archived: github.com/minio/minio (repo status, 2026-02)
- Garage S3 compatibility matrix (tagging endpoints missing):
  garagehq.deuxfleurs.fr/documentation/reference-manual/s3-compatibility/
- SeaweedFS S3 API: github.com/seaweedfs/seaweedfs/wiki/Amazon-S3-API
