// DELEA backend infrastructure: one template for every tier (TRD §2.1).
// Shared tier: Essential + Professional tenants on one App Service.
// Enterprise tier: same template, one deployment per customer.
// Supabase is not managed here; its URL, keys and connection string come in as secure params.
targetScope = 'resourceGroup'

@description('Deployment tier.')
@allowed([
  'shared'
  'enterprise'
])
param tier string

@description('Short name used in resource names: "shared" or the Enterprise customer slug.')
@minLength(2)
@maxLength(20)
param tenantSlug string

@description('Azure region.')
param location string = resourceGroup().location

@description('Backend container image, e.g. <registry>.azurecr.io/delea-backend:<tag>. Same image for every tier.')
param containerImage string

@description('Deploy a Redis cache alongside the App Service.')
param deployRedis bool = false

@description('App Service plan SKU.')
param appServiceSkuName string = tier == 'enterprise' ? 'P1v3' : 'B1'

@secure()
param supabaseUrl string

@secure()
param supabaseAnonKey string

@secure()
param supabaseServiceRoleKey string

@secure()
param databaseUrl string

var namePrefix = 'delea-${tenantSlug}'
var tags = {
  product: 'delea'
  tier: tier
  tenant: tenantSlug
}

module appService 'modules/appService.bicep' = {
  name: '${namePrefix}-app'
  params: {
    name: '${namePrefix}-api'
    location: location
    skuName: appServiceSkuName
    containerImage: containerImage
    supabaseUrl: supabaseUrl
    supabaseAnonKey: supabaseAnonKey
    supabaseServiceRoleKey: supabaseServiceRoleKey
    databaseUrl: databaseUrl
    tags: tags
  }
}

module redis 'modules/redis.bicep' = if (deployRedis) {
  name: '${namePrefix}-redis'
  params: {
    name: '${namePrefix}-redis'
    location: location
    skuName: tier == 'enterprise' ? 'Standard' : 'Basic'
    tags: tags
  }
}

output appServiceHostName string = appService.outputs.defaultHostName
