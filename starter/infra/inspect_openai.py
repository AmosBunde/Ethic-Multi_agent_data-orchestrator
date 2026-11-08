"""Discover which model deployments exist on the Azure OpenAI / AI Foundry resource
referenced by the Key Vault secrets ai-endpoint and ai-key.

AI Services (Foundry) resources do not answer the legacy deployments-list API, so this
also probes a set of likely deployment names directly against the embeddings and chat
endpoints and reports which respond.

    python infra/inspect_openai.py [extra-deployment-name ...]
"""
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from azure_login import get_credential
from azure.keyvault.secrets import SecretClient

VAULT = "structuredazureendpoint"
API_VERSION = "2024-12-01-preview"

EMBEDDING_CANDIDATES = [
    "text-embedding-ada-002", "text-embedding-3-small", "text-embedding-3-large",
    "ada-002", "embedding", "embeddings", "text-embedding",
]
CHAT_CANDIDATES = [
    "gpt-4.1-mini", "gpt-4.1", "gpt-4.1-nano", "gpt-4o-mini", "gpt-4o", "gpt-4",
    "gpt-35-turbo", "gpt-5-mini", "gpt-5-nano", "gpt-5", "chat", "gpt-4.1mini",
]


def _call(method, url, key, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"api-key": key, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode())
        except Exception:
            return e.code, {}


def main(credential=None):
    extra = sys.argv[1:]
    client = SecretClient(f"https://{VAULT}.vault.azure.net/", credential or get_credential())
    endpoint = client.get_secret("ai-endpoint").value.rstrip("/")
    key = client.get_secret("ai-key").value
    print("ai-endpoint:", endpoint)

    # 1. Legacy list (works for classic Azure OpenAI resources)
    status, data = _call("GET", f"{endpoint}/openai/deployments?api-version=2023-03-15-preview", key)
    rows = data.get("data", []) if isinstance(data, dict) else []
    if rows:
        print(f"\nDeployments (list API):")
        for d in rows:
            print(f"  {d.get('id',''):35s} {d.get('model',''):30s} {d.get('status','')}")
    else:
        print(f"\nList API returned no deployments (HTTP {status}); probing names instead.")

    # 2. Probe embeddings
    found_emb, found_chat = [], []
    print("\nEmbedding deployments:")
    for name in EMBEDDING_CANDIDATES + extra:
        status, data = _call("POST", f"{endpoint}/openai/deployments/{name}/embeddings?api-version={API_VERSION}",
                             key, {"input": "ping"})
        if status == 200:
            dims = len(data["data"][0]["embedding"])
            print(f"  OK   {name}  ({dims} dims)")
            found_emb.append(name)
        elif status not in (404,):
            msg = (data.get("error") or {}).get("message", "") if isinstance(data, dict) else ""
            print(f"  {status}  {name}  {msg[:90]}")

    # 3. Probe chat
    print("\nChat deployments:")
    for name in CHAT_CANDIDATES + extra:
        status, data = _call("POST", f"{endpoint}/openai/deployments/{name}/chat/completions?api-version={API_VERSION}",
                             key, {"messages": [{"role": "user", "content": "ping"}], "max_completion_tokens": 1})
        if status == 200:
            print(f"  OK   {name}  (model: {data.get('model', '?')})")
            found_chat.append(name)
        elif status not in (404,):
            msg = (data.get("error") or {}).get("message", "") if isinstance(data, dict) else ""
            print(f"  {status}  {name}  {msg[:90]}")

    print()
    if not found_emb:
        print("No embedding deployment found. chat.py needs one named text-embedding-ada-002.")
    if not found_chat:
        print("No chat deployment found. chat.py needs one named gpt-4.1-mini.")
    return 0 if (found_emb and found_chat) else 1


if __name__ == "__main__":
    sys.exit(main())
