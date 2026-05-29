"""Unit tests for the aerolake-healthcheck CLI.

We invoke the main() function directly with argv lists (rather than
spawning a subprocess) so the tests are fast and can use our existing
moto-based fixtures from conftest.py.
"""

from __future__ import annotations

import json

from aerolake.scripts.healthcheck import main

# --- Success path --------------------------------------------------------

def test_main_returns_zero_when_healthcheck_passes(
    monkeypatch,
    test_settings,
    mock_s3,
    capsys,
):
    """When MinIO is reachable, main() returns 0 and prints success."""
    monkeypatch.setattr(
        "aerolake.scripts.healthcheck.get_settings",
        lambda: test_settings,
    )

    exit_code = main([])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "passed" in captured.out.lower()


def test_main_verbose_prints_endpoint_and_bucket(
    monkeypatch,
    test_settings,
    mock_s3,
    capsys,
):
    """In verbose mode, the success output includes bucket and region."""
    monkeypatch.setattr(
        "aerolake.scripts.healthcheck.get_settings",
        lambda: test_settings,
    )

    exit_code = main(["-v"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert test_settings.s3_bucket in captured.out
    assert test_settings.s3_region in captured.out


# --- JSON output ---------------------------------------------------------

def test_main_json_output_is_parseable(
    monkeypatch,
    test_settings,
    mock_s3,
    capsys,
):
    """In JSON mode, stdout must contain a valid JSON document."""
    monkeypatch.setattr(
        "aerolake.scripts.healthcheck.get_settings",
        lambda: test_settings,
    )

    exit_code = main(["--json"])

    captured = capsys.readouterr()
    assert exit_code == 0

    json_lines = [
        line for line in captured.out.splitlines()
        if line.strip().startswith("{")
    ]
    assert json_lines, "no JSON output found"
    report = json.loads(json_lines[-1])

    assert report["status"] == "ok"
    assert report["bucket"] == test_settings.s3_bucket
    assert report["error"] is None
    assert isinstance(report["latency_ms"], (int, float))


# --- Failure paths -------------------------------------------------------

def test_main_returns_one_when_bucket_missing(
    monkeypatch,
    test_settings,
    mock_s3,
    capsys,
):
    """When the bucket does not exist, main() returns 1."""
    bad_settings = test_settings.model_copy(update={"s3_bucket": "does-not-exist"})
    monkeypatch.setattr(
        "aerolake.scripts.healthcheck.get_settings",
        lambda: bad_settings,
    )

    exit_code = main([])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "failed" in captured.out.lower()


def test_main_json_on_failure_reports_error(
    monkeypatch,
    test_settings,
    mock_s3,
    capsys,
):
    """JSON output on failure should have status=error and a non-null error."""
    bad_settings = test_settings.model_copy(update={"s3_bucket": "missing-bucket"})
    monkeypatch.setattr(
        "aerolake.scripts.healthcheck.get_settings",
        lambda: bad_settings,
    )

    exit_code = main(["--json"])

    captured = capsys.readouterr()
    assert exit_code == 1

    json_lines = [
        line for line in captured.out.splitlines()
        if line.strip().startswith("{")
    ]
    assert json_lines
    report = json.loads(json_lines[-1])
    assert report["status"] == "error"
    assert report["error"] is not None


# --- Configuration error path --------------------------------------------

def test_main_returns_two_on_settings_load_failure(
    monkeypatch,
    capsys,
):
    """If Settings cannot be loaded, main() returns 2 (config error)."""
    def explode():
        raise RuntimeError("simulated config failure")

    monkeypatch.setattr(
        "aerolake.scripts.healthcheck.get_settings",
        explode,
    )

    exit_code = main([])

    captured = capsys.readouterr()
    assert exit_code == 2
    assert "error" in captured.out.lower()
