# ADR-001 — Use boto3 over the MinIO SDK as our S3 client

- **Status:** Accepted
- **Date:** 2026-05-27
- **Author:** Théo Schmitt
- **Supersedes:** N/A

## Context

AeroLake stores SigMF captures in a MinIO bucket and reads them back from
the consumer. Every Python module that touches storage will go through a
single client class (`StorageClient` in `aerolake.common.storage`). The
question is which underlying library that class should wrap.

Three serious candidates exist in the Python ecosystem:

1. **boto3** — the official AWS SDK. Speaks the S3 protocol, works with
   any S3-compatible backend (AWS S3, MinIO, Ceph, Wasabi, etc.) via the
   `endpoint_url` parameter.

2. **minio-py** — the official MinIO Python SDK. MinIO-specific, with a
   slightly more ergonomic API for tags, lifecycle, and notifications.
   Used by Pierre Galopin and Lucien Millet in the NeSIVA legacy
   (`BitGrabber.ipynb`, August 2025).

3. **s3fs** — a filesystem-style abstraction over S3. Plays very well
   with PyArrow / Parquet (treats S3 as a path-like filesystem). Also
   used in the NeSIVA legacy alongside minio-py for Parquet streaming.

The decision is project-wide: every read and every write goes through
this choice, and changing later would mean rewriting `StorageClient`,
its tests, and any code already calling it.

## Decision

**We use boto3 as the underlying S3 client in `StorageClient`.**

`s3fs` may be added later as a complementary library if and when we
need PyArrow / Parquet streaming on the consumer side. It does not
replace boto3 — both can coexist.

## Rationale

- **Portability.** AeroLake will likely move from local MinIO (dev) to
  on-premises MinIO (LASSENA infrastructure) and possibly to AWS S3 or
  another provider in the future. boto3 talks to all of them without
  code changes — only the `endpoint_url` differs. With minio-py we
  would be locked to MinIO.
- **Ecosystem maturity.** boto3 is maintained by AWS, has the largest
  user base of any S3 client, and is the reference implementation that
  every other tool tests against. Bug fixes, security updates, and new
  features land there first.
- **Tooling support.** moto (used in our test suite) mocks boto3
  natively; testing minio-py requires either a real MinIO container
  during tests or a hand-rolled mock layer.
- **AWS feature parity.** boto3 exposes the full S3 API (multipart
  uploads with HTTP Range requests, Object Lock, presigned URLs,
  Select). The cadrage document already requires multipart uploads
  and HTTP Range Requests; both are boto3-first features.
- **Concept transferability.** boto3 patterns (Config, Session,
  Resource vs Client) are widely known across the Python community,
  making the project easier to hand over to future contributors.

## Consequences

### Positive

- Same client code works for local MinIO, on-prem MinIO, and AWS S3.
- Existing test mocks (moto) work out of the box.
- Tags and `x-amz-meta-*` metadata work identically across backends
  because they use the standard S3 protocol.
- Easy onboarding for any developer who has touched AWS before.

### Negative

- The boto3 API is more verbose than minio-py for some operations
  (e.g. setting object tags requires a separate `put_object_tagging`
  call after `put_object`).
- We must explicitly configure `signature_version="s3v4"` and
  `s3={"addressing_style": "path"}` for MinIO compatibility; on plain
  AWS these are defaults and the extra config is harmless but
  unnecessary.
- We diverge from the NeSIVA legacy convention. Any code we copy from
  Pierre or Lucien needs to be ported from minio-py / s3fs to boto3
  rather than reused verbatim.

### Neutral

- `s3fs` remains available as a future addition for analytics
  workflows. This ADR does not preclude introducing it later for
  Parquet / Iceberg integration (see future ADR on the analytics
  layer).

## Alternatives considered

### minio-py

Considered for legacy compatibility. Rejected because:
- Locks the project to MinIO; migration to AWS S3 or any other S3
  vendor would require a full client rewrite.
- Less testing ecosystem support; moto does not target it.
- Smaller community and fewer Stack Overflow answers.

### s3fs alone

Considered as a simpler, more Pythonic interface. Rejected as the
*sole* client because:
- Designed for read/write filesystem semantics, not for the full S3
  control plane (tags, lifecycle, multipart uploads at fine
  granularity).
- Performance and error handling abstractions are less explicit than
  boto3, which makes debugging harder in production.

We may still adopt s3fs *alongside* boto3 for Parquet streaming, in a
future ADR.

## References

- [boto3 documentation](https://boto3.amazonaws.com/v1/documentation/api/latest/index.html)
- [MinIO Python SDK](https://min.io/docs/minio/linux/developers/python/minio-py.html)
- [s3fs](https://s3fs.readthedocs.io/)
- LASSENA-Project_AeroLake.pdf (project mandate, May 2026)
- Internal: `src/aerolake/common/storage.py`
