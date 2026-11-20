# Copyright 2026 Google LLC.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Filetree storage for the FastMCP OAuth proxy.

Adapted from the Google Ads MCP. Redis and Firestore are intentionally absent.
"""

from __future__ import annotations

import os
import pathlib
from typing import Any

from cryptography.fernet import Fernet


def create_client_storage() -> Any:
    """Create the encrypted filetree or memory store described by GSC_MCP_* env vars.

    Returns None when no storage env vars are set, so unit tests can import the
    server without writing a store. A configured store requires an encryption key.
    """
    storage_type = os.environ.get("GSC_MCP_STORAGE_TYPE", "").strip().lower()
    storage_path = os.environ.get("GSC_MCP_STORAGE_PATH", "").strip()
    if not storage_type:
        if storage_path:
            storage_type = "filetree"
        else:
            return None

    if storage_type in ("filetree", "file", "dir", "path"):
        if not storage_path:
            raise ValueError(
                "Storage path must be provided via GSC_MCP_STORAGE_PATH."
            )
        from key_value.aio.stores.filetree import (
            FileTreeStore,
            FileTreeV1CollectionSanitizationStrategy,
            FileTreeV1KeySanitizationStrategy,
        )

        directory = pathlib.Path(storage_path)
        directory.mkdir(parents=True, exist_ok=True)
        store: Any = FileTreeStore(
            data_directory=directory,
            key_sanitization_strategy=FileTreeV1KeySanitizationStrategy(directory),
            collection_sanitization_strategy=FileTreeV1CollectionSanitizationStrategy(
                directory
            ),
        )
    elif storage_type == "memory":
        from key_value.aio.stores.memory import MemoryStore

        store = MemoryStore()
    else:
        raise ValueError(
            f"Unsupported client storage type: '{storage_type}'. "
            "Expected one of: filetree, memory."
        )

    encryption_key = os.environ.get("GSC_MCP_STORAGE_ENCRYPTION_KEY", "").strip()
    if not encryption_key:
        raise ValueError("GSC_MCP_STORAGE_ENCRYPTION_KEY is required")

    from key_value.aio.wrappers.encryption import FernetEncryptionWrapper

    return FernetEncryptionWrapper(
        key_value=store,
        fernet=_fernet(encryption_key),
        raise_on_decryption_error=False,
    )


def _fernet(encryption_key: str) -> Fernet:
    """Accept a Fernet key, or derive one from other high-entropy material."""
    try:
        return Fernet(encryption_key.encode("utf-8"))
    except Exception:
        from fastmcp.server.auth.jwt_issuer import derive_jwt_key

        derived = derive_jwt_key(
            high_entropy_material=encryption_key,
            salt="fastmcp-storage-encryption-key",
        )
        return Fernet(derived)
