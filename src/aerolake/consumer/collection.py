"""Group several Recordings into a SigMF *Collection*.

A SigMF **Recording** is the (``.sigmf-data`` + ``.sigmf-meta``) pair AeroLake
already produces and stores in MinIO. A SigMF **Collection** (spec v1.2.x) is a
single ``.sigmf-collection`` JSON file that groups several *related* Recordings
— a "campaign" — by reference. The file holds a top-level ``collection`` object
with ``core:version``, optional ``core:description`` / ``core:author``, and an
ordered ``core:streams`` array. Each stream entry is a ``{name, hash}`` tuple:

- ``name`` — the Recording's base name (no extension), **relative to the
  directory the collection file lives in**. In AeroLake every Recording is
  literally named ``capture.sigmf-data`` inside its own folder, so a bare
  ``"capture"`` would be identical for all of them and impossible to resolve.
  We therefore use the path relative to the collection file, e.g.
  ``"2026-06-17_10h32m05_synthetic_a1b2c3d4/capture"``. That keeps the streams
  individually identifiable and round-trips through the spec's own resolution
  rule (``base_path / (name + ".sigmf-meta")`` lands on the right metadata file).
- ``hash`` — the SHA-512 (hex) of that Recording's ``.sigmf-meta`` bytes.

This module is the *pure logic* (no CLI): it lists Recordings under a prefix,
fetches each ``.sigmf-meta``, hashes it, and assembles the collection object.
:meth:`CollectionBuilder.build` only *plans* (nothing is written);
:meth:`CollectionBuilder.write` performs the single upload. The CLI
(``aerolake-collection``) is a thin wrapper over these two.

Selection is **by prefix**: every complete Recording under ``--prefix`` joins
the collection. Lone files (a ``.sigmf-data`` without its ``.sigmf-meta`` or
vice versa) are **orphans** — skipped, but reported. Non-SigMF objects
(``quality_report.json``, a pre-existing ``.sigmf-collection``) are ignored
entirely and never counted as orphans.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass

import structlog
from sigmf.sigmffile import SigMFCollection

from aerolake.common.storage import StorageClient
from aerolake.producer.sigmf_writer import SIGMF_VERSION

logger = structlog.get_logger(__name__)

# SigMF file-name suffixes. A "Recording base" is a key with the suffix removed,
# so ``foo/capture.sigmf-data`` and ``foo/capture.sigmf-meta`` share the base
# ``foo/capture``.
_DATA_EXT = ".sigmf-data"
_META_EXT = ".sigmf-meta"
_COLLECTION_EXT = ".sigmf-collection"

# The keys the spec allows inside the top-level ``collection`` object. We borrow
# the canonical list straight from the sigmf library so our validation stays in
# sync with whatever spec version is installed, rather than hard-coding it.
_VALID_COLLECTION_KEYS: frozenset[str] = frozenset(SigMFCollection.VALID_COLLECTION_KEYS)


@dataclass(frozen=True)
class RecordingRef:
    """One complete Recording, resolved into a collection stream entry.

    Attributes
    ----------
    data_key, meta_key
        Full bucket keys of the ``.sigmf-data`` / ``.sigmf-meta`` objects.
    name
        The SigMF stream name: the Recording's base, relative to the collection
        file's directory, with the extension stripped (e.g. ``<folder>/capture``).
    meta_hash
        SHA-512 (lower-case hex) of the ``.sigmf-meta`` bytes.
    """

    data_key: str
    meta_key: str
    name: str
    meta_hash: str


@dataclass(frozen=True)
class CollectionPlan:
    """What a collection *would* look like — assembled but not yet written.

    The output of :meth:`CollectionBuilder.build`. Holds the streams, the
    orphans found alongside them, the assembled top-level object ready to
    serialize, and the bucket key the ``.sigmf-collection`` would occupy.
    Nothing has been uploaded at this point.

    Attributes
    ----------
    prefix
        The normalized directory prefix that was scanned (always ends in ``/``,
        unless it is the empty whole-bucket prefix).
    collection_key
        Where the ``.sigmf-collection`` will be written (root of ``prefix``).
    streams
        The complete Recordings, sorted by key.
    orphans
        Keys of lone ``.sigmf-data`` / ``.sigmf-meta`` files (skipped).
    collection_obj
        The ``{"collection": {...}}`` dict, conformant and ready to serialize.
    """

    prefix: str
    collection_key: str
    streams: list[RecordingRef]
    orphans: list[str]
    collection_obj: dict


def _as_dir_prefix(prefix: str) -> str:
    """Normalize a prefix to a directory form: no leading ``/``, trailing ``/``.

    The empty prefix (whole bucket) is left as-is. ``gnss_l1/2026-06-17`` and
    ``/gnss_l1/2026-06-17/`` both become ``gnss_l1/2026-06-17/`` so the
    collection file lands *at the root of* the prefix the user named.
    """
    p = prefix.strip().lstrip("/")
    if p and not p.endswith("/"):
        p += "/"
    return p


def _slugify(name: str) -> str:
    """Turn a human collection name into a safe ``.sigmf-collection`` base name.

    Keeps ASCII letters/digits/dot/dash/underscore; collapses every other run
    of characters into a single dash; trims leading/trailing dashes. Falls back
    to ``"collection"`` if nothing usable remains (e.g. an all-symbols name).
    """
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", name.strip()).strip("-")
    return slug or "collection"


class CollectionBuilder:
    """Assemble SigMF Collections from Recordings stored in MinIO.

    Mirrors :class:`aerolake.consumer.reader.CaptureReader`: a thin, injectable
    wrapper over the single :class:`StorageClient` chokepoint, so it is fully
    exercisable against a moto-mocked backend in tests.
    """

    def __init__(self, storage_client: StorageClient | None = None) -> None:
        self._storage = storage_client or StorageClient()

    # --- Scanning ---------------------------------------------------------

    def scan(self, prefix: str) -> tuple[list[tuple[str, str]], list[str]]:
        """Pair up Recordings under ``prefix``.

        Returns ``(complete, orphans)`` where ``complete`` is a sorted list of
        ``(data_key, meta_key)`` for every base that has **both** files, and
        ``orphans`` is the sorted list of lone-file keys (a ``.sigmf-data``
        without its ``.sigmf-meta``, or the reverse). Any object that is neither
        a ``.sigmf-data`` nor a ``.sigmf-meta`` is ignored — it never appears in
        either list.
        """
        dir_prefix = _as_dir_prefix(prefix)
        keys = set(self._storage.list_objects(prefix=dir_prefix))

        # Reduce each SigMF file to its base (key without the extension). Using
        # sets lets us pair them with plain set algebra below.
        data_bases = {k[: -len(_DATA_EXT)] for k in keys if k.endswith(_DATA_EXT)}
        meta_bases = {k[: -len(_META_EXT)] for k in keys if k.endswith(_META_EXT)}

        # Complete = base present on both sides. Orphan = present on exactly one
        # side (symmetric difference), re-expanded to the file that *does* exist.
        complete = [
            (base + _DATA_EXT, base + _META_EXT) for base in sorted(data_bases & meta_bases)
        ]
        orphans = sorted(
            base + (_DATA_EXT if base in data_bases else _META_EXT)
            for base in (data_bases ^ meta_bases)
        )

        logger.info(
            "collection.scan",
            prefix=dir_prefix,
            complete=len(complete),
            orphans=len(orphans),
        )
        return complete, orphans

    # --- Planning ---------------------------------------------------------

    def build(
        self,
        *,
        prefix: str,
        name: str,
        description: str | None = None,
        author: str | None = None,
    ) -> CollectionPlan:
        """Scan ``prefix``, hash each Recording's metadata, assemble the plan.

        Downloads every complete Recording's ``.sigmf-meta`` (small JSON) to
        compute its SHA-512, builds the conformant ``collection`` object, and
        works out where the ``.sigmf-collection`` would be written. Nothing is
        uploaded — see :meth:`write` for that.
        """
        dir_prefix = _as_dir_prefix(prefix)
        complete, orphans = self.scan(prefix)

        streams: list[RecordingRef] = []
        for data_key, meta_key in complete:
            # Hash the metadata exactly as stored: the SHA-512 covers the raw
            # bytes of the .sigmf-meta object, nothing more.
            meta_bytes = self._storage.download_bytes(meta_key)
            meta_hash = hashlib.sha512(meta_bytes).hexdigest()

            # Stream name = recording base relative to the collection's dir, so
            # that dir_prefix + name + ".sigmf-meta" == meta_key (the spec's own
            # resolution rule). The startswith guard is belt-and-suspenders:
            # list_objects(prefix=dir_prefix) only returns keys under it.
            base = meta_key[: -len(_META_EXT)]
            rel_name = base[len(dir_prefix) :] if base.startswith(dir_prefix) else base

            streams.append(
                RecordingRef(
                    data_key=data_key,
                    meta_key=meta_key,
                    name=rel_name,
                    meta_hash=meta_hash,
                )
            )

        collection_obj = _assemble_collection(streams, description, author)
        collection_key = dir_prefix + _slugify(name) + _COLLECTION_EXT

        logger.info(
            "collection.build",
            collection_key=collection_key,
            streams=len(streams),
            orphans=len(orphans),
        )
        return CollectionPlan(
            prefix=dir_prefix,
            collection_key=collection_key,
            streams=streams,
            orphans=orphans,
            collection_obj=collection_obj,
        )

    # --- Writing ----------------------------------------------------------

    def write(self, plan: CollectionPlan) -> None:
        """Upload a planned collection's ``.sigmf-collection`` to MinIO.

        The single side-effecting step, kept separate from :meth:`build` so a
        caller (the CLI) can preview the plan — and skip writing an empty
        collection — before committing anything to the bucket.
        """
        body = json.dumps(plan.collection_obj, indent=2, sort_keys=True).encode("utf-8")
        self._storage.upload_bytes(
            plan.collection_key,
            body,
            content_type="application/json",
        )
        logger.info(
            "collection.write.ok",
            collection_key=plan.collection_key,
            streams=len(plan.streams),
        )


def _assemble_collection(
    streams: list[RecordingRef],
    description: str | None,
    author: str | None,
) -> dict:
    """Build the conformant ``{"collection": {...}}`` object.

    ``core:version`` is always present (the only required field) and reuses the
    producer's :data:`SIGMF_VERSION` for consistency. ``core:description`` /
    ``core:author`` are written only when supplied (absence is a real absence,
    matching the encoder's convention). The result is validated structurally —
    every key must be one the spec allows — so a mistake fails fast here rather
    than producing a non-compliant file in the bucket.
    """
    collection: dict[str, object] = {"core:version": SIGMF_VERSION}
    if description is not None:
        collection["core:description"] = description
    if author is not None:
        collection["core:author"] = author
    collection["core:streams"] = [{"name": s.name, "hash": s.meta_hash} for s in streams]

    unknown = set(collection) - _VALID_COLLECTION_KEYS
    if unknown:
        raise ValueError(
            f"Non-conformant SigMF collection keys: {sorted(unknown)}. "
            f"Allowed: {sorted(_VALID_COLLECTION_KEYS)}"
        )

    return {"collection": collection}
