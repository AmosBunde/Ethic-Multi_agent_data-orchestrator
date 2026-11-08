"""Shared device-code login with a persistent token cache.

First call prompts once; later calls (and later processes) reuse the cached login.
Delete ~/.ethic-agents to force a fresh sign-in.
"""
from pathlib import Path

from azure.identity import AuthenticationRecord, DeviceCodeCredential, TokenCachePersistenceOptions

_RECORD = Path.home() / ".ethic-agents" / "auth_record.json"
_CACHE = TokenCachePersistenceOptions(name="ethic-agents", allow_unencrypted_storage=True)


def get_credential() -> DeviceCodeCredential:
    if _RECORD.exists():
        record = AuthenticationRecord.deserialize(_RECORD.read_text())
        return DeviceCodeCredential(cache_persistence_options=_CACHE, authentication_record=record)
    cred = DeviceCodeCredential(cache_persistence_options=_CACHE)
    record = cred.authenticate(scopes=["https://vault.azure.net/.default"])
    _RECORD.parent.mkdir(parents=True, exist_ok=True)
    _RECORD.write_text(record.serialize())
    return cred
