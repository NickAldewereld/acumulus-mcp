"""Shared pytest fixtures for acumulus-mcp tests."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _set_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Default env so AcumulusClient() constructs without arguments."""
    monkeypatch.setenv("ACUMULUS_CONTRACT_CODE", "123456")
    monkeypatch.setenv("ACUMULUS_USERNAME", "test-user")
    monkeypatch.setenv("ACUMULUS_API_TOKEN", "test-token")
    monkeypatch.delenv("ACUMULUS_TESTMODE", raising=False)


@pytest.fixture
def base_url() -> str:
    return "https://api.sielsystems.nl/acumulus/stable/"
