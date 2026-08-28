"""Tests for the IQEngine catalog API client."""

from __future__ import annotations

import io
from urllib.error import HTTPError

import pytest

from aerolake.common.config import Settings
from aerolake.common.iqengine import (
    CatalogResponse,
    IQEngineCatalog,
    IQEngineClient,
    IQEngineError,
)


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


def test_catalog_normalizes_rows_and_triggers_one_lazy_sync(tmp_path) -> None:
    class FakeClient:
        def __init__(self) -> None:
            self.sync_calls = 0

        def sync(self):
            self.sync_calls += 1
            return CatalogResponse(200, {"status": "queued"})

        def search(self, **filters):
            assert filters == {"prefix": "iridium/", "signal_type": "iridium"}
            return CatalogResponse(
                200,
                [{"file_path": "iridium/a/capture.sigmf-meta", "tags": {"signal-type": "iridium"}}],
            )

    settings = _settings(
        iqengine_sync_interval_s=3600,
        iqengine_sync_state_path=str(tmp_path / "sync-state.json"),
    )
    fake = FakeClient()
    catalog = IQEngineCatalog(fake, settings)

    result = catalog.search(prefix="iridium/", filters={"signal_type": "iridium"})

    assert result.rows[0].data_key == "iridium/a/capture.sigmf-data"
    assert result.rows[0].tags == {"signal-type": "iridium"}
    assert result.stale is True
    assert result.sync_in_flight is True

    for _ in range(100):
        if fake.sync_calls:
            break
    assert fake.sync_calls == 1
