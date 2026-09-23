from __future__ import annotations

import os
import platform

import keyring


SERVICE = "codex-local-ops"


class SecretStore:
    def get(self, name: str) -> str | None:
        value = keyring.get_password(SERVICE, name)
        if value is not None:
            return value
        env_name = "CLOPS_SECRET_" + "".join(c if c.isalnum() else "_" for c in name.upper())
        return os.environ.get(env_name)

    def set(self, name: str, value: str) -> None:
        keyring.set_password(SERVICE, name, value)

    def delete(self, name: str) -> None:
        try:
            keyring.delete_password(SERVICE, name)
        except keyring.errors.PasswordDeleteError:
            return

    def info(self) -> dict:
        backend = keyring.get_keyring()
        return {"platform": platform.system().lower(), "backend": backend.__class__.__name__, "plaintext": False}


def current_secret_store() -> SecretStore:
    return SecretStore()
