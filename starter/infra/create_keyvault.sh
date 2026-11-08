#!/usr/bin/env bash
# Creates the single Azure Key Vault used by chat.py and loads the 14 secrets
# the three agents need. Idempotent: safe to re-run.
#
# Usage:
#   az login
#   cp secrets.env.example secrets.env   # fill in real values
#   ./create_keyvault.sh [-g RESOURCE_GROUP] [-n KEYVAULT_NAME] [-l LOCATION] [-f secrets.env]
#
# Required role on the subscription/resource group: Owner or
# "User Access Administrator" + "Contributor" (needed for the RBAC role assignment).
set -euo pipefail

RESOURCE_GROUP="${RESOURCE_GROUP:-rg-ethic-agents}"
LOCATION="${LOCATION:-eastus}"
KEYVAULT_NAME="${KEYVAULT_NAME:-structuredazureendpoint}"
SECRETS_FILE="${SECRETS_FILE:-$(dirname "$0")/secrets.env}"

while getopts "g:n:l:f:h" opt; do
  case "$opt" in
    g) RESOURCE_GROUP="$OPTARG" ;;
    n) KEYVAULT_NAME="$OPTARG" ;;
    l) LOCATION="$OPTARG" ;;
    f) SECRETS_FILE="$OPTARG" ;;
    h) sed -n '2,12p' "$0"; exit 0 ;;
    *) exit 1 ;;
  esac
done

command -v az >/dev/null || { echo "az CLI not found. Install: https://aka.ms/installazurecli" >&2; exit 1; }
az account show >/dev/null 2>&1 || { echo "Not logged in. Run: az login" >&2; exit 1; }

# Key Vault names are globally unique, 3-24 chars, letters/digits/hyphens, start with a letter.
if ! [[ "$KEYVAULT_NAME" =~ ^[a-zA-Z][a-zA-Z0-9-]{2,23}$ ]]; then
  echo "Invalid Key Vault name '$KEYVAULT_NAME' (3-24 chars, alphanumeric/hyphen, start with a letter)" >&2
  exit 1
fi

SUB_ID=$(az account show --query id -o tsv)
ME_OID=$(az ad signed-in-user show --query id -o tsv)

echo "Subscription : $SUB_ID"
echo "Resource grp : $RESOURCE_GROUP ($LOCATION)"
echo "Key Vault    : $KEYVAULT_NAME"
echo

az group create -n "$RESOURCE_GROUP" -l "$LOCATION" -o none

if az keyvault show -n "$KEYVAULT_NAME" -g "$RESOURCE_GROUP" >/dev/null 2>&1; then
  echo "Key Vault already exists, reusing."
else
  # Recover a soft-deleted vault with the same name rather than failing.
  if az keyvault list-deleted --query "[?name=='$KEYVAULT_NAME'].name" -o tsv | grep -q .; then
    echo "Recovering soft-deleted vault $KEYVAULT_NAME ..."
    az keyvault recover -n "$KEYVAULT_NAME" -o none
  else
    az keyvault create \
      -n "$KEYVAULT_NAME" -g "$RESOURCE_GROUP" -l "$LOCATION" \
      --sku standard \
      --enable-rbac-authorization true \
      --retention-days 7 \
      --public-network-access Enabled \
      --tags project=ethic-multi-agent-orchestrator component=secrets \
      -o none
    echo "Key Vault created."
  fi
fi

KV_ID=$(az keyvault show -n "$KEYVAULT_NAME" -g "$RESOURCE_GROUP" --query id -o tsv)
KV_URI=$(az keyvault show -n "$KEYVAULT_NAME" -g "$RESOURCE_GROUP" --query properties.vaultUri -o tsv)

# Let the signed-in user write and read secrets (RBAC model, no access policies).
az role assignment create \
  --assignee-object-id "$ME_OID" --assignee-principal-type User \
  --role "Key Vault Secrets Officer" --scope "$KV_ID" -o none 2>/dev/null \
  || echo "Role assignment already present (or insufficient rights to assign; continuing)."

# RBAC propagation can take up to a minute.
echo "Waiting for RBAC propagation..."
for i in $(seq 1 12); do
  az keyvault secret list --vault-name "$KEYVAULT_NAME" -o none >/dev/null 2>&1 && break
  sleep 5
done

# ---- secrets -----------------------------------------------------------------
# Names below must match what chat.py passes to client.get_secret(...).
# See README.md in this folder for the mapping to chat.py variables.
EXPECTED=(
  ai-endpoint
  ai-key
  postgres-host
  postgres-database
  postgres-user
  postgres-password
  mongodb-connection-string
  mongodb-database
  blob-connection-string
  content-safety-endpoint
  content-safety-key
)

if [[ -f "$SECRETS_FILE" ]]; then
  echo "Loading secrets from $SECRETS_FILE"
  set -a; # shellcheck disable=SC1090
  source "$SECRETS_FILE"; set +a
  missing=0
  for name in "${EXPECTED[@]}"; do
    var="${name//-/_}"; var="${var^^}"
    val="${!var:-}"
    if [[ -z "$val" ]]; then
      echo "  skip  $name  (env var $var is empty)"; missing=1; continue
    fi
    az keyvault secret set --vault-name "$KEYVAULT_NAME" -n "$name" --value "$val" -o none
    echo "  set   $name"
  done
  [[ $missing -eq 1 ]] && echo "Some secrets were skipped. Fill them in $SECRETS_FILE and re-run, or set them in the portal."
else
  echo "No secrets file at $SECRETS_FILE. Vault created but empty."
  echo "Copy secrets.env.example to secrets.env, fill it in, and re-run."
fi

echo
echo "Done. Use these in chat.py:"
echo "  keyVaultName = \"$KEYVAULT_NAME\""
echo "  KVUri        = \"$KV_URI\""
