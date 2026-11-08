// Azure Key Vault for the multi-agent orchestrator (alternative to create_keyvault.sh).
// Deploy at resource-group scope:
//   az group create -n rg-ethic-agents -l eastus
//   az deployment group create -g rg-ethic-agents -f keyvault.bicep \
//     -p keyVaultName=<globally-unique-name> \
//     -p secretsOfficerObjectId=$(az ad signed-in-user show --query id -o tsv)
// Secret values are not set here; load them with create_keyvault.sh or `az keyvault secret set`.

@minLength(3)
@maxLength(24)
@description('Globally unique Key Vault name (letters, digits, hyphens; must start with a letter).')
param keyVaultName string = 'structuredazureendpoint'

@description('Azure region. Defaults to the resource group location.')
param location string = resourceGroup().location

@description('Entra object ID of the user/identity that will write and read secrets.')
param secretsOfficerObjectId string

@description('Soft-delete retention in days (7-90).')
@minValue(7)
@maxValue(90)
param softDeleteRetentionDays int = 7

@description('Purge protection cannot be disabled once enabled. Keep off for a training project.')
param enablePurgeProtection bool = false

// Built-in role: Key Vault Secrets Officer
var secretsOfficerRoleId = subscriptionResourceId(
  'Microsoft.Authorization/roleDefinitions',
  'b86a8fe4-44ce-4948-aee5-eccb2c155cd7'
)

// Secret names chat.py reads. Created as empty placeholders is NOT possible in Key Vault,
// so these are only emitted as an output for documentation / tooling.
var secretNames = [
  'ai-endpoint'
  'ai-key'
  'postgres-host'
  'postgres-database'
  'postgres-user'
  'postgres-password'
  'mongodb-connection-string'
  'mongodb-database'
  'blob-connection-string'
  'content-safety-endpoint'
  'content-safety-key'
]

resource kv 'Microsoft.KeyVault/vaults@2023-07-01' = {
  name: keyVaultName
  location: location
  tags: {
    project: 'ethic-multi-agent-orchestrator'
    component: 'secrets'
  }
  properties: {
    tenantId: subscription().tenantId
    sku: {
      family: 'A'
      name: 'standard'
    }
    enableRbacAuthorization: true
    enableSoftDelete: true
    softDeleteRetentionInDays: softDeleteRetentionDays
    enablePurgeProtection: enablePurgeProtection ? true : null
    publicNetworkAccess: 'Enabled'
    networkAcls: {
      defaultAction: 'Allow'
      bypass: 'AzureServices'
    }
  }
}

resource secretsOfficer 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(kv.id, secretsOfficerObjectId, secretsOfficerRoleId)
  scope: kv
  properties: {
    roleDefinitionId: secretsOfficerRoleId
    principalId: secretsOfficerObjectId
    principalType: 'User'
  }
}

output keyVaultName string = kv.name
output keyVaultUri string = kv.properties.vaultUri
output expectedSecretNames array = secretNames
