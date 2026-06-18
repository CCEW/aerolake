# ADR-014 — Group related Recordings with SigMF Collections

- **Status:** Accepted
- **Date:** 2026-06-18
- **Author:** Théo Schmitt
- **Supersedes:** N/A

## Context

A capture campaign produces several related Recordings (a `.sigmf-data` +
`.sigmf-meta` pair each), all stored under a common prefix in MinIO — e.g. every
GNSS L1 capture taken on 2026-06-17 lands under `gnss_l1/2026-06-17/`. Nothing
ties those Recordings together as *one logical dataset*: a consumer can list
them, but the "these N captures form a campaign" relationship lives only in the
operator's head.

SigMF (v1.2.x, the spec our `sigmf` library implements) already standardizes
this: a **Collection** is a `.sigmf-collection` JSON file with a top-level
`collection` object holding `core:version`, optional `core:description` /
`core:author`, and an ordered `core:streams` array. Each stream is a
`{name, hash}` tuple — the Recording's base name plus the SHA-512 of its
`.sigmf-meta`. Adopting it makes the grouping a first-class, portable artifact
rather than tribal knowledge.

## Decision

**Add a `CollectionBuilder` (pure logic) + an `aerolake-collection` CLI that
group every complete Recording under a prefix into one `.sigmf-collection`.**

### Selection — by prefix, by arguments

- Recordings are selected **by prefix** (`--prefix gnss_l1/2026-06-17/`): every
  complete pair under it joins the collection. This matches the existing
  discovery model (`aerolake-list --prefix`, `aerolake-validate --prefix`) — no
  manifest file to maintain.
- The command takes **arguments, not a JSON config**:
  `aerolake-collection --prefix <p> --name <n> --description <d>`. The author is
  deduced from the system login (`getpass.getuser()`), like the producer's
  operator default.
- Only **complete** pairs are grouped. Lone files (a `.sigmf-data` without its
  `.sigmf-meta`, or the reverse) are **orphans**: skipped, but always reported
  so nothing silently disappears. Non-SigMF objects (`quality_report.json`, a
  pre-existing `.sigmf-collection`) are ignored entirely.

### Stream `name` — relative path, not a bare base name

Every AeroLake Recording is literally named `capture.sigmf-data` inside its own
folder (`{signal}/{date}/{session}/capture.*`, ADR-003). A bare SigMF base name
`"capture"` would therefore be **identical for every Recording** and impossible
to resolve. We instead use the Recording's base **relative to the collection
file's directory**, e.g. `2026-06-17_10h32m05_synthetic_a1b2c3d4/capture`. This
keeps streams individually identifiable and round-trips through the spec's own
resolution rule: `base_path / (name + ".sigmf-meta")` lands on the right file
(verified against `sigmf.SigMFCollection`, which re-hashes the resolved file).

### Layout & split

- The `.sigmf-collection` is written **at the root of the prefix**:
  `{prefix}/{slug(name)}.sigmf-collection`.
- `core:version` reuses the producer's `SIGMF_VERSION` (= `sigmf.__specification__`).
- The logic lives in `consumer/collection.py`, split `build()` (scan + hash +
  assemble, **no write**) / `write()` (the single upload) so the CLI can offer a
  `--dry-run` preview and skip writing an empty collection. Keys are validated
  against `SigMFCollection.VALID_COLLECTION_KEYS` so a malformed object fails
  fast — same "validate before storing" stance as the encoder (`sigmf_writer`).

## Rationale

- **Standard over bespoke**: a `.sigmf-collection` is portable to any SigMF tool;
  a custom manifest would not be.
- **Reuses the chokepoint**: `CollectionBuilder` wraps the single `StorageClient`,
  so it is fully testable against moto — no new I/O surface.
- **Consistent UX**: prefix selection, `--dry-run`, `--json`, exit codes 0/1/2,
  stderr logging — identical to `aerolake-list` / `aerolake-validate`.

## Consequences

### Positive

- Campaigns become a queryable, portable artifact stored next to the Recordings.
- Orphans are surfaced, not hidden — a curation signal for free.

### Negative / open

- We **do not** write the optional `core:collection` back-reference into each
  Recording's `.sigmf-meta` (would mutate existing objects; out of scope). A
  Collection points at its Recordings, not the reverse.
- A Collection is a **snapshot**: if a Recording's `.sigmf-meta` changes after
  the fact, its stored hash goes stale. Re-run the command to refresh.
- Selection is purely prefix-based; cherry-picking a subset would need a future
  flag (e.g. `--tag` filtering), deliberately deferred.

## Alternatives considered

- **A custom JSON manifest**: simpler to emit but non-standard and not
  interoperable — defeats the point of standardizing on SigMF.
- **Bare `"capture"` stream names** (strict base-name reading of the spec):
  ambiguous in our one-folder-per-capture layout; rejected for the relative path.
- **Building via `sigmf.SigMFCollection`**: it is disk-oriented (resolves and
  re-hashes metafiles from a `base_path`), but our metadata lives in MinIO. We
  assemble the dict ourselves and hash the bytes in-place; the lib is used only
  as an interoperability check.

## References

- SigMF spec v1.2.6 — Collection format (`collection`, `core:streams`)
- ADR-003 (key layout + metadata/tag convention), ADR-013 (mandate realignment)
- `src/aerolake/consumer/collection.py` (`CollectionBuilder`),
  `src/aerolake/scripts/collection.py` (`aerolake-collection`)
