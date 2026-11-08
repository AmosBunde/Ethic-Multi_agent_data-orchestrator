"""Check that the Key Vault is reachable and holds every secret chat.py expects.

Usage:
    pip install azure-identity azure-keyvault-secrets
    python verify_keyvault.py [key-vault-name]   # defaults to structuredazureendpoint

Authenticates the same way chat.py does (device code), so passing this check
means chat.py's secret loading will work for the signed-in user.
"""
import sys

from azure.core.exceptions import HttpResponseError, ResourceNotFoundError
from azure.identity import DeviceCodeCredential
from azure.keyvault.secrets import SecretClient

DEFAULT_VAULT = "structuredazureendpoint"

EXPECTED = [
    "ai-endpoint",
    "ai-key",
    "postgres-host",
    "postgres-database",
    "postgres-user",
    "postgres-password",
    "mongodb-connection-string",
    "mongodb-database",
    "blob-connection-string",
    "content-safety-endpoint",
    "content-safety-key",
]


def main() -> int:
    vault_name = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_VAULT
    vault_uri = f"https://{vault_name}.vault.azure.net/"
    print(f"Vault: {vault_uri}")

    client = SecretClient(vault_url=vault_uri, credential=DeviceCodeCredential())

    try:
        present = {p.name for p in client.list_properties_of_secrets()}
    except HttpResponseError as exc:
        print(f"Cannot list secrets: {exc.message}")
        print("Check that you have the 'Key Vault Secrets Officer' or 'Key Vault Secrets User' role on the vault.")
        return 1

    ok = True
    for name in EXPECTED:
        if name not in present:
            print(f"  MISSING  {name}")
            ok = False
            continue
        try:
            value = client.get_secret(name).value or ""
        except ResourceNotFoundError:
            print(f"  MISSING  {name}")
            ok = False
            continue
        state = "ok" if value.strip() else "EMPTY"
        ok = ok and state == "ok"
        print(f"  {state:<8} {name}")

    extra = sorted(present - set(EXPECTED))
    if extra:
        print("\nUnreferenced secrets in vault:", ", ".join(extra))

    print("\nAll expected secrets present." if ok else "\nFix the items above, then re-run.")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
