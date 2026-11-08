# Azure Key Vault for the Multi-Agent Orchestrator

`chat.py` reads every credential from **one** Azure Key Vault at startup, authenticating
the signed-in user with a device-code flow. This folder creates that vault and loads the
11 secrets the three agents need.

## 1. Create the vault and load secrets

```bash
az login                                   # same account you will run chat.py with
cd starter/infra
cp secrets.env.example secrets.env         # fill in real values (file is git-ignored)
./create_keyvault.sh                       # vault name defaults to structuredazureendpoint
```

The script creates resource group `rg-ethic-agents` (override with `-g`), the vault with
RBAC authorization and soft delete, grants you **Key Vault Secrets Officer**, and sets each
secret from `secrets.env`. Re-running is safe; it updates existing secrets in place.

Prefer infrastructure-as-code? `keyvault.bicep` creates the same vault and role assignment:

```bash
az deployment group create -g rg-ethic-agents -f keyvault.bicep \
  -p secretsOfficerObjectId=$(az ad signed-in-user show --query id -o tsv)
```

Then load secrets with `./create_keyvault.sh` or `az keyvault secret set`.

## 2. Verify

```bash
pip install azure-identity azure-keyvault-secrets
python verify_keyvault.py
```

## 3. Wire into chat.py

Already done in `chat.py`: `keyVaultName = "structuredazureendpoint"` and the secret
reads below. The structured and unstructured agents share one Azure OpenAI resource,
and the Mongo collection name is fixed to `permits` to match the loader script.

| chat.py variable                        | Key Vault secret            | Source Azure resource                         |
|-----------------------------------------|-----------------------------|-----------------------------------------------|
| `structuredazureendpoint`               | `ai-endpoint`               | AI Foundry / Azure OpenAI endpoint URL        |
| `structuredazureapikey`                 | `ai-key`                    | AI Foundry / Azure OpenAI Key 1               |
| `structuredpostgresqlhost`              | `postgres-host`             | PostgreSQL Flexible Server FQDN               |
| `structuredpostgresqldbname`            | `postgres-database`         | PostgreSQL database name                      |
| `structuredpostgresqluser`              | `postgres-user`             | PostgreSQL admin login                        |
| `structuredpostgresqlpassword`          | `postgres-password`         | PostgreSQL admin password                     |
| `unstructuredmongourl`                  | `mongodb-connection-string` | Cosmos DB for MongoDB vCore connection string |
| `unstructureddbname`                    | `mongodb-database`          | Mongo database (`documents`)                  |
| `unstructuredcollectionname`            | *(constant `permits`)*      | Set by `data/unstructured_load_azure.py`      |
| `unstructuredazureendpoint`             | `ai-endpoint` (shared)      | Same Azure OpenAI resource                    |
| `unstructuredazurekey`                  | `ai-key` (shared)           | Same Azure OpenAI resource                    |
| `multimodalazureconnstring`             | `blob-connection-string`    | Storage account connection string             |
| `multimodalazurecontentsafetyendpoint`  | `content-safety-endpoint`   | Azure AI Content Safety endpoint              |
| `multimodalazurecontentsafetykey`       | `content-safety-key`        | Azure AI Content Safety Key 1                 |

Required model deployments on the Azure OpenAI resource: `gpt-4.1-mini` and
`text-embedding-ada-002`. The storage account needs a container named `houses`.

## Design notes

- **One vault, not three.** `chat.py` opens a single `SecretClient`. Per-agent access control
  is done with RBAC on individual secrets if needed, not with separate vaults.
- **RBAC authorization** instead of legacy access policies. Anyone who only needs to run
  `chat.py` can be granted the read-only role **Key Vault Secrets User**.
- **Soft delete on, purge protection off.** A deleted vault can be recovered for 7 days;
  the script auto-recovers if the name is soft-deleted. Purge protection is irreversible,
  so it is left off for a training project.
- **Public network access enabled** so the device-code flow works from a laptop. Lock down
  with a firewall or private endpoint for production.
