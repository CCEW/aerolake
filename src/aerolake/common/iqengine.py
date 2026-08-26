"""Client for the supported IQEngine catalog integration API.

AeroLake owns MinIO objects and uses this client only to trigger catalog
synchronization and consume read-only catalog results. It never accesses
IQEngine's MongoDB collections directly.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urljoin, urlsplit, urlunsplit
from urllib.request import Request, urlopen

import structlog

from aerolake.common.config import Settings, get_settings

logger = structlog.get_logger(__name__)


class IQEngineError(Exception):
    """Raised when the IQEngine integration API cannot complete a request."""


@dataclass(frozen=True)
class CatalogResponse:
    """JSON response returned by an IQEngine catalog operation."""

    status_code: int
    body: Any


UrlOpener = Callable[..., Any]


class IQEngineClient:
    """Small, injectable client for IQEngine's catalog API.

    The client is disabled when ``iqengine_url`` is empty. Endpoint paths are
    kept in one place so a future versioned IQEngine wrapper can change the
    contract without spreading URL knowledge through AeroLake.
    """

    _SYNC_PATH = "/api/datasources/{account}/{container}/sync"
    _SEARCH_PATH = "/api/datasources/query"
    _META_PATH = "/api/datasources/{account}/{container}/{filepath}/meta"

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        opener: UrlOpener = urlopen,
    ) -> None:
        self._settings = settings or get_settings()
        self._opener = opener
        self._base_url = self._normalize_base_url(self._settings.iqengine_url)

    @staticmethod
    def _normalize_base_url(value: str) -> str:
        """Normalize the configured API base while preserving its path prefix."""
        base = value.strip()
        if not base:
            return ""
        parts = urlsplit(base)
        if parts.scheme not in {"http", "https"} or not parts.netloc:
            raise IQEngineError("AEROLAKE_IQENGINE_URL must be an HTTP(S) URL")
        path = parts.path.rstrip("/")
        return urlunsplit((parts.scheme, parts.netloc, path + "/", "", ""))

    @property
    def enabled(self) -> bool:
        """Whether IQEngine integration has been configured."""
        return bool(self._base_url)

    def _url(self, path: str, params: Mapping[str, object] | None = None) -> str:
        if not self.enabled:
            raise IQEngineError("IQEngine integration is disabled; set AEROLAKE_IQENGINE_URL")
        url = urljoin(self._base_url, path.lstrip("/"))
        if params:
            url = f"{url}?{urlencode({k: str(v) for k, v in params.items()})}"
        return url

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, object] | None = None,
    ) -> CatalogResponse:
        """Issue one authenticated JSON request without logging credentials."""
        request = Request(self._url(path, params), method=method)
        request.add_header("Accept", "application/json")
        token = self._settings.iqengine_token.get_secret_value()
        if token:
            request.add_header("Authorization", f"Bearer {token}")

        try:
            with self._opener(request, timeout=self._settings.iqengine_timeout_s) as response:
                raw = response.read()
                status_code = int(response.status)
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise IQEngineError(
                f"IQEngine request failed with HTTP {exc.code}: {detail[:500]}"
            ) from exc
        except URLError as exc:
            raise IQEngineError(f"IQEngine request failed: {exc.reason}") from exc
        except TimeoutError as exc:
            raise IQEngineError("IQEngine request timed out") from exc

        try:
            body = json.loads(raw.decode("utf-8")) if raw else {}
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise IQEngineError("IQEngine returned an invalid JSON response") from exc
        if not isinstance(body, (dict, list)):
            raise IQEngineError("IQEngine returned an unsupported JSON value")

        logger.info("iqengine.request.ok", method=method, path=path, status_code=status_code)
        return CatalogResponse(status_code=status_code, body=body)

    def sync(self) -> CatalogResponse:
        """Request an idempotent synchronization for the configured datasource."""
        account = self._settings.iqengine_account
        container = self._settings.iqengine_container
        if not account or not container:
            raise IQEngineError(
                "AEROLAKE_IQENGINE_ACCOUNT and AEROLAKE_IQENGINE_CONTAINER are required"
            )
        path = self._SYNC_PATH.format(
            account=quote(account, safe=""),
            container=quote(container, safe=""),
        )
        return self._request("PUT", path)

    def search(self, **filters: object) -> CatalogResponse:
        """Run a read-only catalog query using IQEngine's query parameters."""
        return self._request("GET", self._SEARCH_PATH, params=filters or None)

    def metadata(self, filepath: str) -> CatalogResponse:
        """Retrieve catalog metadata for one datasource object path."""
        account = self._settings.iqengine_account
        container = self._settings.iqengine_container
        if not account or not container:
            raise IQEngineError(
                "AEROLAKE_IQENGINE_ACCOUNT and AEROLAKE_IQENGINE_CONTAINER are required"
            )
        path = self._META_PATH.format(
            account=quote(account, safe=""),
            container=quote(container, safe=""),
            filepath=quote(filepath.strip("/"), safe="/"),
        )
        return self._request("GET", path)
