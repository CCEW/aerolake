"""Load and validate a capture configuration from a JSON file.

This is the bridge between an on-disk ``.json`` request and the validated
:class:`~aerolake.producer.capture_config.CaptureConfig` model. It does one
job: read a file, parse it as JSON, validate it against the schema, and return
the model -- turning every possible failure into a single, clear
:class:`ConfigError` the CLI can print without a traceback.

Why a dedicated error type
--------------------------
A user pointing ``--config`` at the wrong file should get a one-line, readable
message ("file not found", "line 3: invalid JSON", "field X is required"), not
a Python stack trace. Wrapping the three distinct failure modes (missing file,
malformed JSON, schema violation) into ``ConfigError`` lets the CLI catch one
thing and exit cleanly.
"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import ValidationError

from aerolake.producer.capture_config import CaptureConfig


class ConfigError(Exception):
    """A capture-config file could not be read, parsed, or validated."""


def load_capture_config(path: str | Path) -> CaptureConfig:
    """Read ``path`` and return a validated :class:`CaptureConfig`.

    Parameters
    ----------
    path
        Filesystem path to a ``.json`` capture configuration.

    Returns
    -------
    CaptureConfig
        The validated configuration.

    Raises
    ------
    ConfigError
        If the file is missing, is not valid JSON, or does not satisfy the
        :class:`CaptureConfig` schema. The message is human-readable and safe
        to print directly to the user.
    """
    config_path = Path(path)

    try:
        raw = config_path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise ConfigError(f"Config file not found: {config_path}") from exc
    except OSError as exc:
        raise ConfigError(f"Could not read config file {config_path}: {exc}") from exc

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        # JSONDecodeError carries line/column; surface them, they're useful.
        raise ConfigError(
            f"Invalid JSON in {config_path} "
            f"(line {exc.lineno}, column {exc.colno}): {exc.msg}"
        ) from exc

    if not isinstance(data, dict):
        raise ConfigError(
            f"Config file {config_path} must contain a JSON object at the top "
            f"level, got {type(data).__name__}."
        )

    try:
        return CaptureConfig.model_validate(data)
    except ValidationError as exc:
        # Pydantic's error report is already structured and readable; prefix it
        # so the user knows which file is at fault.
        raise ConfigError(
            f"Config file {config_path} is not a valid capture request:\n{exc}"
        ) from exc
