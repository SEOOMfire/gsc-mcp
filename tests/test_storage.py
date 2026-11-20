"""OAuth storage and startup configuration."""

from __future__ import annotations

import asyncio

import pytest
from cryptography.fernet import Fernet
from key_value.aio.stores.memory import MemoryStore
from key_value.aio.wrappers.encryption import FernetEncryptionWrapper

from gsc_mcp.auth_storage import create_client_storage
from gsc_mcp.coordinator import load_oauth_settings, missing_runtime_env
from gsc_mcp.credentials import google_access_token


def test_storage_is_absent_until_configured(monkeypatch):
    monkeypatch.delenv("GSC_MCP_STORAGE_TYPE", raising=False)
    monkeypatch.delenv("GSC_MCP_STORAGE_PATH", raising=False)
    monkeypatch.setenv("GOOGLE_ADS_MCP_STORAGE_PATH", "/tmp/ads-should-be-ignored")
    assert create_client_storage() is None


def test_filetree_roundtrip_and_required_key(monkeypatch, tmp_path):
    monkeypatch.setenv("GSC_MCP_STORAGE_TYPE", "filetree")
    with pytest.raises(ValueError):
        create_client_storage()
    monkeypatch.setenv("GSC_MCP_STORAGE_PATH", str(tmp_path / "store"))
    with pytest.raises(ValueError, match="ENCRYPTION_KEY"):
        create_client_storage()
    monkeypatch.setenv("GSC_MCP_STORAGE_ENCRYPTION_KEY", Fernet.generate_key().decode())
    store = create_client_storage()
    assert isinstance(store, FernetEncryptionWrapper)

    async def round_trip():
        key = "https://example.com/oauth/client"
        value = {"client_id": key}
        await store.put(key, value, collection="mcp-oauth-proxy-clients")
        assert await store.get(key, collection="mcp-oauth-proxy-clients") == value

    asyncio.run(round_trip())


def test_non_fernet_material_is_derived(monkeypatch):
    monkeypatch.setenv("GSC_MCP_STORAGE_TYPE", "memory")
    monkeypatch.setenv("GSC_MCP_STORAGE_ENCRYPTION_KEY", "not-a-fernet-key")
    store = create_client_storage()
    assert isinstance(store, FernetEncryptionWrapper)
    assert isinstance(store.key_value, MemoryStore)


def test_unknown_storage_type_is_rejected(monkeypatch):
    monkeypatch.setenv("GSC_MCP_STORAGE_TYPE", "redis")
    with pytest.raises(ValueError, match="Unsupported"):
        create_client_storage()


def test_partial_oauth_configuration_is_rejected(monkeypatch):
    monkeypatch.setenv("GSC_MCP_CLIENT_ID", "dummy-client-id")
    with pytest.raises(RuntimeError, match="Incomplete OAuth configuration"):
        load_oauth_settings()


def test_runtime_env_lists_only_names(monkeypatch):
    for name in (
        "GSC_MCP_CLIENT_ID",
        "GSC_MCP_CLIENT_SECRET",
        "GSC_MCP_BASE_URL",
        "GSC_MCP_JWT_SIGNING_KEY",
        "GSC_MCP_STORAGE_ENCRYPTION_KEY",
        "GSC_MCP_STORAGE_PATH",
    ):
        monkeypatch.delenv(name, raising=False)
    missing = missing_runtime_env()
    assert "GSC_MCP_CLIENT_SECRET" in missing
    assert all(not value.startswith("ya29.") for value in missing)


def test_missing_session_has_no_google_token():
    with pytest.raises(PermissionError):
        google_access_token()
