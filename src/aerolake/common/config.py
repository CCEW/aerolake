"""Application settings loaded from environment variables.

Settings are sourced from the project's ``.env`` file (for local dev) and from
real environment variables (for CI and production). Every variable used by the
codebase must be declared here so misconfiguration fails fast at startup
rather than mid-pipeline.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

# Resolve the project root by walking up from this file:
# src/aerolake/common/config.py  ->  src/aerolake/common  ->  src/aerolake
# ->  src  ->  <project root>
PROJECT_ROOT: Path = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    """Top-level application settings.

    Values are loaded in the following priority order (highest first):
    1. Real environment variables (e.g. exported in the shell or set by Docker)
    2. The ``.env`` file at the project root
    3. The defaults declared on each field below
    """

    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        env_prefix="AEROLAKE_",
        extra="ignore",
        case_sensitive=False,
    )

    # --- S3 / MinIO storage ----------------------------------------------
    s3_access_key: str = Field(
        ...,
        description="S3 access key ID for the AeroLake service account.",
    )
    s3_secret_key: SecretStr = Field(
        ...,
        description="S3 secret access key. Wrapped in SecretStr so it never "
        "leaks into logs or tracebacks.",
    )
    s3_endpoint: str = Field(
        ...,
        description="S3 endpoint URL, e.g. http://localhost:9000 for local MinIO.",
    )
    s3_bucket: str = Field(
        ...,
        description="Bucket where SigMF captures are stored.",
    )
    s3_region: str = Field(
        default="us-east-1",
        description="S3 region. MinIO ignores this but boto3 requires it.",
    )
    # TLS verification against the endpoint. Lab servers (e.g. FAST) often use
    # an INTERNAL certificate authority that public trust stores don't know:
    # point `s3_ca_bundle` at the CA's .pem to verify properly. Only if the CA
    # file is not (yet) available, `s3_verify_ssl=false` disables verification
    # — a deliberate, temporary trade-off for internal networks.
    s3_verify_ssl: bool = Field(
        default=True,
        description="Verify the endpoint's TLS certificate (disable only as a "
        "temporary measure on trusted internal networks).",
    )
    s3_ca_bundle: str = Field(
        default="",
        description="Path to a CA bundle (.pem) that signs the endpoint's "
        "certificate — the clean fix for internal CAs. Empty = system store.",
    )

    # --- IQEngine catalog integration ------------------------------------
    iqengine_url: str = Field(
        default="",
        description="IQEngine integration API base URL; empty disables integration.",
    )
    iqengine_token: SecretStr = Field(
        default=SecretStr(""),
        description="Bearer token for the dedicated IQEngine service identity.",
    )
    iqengine_account: str = Field(
        default="",
        description="IQEngine datasource account/region identifier.",
    )
    iqengine_container: str = Field(
        default="",
        description="IQEngine datasource container/bucket identifier.",
    )
    iqengine_prefix: str = Field(
        default="",
        description="Optional object prefix configured on the IQEngine datasource.",
    )
    iqengine_timeout_s: float = Field(
        default=10.0,
        gt=0,
        description="Timeout for one IQEngine API request in seconds.",
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the application settings, cached after the first call.

    Use this function instead of instantiating ``Settings()`` directly. The
    cache means we parse the ``.env`` file exactly once per process, which
    matters when many modules need the same config.
    """
    return Settings()
