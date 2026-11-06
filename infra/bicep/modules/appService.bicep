// One Linux App Service plan with two apps running the same backend image (hosted-demo plan
// step 3): the API (uvicorn, the image's default command) and the email worker
// (`python -m app.worker`, which answers GET /health on port 8000 because App Service only keeps
// containers that answer HTTP; app/worker_health.py).
//
// Every setting the code reads is listed here or, with the reason, in
// backend/tests/test_bicep_settings.py, which fails when the two drift apart. Secrets are Key
// Vault references (kvRef): their values live only in the vault.

param namePrefix string
param location string
param skuName string
param tags object = {}
param image string
param vaultName string
param logsId string
param environment string
param supabaseUrl string
param supabaseAnonKey string
param adminAppUrl string
param corsOrigins array
param emailFromDomain string
param forwardedAllowIps string
param apiHostName string = ''
param apiHostNameFirstBind bool = false
param emailHookOn bool = false

// App Service resolves this at runtime with the app's identity. A secret that is missing or
// unreadable reaches the app as this text, and the API and worker then refuse to start
// (backend/app/core/config.py).
func kvRef(vault string, secret string) string => '@Microsoft.KeyVault(VaultName=${vault};SecretName=${secret})'

var apiSettings = [
  { name: 'WEBSITES_PORT', value: '8000' }
  { name: 'ENVIRONMENT', value: environment }
  { name: 'SUPABASE_URL', value: supabaseUrl }
  { name: 'SUPABASE_ANON_KEY', value: supabaseAnonKey }
  { name: 'SUPABASE_SERVICE_ROLE_KEY', value: kvRef(vaultName, 'supabase-service-role-key') }
  { name: 'DATABASE_URL', value: kvRef(vaultName, 'database-url') }
  { name: 'DB_POOL_MIN_SIZE', value: '1' }
  { name: 'DB_POOL_MAX_SIZE', value: '5' }
  { name: 'STRIPE_SECRET_KEY', value: kvRef(vaultName, 'stripe-secret-key') }
  { name: 'STRIPE_WEBHOOK_SECRET', value: kvRef(vaultName, 'stripe-webhook-secret') }
  { name: 'STRIPE_GATEWAY', value: 'stripe' }
  { name: 'ADMIN_APP_URL', value: adminAppUrl }
  { name: 'EMAIL_PROVIDER', value: 'resend' }
  { name: 'EMAIL_FROM_DOMAIN', value: emailFromDomain }
  { name: 'RESEND_API_KEY', value: kvRef(vaultName, 'resend-api-key') }
  { name: 'RESEND_WEBHOOK_SECRET', value: kvRef(vaultName, 'resend-webhook-secret') }
  { name: 'TURNSTILE_SECRET_KEY', value: kvRef(vaultName, 'turnstile-secret-key') }
  { name: 'CORS_ORIGINS', value: join(corsOrigins, ',') }
  { name: 'FORWARDED_ALLOW_IPS', value: forwardedAllowIps }
]

// Supabase Auth's Send Email Hook (TRD §6.6), API only, added once the hook exists: the secret
// is created in Supabase when the hook is configured, after the API is deployed.
var emailHookSettings = emailHookOn ? [
  { name: 'SEND_EMAIL_HOOK_SECRET', value: kvRef(vaultName, 'send-email-hook-secret') }
] : []

var workerSettings = [
  { name: 'WEBSITES_PORT', value: '8000' }
  { name: 'WORKER_HEALTH_PORT', value: '8000' }
  { name: 'ENVIRONMENT', value: environment }
  { name: 'DATABASE_URL', value: kvRef(vaultName, 'database-url') }
  { name: 'DB_POOL_MIN_SIZE', value: '1' }
  { name: 'DB_POOL_MAX_SIZE', value: '2' }
  { name: 'ADMIN_APP_URL', value: adminAppUrl }
  { name: 'EMAIL_PROVIDER', value: 'resend' }
  { name: 'EMAIL_FROM_DOMAIN', value: emailFromDomain }
  { name: 'RESEND_API_KEY', value: kvRef(vaultName, 'resend-api-key') }
]

var commonSiteConfig = {
  linuxFxVersion: 'DOCKER|${image}'
  acrUseManagedIdentityCreds: true
  alwaysOn: true
  ftpsState: 'Disabled'
  minTlsVersion: '1.2'
  http20Enabled: true
  // Liveness only: /health doesn't touch the database, so a database outage never restarts the
  // API in a loop. /health/ready is the deploy smoke test (README).
  healthCheckPath: '/health'
}

resource plan 'Microsoft.Web/serverfarms@2023-12-01' = {
  name: '${namePrefix}-plan'
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

resource api 'Microsoft.Web/sites@2023-12-01' = {
  name: '${namePrefix}-api'
  location: location
  tags: tags
  kind: 'app,linux,container'
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    serverFarmId: plan.id
    httpsOnly: true
    clientAffinityEnabled: false
    keyVaultReferenceIdentity: 'SystemAssigned'
    siteConfig: union(commonSiteConfig, {
      appSettings: concat(apiSettings, emailHookSettings)
    })
  }
}

resource worker 'Microsoft.Web/sites@2023-12-01' = {
  name: '${namePrefix}-worker'
  location: location
  tags: tags
  kind: 'app,linux,container'
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    serverFarmId: plan.id
    httpsOnly: true
    clientAffinityEnabled: false
    keyVaultReferenceIdentity: 'SystemAssigned'
    siteConfig: union(commonSiteConfig, {
      appCommandLine: 'python -m app.worker'
      appSettings: workerSettings
      // Nobody needs to reach the worker from the internet: every public request is refused.
      // The platform's start-up ping and health check reach the container directly.
      ipSecurityRestrictionsDefaultAction: 'Deny'
    })
  }
}

// No FTP or basic-auth deployment credentials on either app.
resource apiFtp 'Microsoft.Web/sites/basicPublishingCredentialsPolicies@2023-12-01' = {
  parent: api
  name: 'ftp'
  properties: { allow: false }
}

resource apiScm 'Microsoft.Web/sites/basicPublishingCredentialsPolicies@2023-12-01' = {
  parent: api
  name: 'scm'
  properties: { allow: false }
}

resource workerFtp 'Microsoft.Web/sites/basicPublishingCredentialsPolicies@2023-12-01' = {
  parent: worker
  name: 'ftp'
  properties: { allow: false }
}

resource workerScm 'Microsoft.Web/sites/basicPublishingCredentialsPolicies@2023-12-01' = {
  parent: worker
  name: 'scm'
  properties: { allow: false }
}

var logCategories = [
  'AppServiceHTTPLogs'
  'AppServiceConsoleLogs'
  'AppServiceAppLogs'
  'AppServicePlatformLogs'
]

resource apiLogs 'Microsoft.Insights/diagnosticSettings@2021-05-01-preview' = {
  name: 'to-log-analytics'
  scope: api
  properties: {
    workspaceId: logsId
    logs: [for category in logCategories: { category: category, enabled: true }]
  }
}

resource workerLogs 'Microsoft.Insights/diagnosticSettings@2021-05-01-preview' = {
  name: 'to-log-analytics'
  scope: worker
  properties: {
    workspaceId: logsId
    logs: [for category in logCategories: { category: category, enabled: true }]
  }
}

// The API's custom domain: bound first without TLS (needs the CNAME and the asuid TXT record),
// then a free App Service managed certificate, then the binding again with SNI TLS.
// The binding without TLS is declared only on the first bind (apiHostNameFirstBind): on any
// later deploy it would replace the live TLS binding until the TLS step put it back, so every
// redeploy would briefly drop TLS on the domain. Later deploys re-declare only the certificate
// (unchanged) and the TLS binding (the same thumbprint).
resource apiDomain 'Microsoft.Web/sites/hostNameBindings@2023-12-01' = if (!empty(apiHostName) && apiHostNameFirstBind) {
  parent: api
  name: empty(apiHostName) ? 'unused' : apiHostName
  properties: {
    siteName: api.name
    hostNameType: 'Verified'
    sslState: 'Disabled'
  }
}

resource apiCertificate 'Microsoft.Web/certificates@2023-12-01' = if (!empty(apiHostName)) {
  name: '${namePrefix}-api-cert'
  location: location
  tags: tags
  properties: {
    serverFarmId: plan.id
    canonicalName: apiHostName
  }
  dependsOn: [
    apiDomain
  ]
}

module apiTls 'hostNameTls.bicep' = if (!empty(apiHostName)) {
  name: '${namePrefix}-api-tls'
  params: {
    siteName: api.name
    hostName: apiHostName
    thumbprint: empty(apiHostName) ? '' : apiCertificate!.properties.thumbprint
  }
}

output principalIds array = [
  api.identity.principalId
  worker.identity.principalId
]
output apiDefaultHostName string = api.properties.defaultHostName
output workerDefaultHostName string = worker.properties.defaultHostName
output customDomainVerificationId string = api.properties.customDomainVerificationId
