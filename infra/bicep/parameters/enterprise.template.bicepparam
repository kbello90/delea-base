// Enterprise tier template. Copy to enterprise.<customer>.bicepparam per Enterprise tenant
// and set tenantSlug. Secrets come from the customer's dedicated Supabase project via
// environment variables at deploy time; never commit them here.
using '../main.bicep'

param tier = 'enterprise'
param tenantSlug = 'customer-slug'
param containerImage = readEnvironmentVariable('DELEA_BACKEND_IMAGE', '')
param deployRedis = true

param supabaseUrl = readEnvironmentVariable('SUPABASE_URL', '')
param supabaseAnonKey = readEnvironmentVariable('SUPABASE_ANON_KEY', '')
param supabaseServiceRoleKey = readEnvironmentVariable('SUPABASE_SERVICE_ROLE_KEY', '')
param databaseUrl = readEnvironmentVariable('DATABASE_URL', '')
