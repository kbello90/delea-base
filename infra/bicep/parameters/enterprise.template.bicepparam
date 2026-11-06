// Enterprise tier template. Copy to enterprise.<customer>.bicepparam per Enterprise tenant and
// set tenantSlug. Its Supabase project is the customer's own; secrets go into this
// deployment's Key Vault (infra/bicep/README.md), never into this file.
using '../main.bicep'

param tier = 'enterprise'
param tenantSlug = 'customer'
param environment = 'production'
param deployApps = bool(readEnvironmentVariable('DELEA_DEPLOY_APPS', 'false'))
param imageTag = readEnvironmentVariable('DELEA_IMAGE_TAG', '')
param deployRedis = false // the app does not use Redis today

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
