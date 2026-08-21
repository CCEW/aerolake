from __future__ import annotations

import numpy as np

from aerolake.scripts.preview_diagnostic import main

_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def test_preview_diagnostic_cli_writes_aerolake_preview_png(tmp_path, capsys) -> None:
    sample_rate = 1_000_000
    center_freq = 100_000_000
    t = np.arange(16_384, dtype=np.float32) / sample_rate
    samples = np.exp(2j * np.pi * 125_000 * t).astype(np.complex64)
    path = tmp_path / "tone.sigmf-data"
    path.write_bytes(samples.tobytes())

    code = main(
        [
            str(path),
            "--sample-rate",
            str(sample_rate),
            "--center-freq",
            str(center_freq),
            "--out-dir",
            str(tmp_path),
        ]
    )

    assert code == 0
    assert "preview diagnostics generated" in capsys.readouterr().out.lower()
    assert (tmp_path / "tone.aerolake-preview.png").read_bytes()[:8] == _PNG_MAGIC
