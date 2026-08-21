from __future__ import annotations

import keyring

SERVICE = "DraftSmith"


def set_secret(name: str, value: str) -> None:
    keyring.set_password(SERVICE, name, value)


def get_secret(name: str) -> str | None:
    try:
        return keyring.get_password(SERVICE, name)
    except keyring.errors.KeyringError:
        # A locked/unavailable vault should behave like missing credentials;
        # never fall back to plaintext storage.
        return None
