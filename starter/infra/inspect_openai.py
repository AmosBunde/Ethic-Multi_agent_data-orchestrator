"""List the model deployments on the Azure OpenAI / AI Foundry resource referenced by the
Key Vault secrets ai-endpoint and ai-key, so deployment names in chat.py can be matched.

    python infra/inspect_openai.py
"""
import json
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from azure_login import get_credential
from azure.keyvault.secrets import SecretClient

VAULT = "structuredazureendpoint"


def main(credential=None):
    client = SecretClient(f"https://{VAULT}.vault.azure.net/", credential or get_credential())
    endpoint = client.get_secret("ai-endpoint").value.rstrip("/")
    key = client.get_secret("ai-key").value
    print("ai-endpoint:", endpoint)

    url = f"{endpoint}/openai/deployments?api-version=2023-03-15-preview"
    req = urllib.request.Request(url, headers={"api-key": key})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.load(r)
    except urllib.error.HTTPError as e:
        print(f"HTTP {e.code} from {url}\n{e.read().decode()[:500]}")
        return 1
    rows = data.get("data", [])
    if not rows:
        print("No deployments found on this resource.")
        return 1
    print(f"\n{'deployment name':35s} {'model':30s} status")
    for d in rows:
        print(f"{d.get('id',''):35s} {d.get('model',''):30s} {d.get('status','')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
