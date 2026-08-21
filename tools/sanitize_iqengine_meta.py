"""Sanitize a SigMF metadata file for IQEngine's annotation renderer."""

from __future__ import annotations

import json
import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: sanitize_iqengine_meta.py <capture.sigmf-meta>")
        return 2

    path = Path(sys.argv[1])
    meta = json.loads(path.read_text())
    backup = path.with_suffix(path.suffix + ".iqengine-bak")
    backup.write_text(json.dumps(meta, indent=2) + "\n")

    required = [
        "core:sample_start",
        "core:sample_count",
        "core:freq_lower_edge",
        "core:freq_upper_edge",
    ]
    clean = []
    dropped = 0
    labels_added = 0
    for index, annotation in enumerate(meta.get("annotations", [])):
        if not isinstance(annotation, dict) or any(
            key not in annotation or annotation[key] in (None, "") for key in required
        ):
            dropped += 1
            continue
        if not annotation.get("core:label"):
            label = (
                annotation.get("core:comment")
                or annotation.get("core:description")
                or f"Annotation {index + 1}"
            )
            annotation["core:label"] = str(label).split(":", 1)[0][:80]
            labels_added += 1
        clean.append(annotation)

    meta["annotations"] = clean
    path.write_text(json.dumps(meta, indent=2, sort_keys=True) + "\n")

    print(f"backup={backup}")
    print(f"kept={len(clean)} dropped={dropped}")
    print(f"labels_added={labels_added}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
