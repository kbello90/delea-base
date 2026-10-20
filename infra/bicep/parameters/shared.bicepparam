// Essential + Professional tiers: one shared deployment.
// Secrets are read from environment variables at deploy time; never commit them here.
using '../main.bicep'

param tier = 'shared'
param tenantSlug = 'shared'
param containerImage = readEnvironmentVariable('DELEA_BACKEND_IMAGE', '')
param deployRedis = false

param supabaseUrl = readEnvironmentVariable('SUPABASE_URL', '')
param supabaseAnonKey = readEnvironmentVariable('SUPABASE_ANON_KEY', '')
param supabaseServiceRoleKey = readEnvironmentVariable('SUPABASE_SERVICE_ROLE_KEY', '')
param databaseUrl = readEnvironmentVariable('DATABASE_URL', '')
