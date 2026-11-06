// The hosted demo (hosted-demo plan): one private demo deployment in East US, in its own
// resource group (rg-delea-demo). ENVIRONMENT=demo: like production for the code (no
// development shortcuts, Stripe test keys only, demo shops never email shoppers); "production"
// is kept for the day live Stripe keys are allowed (TRD §6.15).
//
// Nothing secret is here or in any variable below: secrets go straight into the Key Vault
// (infra/bicep/README.md). The variables without a default must be set, or the build fails.
using '../main.bicep'

param tier = 'shared'
param tenantSlug = 'demo'
param environment = 'demo'
param location = 'eastus'
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
