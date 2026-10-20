// Linux App Service plan + container web app running the FastAPI backend image.

param name string
param location string
param skuName string
param containerImage string
param tags object = {}

@secure()
param supabaseUrl string

@secure()
param supabaseAnonKey string

@secure()
param supabaseServiceRoleKey string

@secure()
param databaseUrl string

resource plan 'Microsoft.Web/serverfarms@2023-12-01' = {
  name: '${name}-plan'
  location: location
  tags: tags
  kind: 'linux'
  sku: {
    name: skuName
  }
  properties: {
    reserved: true
  }
}

resource site 'Microsoft.Web/sites@2023-12-01' = {
  name: name
  location: location
  tags: tags
  kind: 'app,linux,container'
  properties: {
    serverFarmId: plan.id
    httpsOnly: true
    siteConfig: {
      linuxFxVersion: 'DOCKER|${containerImage}'
      alwaysOn: true
      ftpsState: 'Disabled'
      minTlsVersion: '1.2'
      healthCheckPath: '/health'
      appSettings: [
        {
          name: 'WEBSITES_PORT'
          value: '8000'
        }
        {
          name: 'SUPABASE_URL'
          value: supabaseUrl
        }
        {
          name: 'SUPABASE_ANON_KEY'
          value: supabaseAnonKey
        }
        {
          name: 'SUPABASE_SERVICE_ROLE_KEY'
          value: supabaseServiceRoleKey
        }
        {
          name: 'DATABASE_URL'
          value: databaseUrl
        }
      ]
    }
  }
}

output defaultHostName string = site.properties.defaultHostName
