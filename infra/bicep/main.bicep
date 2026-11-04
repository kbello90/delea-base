// DELEA backend infrastructure: one template for every tier (TRD §2.1; hosted-demo plan step 3).
// Shared tier: Essential + Professional tenants on one App Service plan. Enterprise: the same
// template, one deployment per customer. The hosted demo: parameters/demo.bicepparam.
//
// What it creates: a Log Analytics workspace, a container registry (pulled with managed
// identities, no passwords), a Key Vault (access by roles) holding every secret, one Linux App
// Service plan with two apps on it running the same image: the API and the email worker. With
// `apiHostName`, the API's custom domain and a free managed certificate.
//
// Secrets never pass through this template or its parameters: the apps' settings are Key Vault
// references, and the owner sets each secret in the vault (infra/bicep/README.md). Supabase is
// not managed here.
//
// Two phases: first `deployApps = false` (registry, vault, logs, plan), then set the secrets and
// push the image, then `deployApps = true`.
targetScope = 'resourceGroup'

@description('Deployment tier.')
@allowed([
  'shared'
  'enterprise'
])
param tier string

@description('Short name used in resource names: "demo", "shared" or the Enterprise customer slug.')
@minLength(2)
@maxLength(12)
param tenantSlug string

@description('The API\'s ENVIRONMENT. Never local or test: those allow development shortcuts (TRD §6.11).')
@allowed([
  'demo'
  'production'
])
param environment string

@description('Azure region.')
param location string = resourceGroup().location

@description('Create the two apps. False on the first run: the vault, registry and logs must exist (and hold the secrets and the image) first.')
param deployApps bool = true

@description('Backend image tag in this deployment\'s registry, e.g. "2026-11-04.1". The same image runs the API and the worker.')
param imageTag string = ''

@description('App Service plan SKU.')
param appServiceSkuName string = tier == 'enterprise' ? 'P1v3' : 'B1'

@description('Supabase project URL (public).')
param supabaseUrl string

@description('Supabase anon key (public: the admin app ships it to every browser).')
param supabaseAnonKey string

@description('The admin app\'s URL (Stripe onboarding returns there; links in owner emails).')
param adminAppUrl string

@description('Origins allowed to call the admin API: exact origins, no wildcard.')
@minLength(1)
param corsOrigins array

@description('Domain customer and owner emails are sent from (verified in Resend).')
param emailFromDomain string

@description('Addresses of the App Service front end, trusted for X-Forwarded-For (TRD §6.10). Never "*" (the API refuses to start). Confirm on the deployed service.')
@minLength(1)
param forwardedAllowIps string

@description('The API\'s custom domain, e.g. api.deleajewelry.com. Empty on the first app deploy; set it once the CNAME and asuid TXT records exist.')
param apiHostName string = ''

@description('Deploy a Redis cache. The app does not use Redis today; keep false.')
param deployRedis bool = false

var namePrefix = 'delea-${tenantSlug}'
var compactSlug = replace(tenantSlug, '-', '')
var suffix = uniqueString(resourceGroup().id)
var tags = {
  product: 'delea'
  tier: tier
  tenant: tenantSlug
  environment: environment
}

// Built-in role ids (stable across tenants).
var acrPullRole = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '7f951dda-4ed3-4680-a7ca-43fe172d538d')
var keyVaultSecretsUserRole = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '4633458b-17de-408a-b874-0445c86b69e6')

resource logs 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: '${namePrefix}-logs'
  location: location
  tags: tags
  properties: {
    sku: {
      name: 'PerGB2018'
    }
    retentionInDays: 30
  }
}

resource registry 'Microsoft.ContainerRegistry/registries@2023-07-01' = {
  name: take('acrdelea${compactSlug}${suffix}', 50)
  location: location
  tags: tags
  sku: {
    name: 'Basic'
  }
  properties: {
    adminUserEnabled: false // pulls use the apps' managed identities
  }
}

resource vault 'Microsoft.KeyVault/vaults@2023-07-01' = {
  name: take('kv-${compactSlug}-${suffix}', 24)
  location: location
  tags: tags
  properties: {
    tenantId: subscription().tenantId
    sku: {
      family: 'A'
      name: 'standard'
    }
    enableRbacAuthorization: true
    enableSoftDelete: true
    softDeleteRetentionInDays: 90
    publicNetworkAccess: 'Enabled'
  }
}

module apps 'modules/appService.bicep' = if (deployApps) {
  name: '${namePrefix}-apps'
  params: {
    namePrefix: namePrefix
    location: location
    skuName: appServiceSkuName
    tags: tags
    image: '${registry.properties.loginServer}/delea-backend:${imageTag}'
    vaultName: vault.name
    logsId: logs.id
    environment: environment
    supabaseUrl: supabaseUrl
    supabaseAnonKey: supabaseAnonKey
    adminAppUrl: adminAppUrl
    corsOrigins: corsOrigins
    emailFromDomain: emailFromDomain
    forwardedAllowIps: forwardedAllowIps
    apiHostName: apiHostName
  }
}

// Each app's identity may pull from this registry and read this vault's secrets, nothing else.
// The names are fixed at compile time (from the app names), so a redeploy changes nothing.
var appNames = [
  '${namePrefix}-api'
  '${namePrefix}-worker'
]

resource acrPull 'Microsoft.Authorization/roleAssignments@2022-04-01' = [
  for (app, i) in appNames: if (deployApps) {
    name: guid(registry.id, app, 'AcrPull')
    scope: registry
    properties: {
      roleDefinitionId: acrPullRole
      principalId: deployApps ? apps!.outputs.principalIds[i] : ''
      principalType: 'ServicePrincipal'
    }
  }
]

resource secretsUser 'Microsoft.Authorization/roleAssignments@2022-04-01' = [
  for (app, i) in appNames: if (deployApps) {
    name: guid(vault.id, app, 'KeyVaultSecretsUser')
    scope: vault
    properties: {
      roleDefinitionId: keyVaultSecretsUserRole
      principalId: deployApps ? apps!.outputs.principalIds[i] : ''
      principalType: 'ServicePrincipal'
    }
  }
]

module redis 'modules/redis.bicep' = if (deployRedis) {
  name: '${namePrefix}-redis'
  params: {
    name: '${namePrefix}-redis'
    location: location
    skuName: tier == 'enterprise' ? 'Standard' : 'Basic'
    tags: tags
  }
}

output registryLoginServer string = registry.properties.loginServer
output keyVaultName string = vault.name
output apiDefaultHostName string = deployApps ? apps!.outputs.apiDefaultHostName : ''
output workerDefaultHostName string = deployApps ? apps!.outputs.workerDefaultHostName : ''
@description('Value of the asuid.<api host> TXT record that proves the custom domain is ours.')
output customDomainVerificationId string = deployApps ? apps!.outputs.customDomainVerificationId : ''
