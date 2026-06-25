"""Load and validate a capture configuration from a TOML or JSON file.

This is the bridge between an on-disk capture request (``.toml`` or ``.json``)
and the validated :class:`~aerolake.producer.capture_config.CaptureConfig`
model. It does one job: read a file, parse it (TOML *or* JSON, chosen by file
extension), validate it against the schema, and return the model -- turning
every possible failure into a single, clear :class:`ConfigError` the CLI can
print without a traceback.

Why two formats
---------------
**TOML is the recommended format**: unlike JSON it allows *comments*, so a
config file can document each field inline (``# center frequency in Hz``) --
which matters when non-specialists at the lab read or edit a capture request.
JSON is still accepted for backward compatibility, so existing ``.json`` configs
keep working unchanged. The format is picked from the extension: ``.toml`` is
parsed with the standard-library :mod:`tomllib` (no extra dependency, Python
3.11+); anything else is parsed as JSON.

Why a dedicated error type
--------------------------
A user pointing ``--config`` at the wrong file should get a one-line, readable
message ("file not found", "line 3: invalid TOML", "field X is required"), not
a Python stack trace. Wrapping the distinct failure modes (missing file,
malformed TOML/JSON, schema violation) into ``ConfigError`` lets the CLI catch
one thing and exit cleanly.
"""

from __future__ import annotations

import json
import tomllib
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
        Filesystem path to a ``.toml`` (recommended) or ``.json`` capture
        configuration. The format is chosen from the file extension.

    Returns
    -------
    CaptureConfig
        The validated configuration.

    Raises
    ------
    ConfigError
        If the file is missing, is not valid TOML/JSON, or does not satisfy the
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

    # Pick the parser from the extension: ``.toml`` -> tomllib, everything else
    # (``.json`` and unknown suffixes) -> JSON. TOML's root is always a table,
    # so the "must be an object" check below only ever fires for JSON.
    if config_path.suffix.lower() == ".toml":
        try:
            data = tomllib.loads(raw)
        except tomllib.TOMLDecodeError as exc:
            # TOMLDecodeError's message already carries the line/column.
            raise ConfigError(f"Invalid TOML in {config_path}: {exc}") from exc
    else:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            # JSONDecodeError carries line/column; surface them, they're useful.
            raise ConfigError(
                f"Invalid JSON in {config_path} (line {exc.lineno}, column {exc.colno}): {exc.msg}"
            ) from exc

    if not isinstance(data, dict):
        raise ConfigError(
            f"Config file {config_path} must contain an object at the top "
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
