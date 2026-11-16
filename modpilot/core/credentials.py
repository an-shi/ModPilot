from __future__ import annotations

import base64
import ctypes
import ctypes.wintypes
import json
import logging
import os
import sys
from pathlib import Path
from typing import Protocol

from .errors import CredentialError
from ..i18n import tr

log = logging.getLogger(__name__)

SERVICE_NAME = "ModPilot"
NEXUS_SECRET = "nexus_api_key"
LLM_SECRET = "llm_api_key"


class SecretBackend(Protocol):
    name: str

    def get(self, key: str) -> str | None: ...

    def set(self, key: str, value: str) -> None: ...

    def delete(self, key: str) -> None: ...


class InMemorySecretBackend:
    @property
    def name(self) -> str:
        return tr('core.credentials.memory_test_only')

    def __init__(self, initial: dict[str, str] | None = None) -> None:
        self._values = dict(initial or {})

    def get(self, key: str) -> str | None:
        return self._values.get(key)

    def set(self, key: str, value: str) -> None:
        if value:
            self._values[key] = value
        else:
            self._values.pop(key, None)

    def delete(self, key: str) -> None:
        self._values.pop(key, None)


class KeyringBackend:
    @property
    def name(self) -> str:
        return tr('core.credentials.windows_credential_manager')

    def __init__(self) -> None:
        import keyring  # imported lazily so tests can avoid platform backends

        self._keyring = keyring

    def get(self, key: str) -> str | None:
        value = self._keyring.get_password(SERVICE_NAME, key)
        return value or None

    def set(self, key: str, value: str) -> None:
        if value:
            self._keyring.set_password(SERVICE_NAME, key, value)
        else:
            self.delete(key)

    def delete(self, key: str) -> None:
        try:
            self._keyring.delete_password(SERVICE_NAME, key)
        except Exception:
            pass


class _DataBlob(ctypes.Structure):
    _fields_ = [
        ("cbData", ctypes.wintypes.DWORD),
        ("pbData", ctypes.POINTER(ctypes.c_char)),
    ]


class DpapiBackend:
    """Encrypted-at-rest fallback using the current Windows user's DPAPI key.

    This is only used when the Windows Credential Manager backend is unavailable
    (for example in a restricted portable environment). The ciphertext can only
    be decrypted by the same Windows user on the same machine.
    """

    @property
    def name(self) -> str:
        return tr('core.credentials.windows_dpapi_encrypted_file')

    def __init__(self, path: Path) -> None:
        if sys.platform != "win32":
            raise CredentialError(tr('core.credentials.dpapi_is_only_available_on_windows'))
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _load(self) -> dict[str, str]:
        if not self.path.exists():
            return {}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return {str(k): str(v) for k, v in data.items()}
        except Exception as exc:
            raise CredentialError(tr('core.credentials.credential_file_corrupted_0', exc)) from exc

    def _save(self, data: dict[str, str]) -> None:
        temp = self.path.with_suffix(".tmp")
        temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temp, self.path)

    @staticmethod
    def _protect(value: str) -> str:
        raw = value.encode("utf-8")
        blob_in = _DataBlob(len(raw), ctypes.cast(ctypes.c_char_p(raw), ctypes.POINTER(ctypes.c_char)))
        blob_out = _DataBlob()
        if not ctypes.windll.crypt32.CryptProtectData(
            ctypes.byref(blob_in),
            None,
            None,
            None,
            None,
            0,
            ctypes.byref(blob_out),
        ):
            raise CredentialError(tr('core.credentials.dpapi_encryption_failed'))
        try:
            encrypted = ctypes.string_at(blob_out.pbData, blob_out.cbData)
            return base64.b64encode(encrypted).decode("ascii")
        finally:
            ctypes.windll.kernel32.LocalFree(blob_out.pbData)

    @staticmethod
    def _unprotect(value: str) -> str:
        try:
            encrypted = base64.b64decode(value)
        except Exception as exc:
            raise CredentialError(tr('core.credentials.invalid_dpapi_credential_format')) from exc
        blob_in = _DataBlob(
            len(encrypted), ctypes.cast(ctypes.c_char_p(encrypted), ctypes.POINTER(ctypes.c_char))
        )
        blob_out = _DataBlob()
        if not ctypes.windll.crypt32.CryptUnprotectData(
            ctypes.byref(blob_in),
            None,
            None,
            None,
            None,
            0,
            ctypes.byref(blob_out),
        ):
            raise CredentialError(tr('core.credentials.dpapi_decryption_failed_a_different_windows_user_may_d258ad'))
        try:
            return ctypes.string_at(blob_out.pbData, blob_out.cbData).decode("utf-8")
        finally:
            ctypes.windll.kernel32.LocalFree(blob_out.pbData)

    def get(self, key: str) -> str | None:
        encoded = self._load().get(key)
        return self._unprotect(encoded) if encoded else None

    def set(self, key: str, value: str) -> None:
        data = self._load()
        if value:
            data[key] = self._protect(value)
        else:
            data.pop(key, None)
        self._save(data)

    def delete(self, key: str) -> None:
        data = self._load()
        if key in data:
            del data[key]
            self._save(data)


class CredentialStore:
    """Secret storage facade. Values are never written to config or logs."""

    def __init__(self, data_dir: Path, backend: SecretBackend | None = None) -> None:
        self.data_dir = data_dir
        self._backend = backend or self._choose_backend()
        log.info(tr('core.credentials.credential_storage_backend_s'), self._backend.name)

    def _choose_backend(self) -> SecretBackend:
        try:
            backend = KeyringBackend()
            # A read is harmless and forces backend initialisation early.
            backend.get("__modpilot_probe__")
            return backend
        except Exception as exc:
            log.warning(tr('core.credentials.windows_credential_manager_is_unavailable_falling_ba_8b30a0'), exc)
        if sys.platform == "win32":
            return DpapiBackend(self.data_dir / "credentials.dat")
        return InMemorySecretBackend()

    @property
    def backend_name(self) -> str:
        return self._backend.name

    def get(self, key: str) -> str | None:
        try:
            value = self._backend.get(key)
            return value if value else None
        except Exception as exc:
            log.error(tr('core.credentials.failed_to_read_credentials_s_s'), key, exc)
            raise CredentialError(tr('core.credentials.failed_to_read_credentials_0', exc)) from exc

    def set(self, key: str, value: str) -> None:
        try:
            self._backend.set(key, value.strip())
        except Exception as exc:
            log.error(tr('core.credentials.failed_to_save_credentials_s_s'), key, exc)
            raise CredentialError(tr('core.credentials.failed_to_save_credentials_0', exc)) from exc

    def delete(self, key: str) -> None:
        try:
            self._backend.delete(key)
        except Exception as exc:
            raise CredentialError(tr('core.credentials.failed_to_delete_credentials_0', exc)) from exc

    def has(self, key: str) -> bool:
        return bool(self.get(key))

    def known_secrets(self) -> list[str]:
        values: list[str] = []
        for key in (NEXUS_SECRET, LLM_SECRET):
            try:
                value = self.get(key)
            except CredentialError:
                continue
            if value:
                values.append(value)
        return values
