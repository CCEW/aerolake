"""Shared pytest fixtures for the AeroLake test suite.

This file is automatically discovered by pytest. Any fixture defined here
is available to any test module without being imported explicitly.
"""

from __future__ import annotations

import boto3
import pytest
from moto import mock_aws

from aerolake.common.config import Settings
from aerolake.common.storage import StorageClient

# --- Configuration --------------------------------------------------------

@pytest.fixture
def test_settings() -> Settings:
    """A Settings instance with test credentials, isolated from the real .env.

    Note: passing values as kwargs takes precedence over .env file loading,
    so tests are deterministic regardless of the developer's local config.
    """
    return Settings(
        s3_access_key="test-access-key",
        s3_secret_key="test-secret-key",
        s3_endpoint="",
        s3_bucket="test-bucket",
        s3_region="us-east-1",
    )


# --- Mocked S3 ------------------------------------------------------------

@pytest.fixture
def mock_s3(test_settings: Settings):
    """Spin up a moto-mocked S3 with the test bucket pre-created.

    The mock is active only for the duration of the test. Once the test
    finishes, all mocked state is discarded, so each test starts fresh.
    """
    with mock_aws():
        s3 = boto3.client(
            "s3",
            aws_access_key_id=test_settings.s3_access_key,
            aws_secret_access_key=test_settings.s3_secret_key,
            region_name=test_settings.s3_region,
        )
        s3.create_bucket(Bucket=test_settings.s3_bucket)
        yield s3


@pytest.fixture
def storage_client(test_settings: Settings, mock_s3) -> StorageClient:
    """A StorageClient pointed at the moto-mocked S3.

    Depends on mock_s3 to ensure the mock is active before instantiation.
    """
    return StorageClient(test_settings)
