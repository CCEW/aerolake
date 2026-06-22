# ADR-018 — Remove the quality / validation layer

- **Status:** Accepted
- **Date:** 2026-06-22
- **Author:** Théo Schmitt
- **Supersedes:** ADR-004 (prioritize data quality) and ADR-005 (quality tag promotion lifecycle)

## Context

Earlier ADRs built a **quality layer**: pure metric functions (`quality/metrics.py`
— clipping, RMS dBFS, invalid samples, DC offset, completeness, SigMF metadata
validity), a `QualityChecker`/`QualityReport` (`quality/checker.py`) applying
configurable thresholds, a `CaptureReader.validate()` that scored a capture,
wrote a `quality_report.json`, and promoted an S3 `quality` tag
(`raw → validated/rejected`), and the `aerolake-validate` CLI to run it over a
prefix (ADR-004, ADR-005).

The mission has since been clarified: AeroLake's job is a **clean, standard SigMF
data lakehouse** so the lab's acquisitions are well organized — and **the user
already chooses, at capture time, whether to keep an acquisition** (the
validate-before-upload confirmation in `aerolake-capture`). An automated
pass/fail quality verdict added no value to that goal: it introduced a whole
subsystem, a vestigial `quality` tag that nothing meaningfully consumed, and
extra surface to maintain.

## Decision

**Remove the quality/validation layer entirely.**

- **Deleted:** `src/aerolake/quality/` (metrics + checker), `CaptureReader.validate()`,
  the `aerolake-validate` CLI (`scripts/validate.py` + its entry point), and the
  matching tests (`tests/quality/`, `tests/scripts/test_validate.py`, the
  `validate` tests in `tests/consumer/test_reader.py`).
- **Dropped the `quality` tag:** the producer (`orchestrator.py`) and `ingest.py`
  no longer attach `quality=raw`, and `aerolake-list` loses its `--quality`
  filter and Quality column (it keeps `--signal-type`, `--hardware`, `--tag`).
- **Kept:** the **SigMF schema validation** in `sigmf_writer.encode()` and
  `ingest` (`SigMFFile(...).validate()`). This is a *different* "validation" — it
  guarantees every stored `.sigmf-meta` is spec-conformant, which is exactly what
  a "clean, standard" lakehouse needs. It stays.

## Rationale

- **Matches the real mission**: a tidy SigMF lakehouse, not a curation/QA tool.
- **Less surface, less to maintain**: removes a package, a CLI, a reader method,
  ~35 tests, and a tag that no longer had a lifecycle behind it.
- **No loss of the important guarantee**: conformance (schema validation) — the
  thing that keeps the lakehouse standard — is untouched.

## Consequences

### Positive

- Simpler, more focused codebase (26 source files vs 33).
- Tags carry only meaningful, producer-set facts (`signal-type`, `recorder`,
  `hardware`, `operator`, `mobile`, …) — no dead `quality` tag.

### Negative / open

- No built-in objective quality metrics anymore. If a future need arises to
  *measure* (not gate) capture quality, the pure metric functions can be
  recovered from git history (they were genuinely reusable, side-effect-free).
- ADR-003's tag list no longer includes `quality`.

## Alternatives considered

- **Keep the metrics, drop only the CLI/tag**: still carries an unused subsystem;
  rejected — the user wants it gone, and dead code is clutter.
- **Keep the `quality=raw` tag as an "as-recorded" marker**: a tag that is always
  the same value and never promoted is noise in a lakehouse meant to be tidy;
  removed.

## References

- Mission clarification (Théo, 2026-06): clean SigMF lakehouse; the user opts in
  to saving each acquisition, so a quality verdict is redundant.
- Supersedes ADR-004, ADR-005. Schema validation kept in
  `src/aerolake/producer/sigmf_writer.py` (`encode`) and `producer/ingest.py`.
