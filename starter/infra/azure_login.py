"""Shared device-code login that prompts once and reuses the Key Vault token.

Why: with the account used here, azure-identity's device-code credential cannot find
its own account in the MSAL cache after signing in, so every Key Vault call re-prompts.
This wrapper performs one interactive login per token lifetime and caches the resulting
access token in memory and in an owner-only file, so chat.py, the loaders, and the
inspector all run without further prompts until the token expires (~1 hour).

The credential is created for the Key Vault's own Entra tenant, discovered from the
vault's unauthenticated 401 challenge, so no second tenant challenge occurs.

Delete ~/.ethic-agents to force a fresh sign-in.
"""
import json
import os
import re
import time
import urllib.error
import urllib.request
from pathlib import Path

from azure.core.credentials import AccessToken, AccessTokenInfo
from azure.identity import DeviceCodeCredential

VAULT = "structuredazureendpoint"
VAULT_URL = f"https://{VAULT}.vault.azure.net/"

_TOKEN_FILE = Path.home() / ".ethic-agents" / "tokens.json"
_SKEW_SECONDS = 300


def vault_tenant_id(vault_url: str = VAULT_URL):
    """Return the tenant GUID from the vault's WWW-Authenticate challenge, or None."""
    try:
        urllib.request.urlopen(f"{vault_url}secrets?api-version=7.4", timeout=15)
    except urllib.error.HTTPError as e:
        hdr = e.headers.get("WWW-Authenticate", "")
        m = re.search(r'authorization="https://login\.[^/]+/([0-9a-fA-F-]{36})"', hdr)
        return m.group(1) if m else None
    except Exception:
        return None
    return None


class OnePromptCredential:
    """Device-code login with one prompt per token lifetime and a file-backed token cache."""

    def __init__(self, tenant_id=None):
        kwargs = {"additionally_allowed_tenants": ["*"]}
        if tenant_id:
            kwargs["tenant_id"] = tenant_id
        self._inner = DeviceCodeCredential(**kwargs)
        self._tokens = {}
        self._load()

    # -- azure-core credential protocol ------------------------------------------
    def get_token(self, *scopes, **kwargs) -> AccessToken:
        key = " ".join(sorted(scopes))
        tok = self._tokens.get(key)
        if tok and tok.expires_on - time.time() > _SKEW_SECONDS:
            return tok
        # The inner credential is already pinned to the vault tenant, so the tenant_id
        # the Key Vault challenge passes along is not needed.
        tok = self._inner.get_token(*scopes)
        self._tokens[key] = tok
        self._save()
        return tok

    def get_token_info(self, *scopes, options=None) -> AccessTokenInfo:
        tok = self.get_token(*scopes)
        return AccessTokenInfo(tok.token, tok.expires_on)

    def close(self):
        pass

    # -- persistence ---------------------------------------------------------------
    def _load(self):
        try:
            data = json.loads(_TOKEN_FILE.read_text())
        except (OSError, ValueError):
            return
        now = time.time()
        for key, item in data.items():
            if item["expires_on"] - now > _SKEW_SECONDS:
                self._tokens[key] = AccessToken(item["token"], item["expires_on"])

    def _save(self):
        _TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(_TOKEN_FILE.parent, 0o700)
        payload = {k: {"token": t.token, "expires_on": t.expires_on} for k, t in self._tokens.items()}
        tmp = _TOKEN_FILE.with_suffix(".tmp")
        if tmp.exists():
            tmp.unlink()
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(json.dumps(payload))
        os.replace(tmp, _TOKEN_FILE)


def get_credential() -> OnePromptCredential:
    return OnePromptCredential(tenant_id=vault_tenant_id())
