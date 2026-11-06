// Essential + Professional tiers: one shared production deployment. Not deployed yet; the
// hosted demo is parameters/demo.bicepparam. Secrets are never here: they go into the Key Vault
// (infra/bicep/README.md).
using '../main.bicep'

param tier = 'shared'
param tenantSlug = 'shared'
param environment = 'production'
param deployApps = bool(readEnvironmentVariable('DELEA_DEPLOY_APPS', 'false'))
param imageTag = readEnvironmentVariable('DELEA_IMAGE_TAG', '')

param supabaseUrl = readEnvironmentVariable('SUPABASE_URL')
param supabaseAnonKey = readEnvironmentVariable('SUPABASE_ANON_KEY')
param adminAppUrl = readEnvironmentVariable('DELEA_ADMIN_APP_URL')
param corsOrigins = [
  readEnvironmentVariable('DELEA_ADMIN_APP_URL')
]
param emailFromDomain = 'mail.deleajewelry.com'
param forwardedAllowIps = readEnvironmentVariable('DELEA_FORWARDED_ALLOW_IPS')
param apiHostName = readEnvironmentVariable('DELEA_API_HOST_NAME', '')
param apiHostNameFirstBind = bool(readEnvironmentVariable('DELEA_API_HOST_FIRST_BIND', 'false'))
param emailHookOn = bool(readEnvironmentVariable('DELEA_EMAIL_HOOK', 'false'))
