"""Inventory Azure OpenAI / AI Foundry accounts in the subscription and create the two
model deployments chat.py needs on the account behind the Key Vault's ai-endpoint.

    python infra/create_deployments.py            # list everything, create missing
    python infra/create_deployments.py --list     # inventory only, change nothing

Needs Cognitive Services Contributor (or Contributor) on the target account. Uses the
same one-prompt device-code login as the rest of infra/, for the management scope.
"""
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from azure_login import get_credential
from azure.keyvault.secrets import SecretClient

VAULT = "structuredazureendpoint"
ARM = "https://management.azure.com"
ARM_SCOPE = "https://management.azure.com/.default"
CS_API = "2024-10-01"

# name -> (model format, model name, model version, preferred skus, capacity in K TPM)
WANTED = {
    "gpt-4.1-mini":           ("OpenAI", "gpt-4.1-mini",           "2025-04-14", ["GlobalStandard", "Standard"], 50),
    "text-embedding-ada-002": ("OpenAI", "text-embedding-ada-002", "2",          ["Standard", "GlobalStandard"], 50),
}


def arm(token, method, path, body=None, params=None):
    url = f"{ARM}{path}?{urllib.parse.urlencode(params or {'api-version': CS_API})}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            raw = r.read().decode()
            return r.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, {"error": {"message": raw[:300]}}


def main():
    list_only = "--list" in sys.argv
    cred = get_credential()
    secrets = SecretClient(f"https://{VAULT}.vault.azure.net/", cred)
    target_host = urllib.parse.urlparse(secrets.get_secret("ai-endpoint").value).hostname.lower()
    print("Target endpoint host (from Key Vault ai-endpoint):", target_host)

    token = cred.get_token(ARM_SCOPE).token

    status, subs = arm(token, "GET", "/subscriptions", params={"api-version": "2022-12-01"})
    if status != 200:
        print("Cannot list subscriptions:", subs); return 1
    subs = subs.get("value", [])
    print(f"Subscriptions visible: {len(subs)}")

    target = None
    for sub in subs:
        sid = sub["subscriptionId"]
        status, accts = arm(token, "GET", f"/subscriptions/{sid}/providers/Microsoft.CognitiveServices/accounts")
        for a in accts.get("value", []):
            props = a.get("properties", {})
            host = urllib.parse.urlparse(props.get("endpoint", "")).hostname or ""
            print(f"\nAccount: {a['name']}  kind={a.get('kind')}  location={a.get('location')}  sku={a.get('sku',{}).get('name')}")
            print(f"  endpoint: {props.get('endpoint')}")
            status, deps = arm(token, "GET", f"{a['id']}/deployments")
            deps = deps.get("value", [])
            if deps:
                for d in deps:
                    m = d["properties"].get("model", {})
                    print(f"  deployment: {d['name']:30s} {m.get('name','')}:{m.get('version','')}  "
                          f"sku={d.get('sku',{}).get('name')}  state={d['properties'].get('provisioningState')}")
            else:
                print("  deployments: none")
            if host.lower() == target_host:
                target = (a, deps)

    if target is None:
        print(f"\nNo account found whose endpoint matches {target_host}. Check the ai-endpoint secret.")
        return 1

    acct, deps = target
    existing = {d["name"] for d in deps}
    missing = [n for n in WANTED if n not in existing]
    print(f"\nTarget account: {acct['name']} ({acct.get('location')}). Missing deployments: {missing or 'none'}")
    if list_only or not missing:
        return 0

    for name in missing:
        fmt, model, version, skus, capacity = WANTED[name]
        created = False
        for sku in skus:
            body = {
                "sku": {"name": sku, "capacity": capacity},
                "properties": {
                    "model": {"format": fmt, "name": model, "version": version},
                    "versionUpgradeOption": "OnceNewDefaultVersionAvailable",
                },
            }
            print(f"\nCreating {name} ({model}:{version}, sku={sku}, capacity={capacity}K TPM) ...")
            status, resp = arm(token, "PUT", f"{acct['id']}/deployments/{name}", body)
            if status in (200, 201, 202):
                created = True
                break
            msg = (resp.get("error") or {}).get("message", str(resp))
            print(f"  HTTP {status}: {msg[:300]}")
            if "capacity" in msg.lower() or "quota" in msg.lower():
                body["sku"]["capacity"] = 10
                print("  retrying with capacity=10 ...")
                status, resp = arm(token, "PUT", f"{acct['id']}/deployments/{name}", body)
                if status in (200, 201, 202):
                    created = True
                    break
                print(f"  HTTP {status}: {(resp.get('error') or {}).get('message', '')[:300]}")
        if not created:
            print(f"  FAILED to create {name}. Create it in the Foundry portal.")
            continue

        for _ in range(30):
            status, d = arm(token, "GET", f"{acct['id']}/deployments/{name}")
            state = d.get("properties", {}).get("provisioningState")
            if state in ("Succeeded", "Failed", "Canceled"):
                break
            time.sleep(5)
        print(f"  {name}: {state}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
