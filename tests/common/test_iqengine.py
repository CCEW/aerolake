"""Tests for the IQEngine catalog API client."""

from __future__ import annotations

import io
from urllib.error import HTTPError

import pytest

from aerolake.common.config import Settings
from aerolake.common.iqengine import IQEngineClient, IQEngineError


class _Response:
    status = 200

    def __init__(self, body: bytes = b'{"ok": true}') -> None:
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        return None

    def read(self) -> bytes:
        return self._body


def _settings(**overrides) -> Settings:
    values = {
        "s3_access_key": "test-access-key",
        "s3_secret_key": "test-secret-key",
        "s3_endpoint": "http://localhost:9000",
        "s3_bucket": "test-bucket",
        "iqengine_url": "https://iqengine.example.test/api-root",
        "iqengine_token": "service-token",
        "iqengine_account": "us-east/1",
        "iqengine_container": "captures bucket",
    }
    values.update(overrides)
    return Settings(**values)


def test_disabled_client_requires_explicit_configuration(test_settings) -> None:
    client = IQEngineClient(test_settings)

    assert not client.enabled
    with pytest.raises(IQEngineError, match="integration is disabled"):
        client.search(signal_type="iridium")


def test_sync_uses_bearer_auth_and_datasource_path() -> None:
    requests = []

    def opener(request, *, timeout):
        requests.append((request, timeout))
        return _Response(b'{"status": "complete"}')

    client = IQEngineClient(_settings(), opener=opener)
    response = client.sync()

    assert response.body == {"status": "complete"}
    assert requests[0][0].method == "PUT"
    assert requests[0][0].full_url == (
        "https://iqengine.example.test/api-root/api/datasources/"
        "us-east%2F1/captures%20bucket/sync"
    )
    assert requests[0][0].get_header("Authorization") == "Bearer service-token"
    assert requests[0][1] == 10.0


def test_search_encodes_filters() -> None:
    requests = []

    def opener(request, *, timeout):
        requests.append(request)
        return _Response(
            b'[{"type": "api", "account": "us-east-1", '
            b'"container": "captures", "file_path": "recordings/example"}]'
        )

    response = IQEngineClient(_settings(), opener=opener).search(
        signal_type="iridium",
        center_frequency=1_626_000_000,
    )

    assert response.body[0]["file_path"] == "recordings/example"
    assert requests[0].full_url == (
        "https://iqengine.example.test/api-root/api/datasources/query?"
        "signal_type=iridium&center_frequency=1626000000"
    )


def test_metadata_encodes_object_path_without_encoding_slashes() -> None:
    requests = []

    def opener(request, *, timeout):
        requests.append(request)
        return _Response()

    IQEngineClient(_settings(), opener=opener).metadata(
        "iridium/2026-08/session one/capture.sigmf-meta"
    )

    assert requests[0].full_url == (
        "https://iqengine.example.test/api-root/api/datasources/"
        "us-east%2F1/captures%20bucket/iridium/2026-08/"
        "session%20one/capture.sigmf-meta/meta"
    )


def test_http_error_is_wrapped() -> None:
    def opener(request, *, timeout):
        raise HTTPError(
            request.full_url,
            401,
            "Unauthorized",
            hdrs=None,
            fp=io.BytesIO(b'{"error": "invalid token"}'),
        )

    with pytest.raises(IQEngineError, match="HTTP 401"):
        IQEngineClient(_settings(), opener=opener).search()
