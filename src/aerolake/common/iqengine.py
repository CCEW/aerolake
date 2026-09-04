"""Client for the supported IQEngine catalog integration API.

AeroLake owns MinIO objects and uses this client only to trigger catalog
synchronization and consume read-only catalog results. It never accesses
IQEngine's MongoDB collections directly.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock, Thread
from time import time
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


@dataclass(frozen=True)
class CatalogRow:
    """A normalized read-only catalog result with MinIO object references."""

    data_key: str
    tags: dict[str, str]
    metadata: dict[str, str]


@dataclass(frozen=True)
class CatalogSearchResult:
    """Catalog rows plus the freshness/degraded state visible to callers."""

    rows: list[CatalogRow]
    stale: bool
    sync_in_flight: bool
    sync_error: str | None = None


UrlOpener = Callable[..., Any]


class IQEngineClient:
    """Small, injectable client for IQEngine's catalog API.

    The client is disabled when ``iqengine_url`` is empty. Endpoint paths are
    kept in one place so a future versioned IQEngine wrapper can change the
    contract without spreading URL knowledge through AeroLake.
    """

    _SYNC_PATH = "/api/v1/integration/datasources/{account}/{container}/sync"
    _SEARCH_PATH = "/api/v1/integration/datasources/query"
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
            url = f"{url}?{urlencode(params, doseq=True)}"
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
        return self._request("POST", path)

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


class IQEngineCatalog:
    """AeroLake-owned freshness and degraded-mode facade for IQEngine.

    This class orchestrates the supported API only. It does not access or
    duplicate IQEngine's MongoDB catalog. Sync state is process-local unless a
    state path is configured, and one background sync is allowed at a time.
    """

    def __init__(
        self,
        client: IQEngineClient | None = None,
        settings: Settings | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._client = client or IQEngineClient(self._settings)
        self._lock = Lock()
        self._sync_in_flight = False
        self._state: dict[str, Any] = self._read_state()

    def _state_path(self) -> Path | None:
        value = self._settings.iqengine_sync_state_path.strip()
        return Path(value).expanduser() if value else None

    def _read_state(self) -> dict[str, Any]:
        path = self._state_path()
        if path is None or not path.exists():
            return {}
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return value if isinstance(value, dict) else {}

    def _write_state(self) -> None:
        path = self._state_path()
        if path is None:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(self._state, indent=2) + "\n", encoding="utf-8")
        except OSError as exc:
            logger.warning("iqengine.sync_state.write_failed", error=str(exc))

    def _sync_worker(self) -> None:
        started = time()
        try:
            response = self._client.sync()
            with self._lock:
                self._state.update(
                    {
                        "last_success_at": datetime.now(UTC).isoformat(),
                        "last_status": response.body.get("status")
                        if isinstance(response.body, dict)
                        else response.status_code,
                        "last_error": None,
                        "last_duration_s": round(time() - started, 3),
                    }
                )
                self._write_state()
            logger.info("iqengine.sync.ok", duration_s=round(time() - started, 3))
        except IQEngineError as exc:
            with self._lock:
                self._state["last_error"] = str(exc)
                self._state["last_duration_s"] = round(time() - started, 3)
                self._write_state()
            logger.warning("iqengine.sync.failed", error=str(exc))
        finally:
            with self._lock:
                self._sync_in_flight = False

    def _ensure_fresh(self) -> tuple[bool, bool, str | None]:
        last_success = self._state.get("last_success_at")
        try:
            age = time() - datetime.fromisoformat(last_success).timestamp()
        except (TypeError, ValueError, OverflowError):
            age = float("inf")
        stale = age >= self._settings.iqengine_sync_interval_s
        with self._lock:
            if stale and not self._sync_in_flight:
                self._sync_in_flight = True
                Thread(target=self._sync_worker, daemon=True).start()
            return stale, self._sync_in_flight, self._state.get("last_error")

    @staticmethod
    def _records(body: Any) -> list[dict[str, Any]]:
        if isinstance(body, list):
            return [item for item in body if isinstance(item, dict)]
        if isinstance(body, dict):
            for key in ("results", "data", "captures", "records"):
                value = body.get(key)
                if isinstance(value, list):
                    return [item for item in value if isinstance(item, dict)]
        return []

    def search(self, *, prefix: str = "", filters: Mapping[str, object] | None = None) -> CatalogSearchResult:
        stale, in_flight, sync_error = self._ensure_fresh()
        query = dict(filters or {})
        if prefix:
            query.setdefault("prefix", prefix)
        response = self._client.search(**query)

        rows = []
        for record in self._records(response.body):
            # Extract the path from the lightweight stub
            filepath = record.get("data_key") or record.get("file_path") or record.get("key")
            if not isinstance(filepath, str) or not filepath:
                continue

            try:
                # Fetch the full SigMF metadata document for this specific capture
                meta_response = self._client.metadata(filepath)
                full_record = meta_response.body if isinstance(meta_response.body, dict) else {}

                # Inject the path back into the full record so _row knows the identity
                full_record["file_path"] = filepath

                if (row := self._row(full_record)) is not None:
                    rows.append(row)
            except IQEngineError as e:
                logger.warning("iqengine.metadata.failed", filepath=filepath, error=str(e))
                continue

        return CatalogSearchResult(
            rows=rows,
            stale=stale,
            sync_in_flight=in_flight,
            sync_error=sync_error,
        )

    @staticmethod
    def _row(record: dict[str, Any]) -> CatalogRow | None:
        raw_key = record.get("data_key") or record.get("file_path") or record.get("key")
        if not isinstance(raw_key, str) or not raw_key:
            return None
        data_key = raw_key[:-len(".sigmf-meta")] + ".sigmf-data" if raw_key.endswith(".sigmf-meta") else raw_key

        tags = {}
        metadata = {}

        # Extract native SigMF objects directly from the root of the metadata response
        sigmf_global = record.get("global") or {}
        sigmf_captures = record.get("captures") or [{}]
        capture_0 = sigmf_captures[0] if isinstance(sigmf_captures, list) and len(sigmf_captures) > 0 else {}

        # Map standard SigMF fields into the exact keys catalog.py expects
        if "core:hw" in sigmf_global:
            tags["hardware"] = str(sigmf_global["core:hw"])

        if "core:sample_rate" in sigmf_global:
            metadata["sample-rate"] = str(sigmf_global["core:sample_rate"])

        if "core:frequency" in capture_0:
            metadata["center-freq"] = str(capture_0["core:frequency"])

        # Extract signal_type
        st = record.get("aerolake:signal_type") or sigmf_global.get("aerolake:signal_type")
        if st:
            tags["signal-type"] = str(st)

        return CatalogRow(
            data_key=data_key,
            tags=tags,
            metadata=metadata,
        )
