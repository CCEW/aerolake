"""Central logging configuration for AeroLake.

Why this module exists
----------------------
structlog's *default* (unconfigured) renderer writes its log lines to
**stdout**. For a library that's harmless, but for our CLIs it is not: the
log lines interleave with the program's real output. Concretely, that breaks
two things:

  - ``--json`` output: a consumer doing ``aerolake-validate --json | jq`` gets
    log lines mixed into the JSON stream, which is no longer parseable.
  - human-readable tables: the rich summary table gets muddied by log lines
    printed in the middle of it.

The fix is a one-liner with a clear contract: **logs go to stderr, the
program's result goes to stdout.** This is the standard Unix convention and
keeps stdout pipeable.

CLIs call :func:`configure_logging` once at the start of ``main()``. Library
code keeps using ``structlog.get_logger(__name__)`` as before — the module
loggers are lazy proxies that pick up this configuration on first use, so the
order (configure first, then log) is what matters, not import timing.
"""

from __future__ import annotations

import logging
import sys

import structlog


class _StderrLogger:
    """Minimal structlog logger that writes to stderr, resolved at call time.

    structlog's built-in ``PrintLoggerFactory(file=sys.stderr)`` captures the
    ``sys.stderr`` object *once*, when configured. That's fine for a one-shot
    CLI process, but brittle anywhere the stream is swapped after
    configuration (notably pytest's ``capsys``, which replaces and then closes
    its capture buffer between tests — logging into the stale reference raises
    "I/O operation on closed file").

    Looking up ``sys.stderr`` on every write sidesteps that entirely: we always
    target whatever stderr is current. ``__getattr__`` maps every structlog
    level method (info, warning, error, …) onto the same write.
    """

    def msg(self, message: str) -> None:
        print(message, file=sys.stderr, flush=True)

    def __getattr__(self, _name: str):
        # Any level method (info/debug/warning/error/critical/…) -> msg.
        return self.msg


def configure_logging(level: int = logging.INFO) -> None:
    """Route structlog output to stderr, keeping stdout clean for results.

    Parameters
    ----------
    level
        Minimum level to emit (standard ``logging`` integer, default INFO).
        Messages below this level are dropped cheaply before rendering.
    """
    structlog.configure(
        # Render to stderr (resolved per-write) instead of the default stdout.
        logger_factory=lambda *_args, **_kw: _StderrLogger(),
        # Drop messages below `level` without paying the rendering cost.
        wrapper_class=structlog.make_filtering_bound_logger(level),
        # Don't cache the bound logger on first use: CLIs (and tests) may call
        # configure_logging() more than once per process and we want each call
        # to take effect rather than be pinned to the first configuration.
        cache_logger_on_first_use=False,
    )
