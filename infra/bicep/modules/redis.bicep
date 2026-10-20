// Azure Cache for Redis. Optional per tier (TRD §1.2, §2.1).
// Shared tier keys are namespaced tenant:{id}:*; Enterprise gets a dedicated instance.

param name string
param location string

@allowed([
  'Basic'
  'Standard'
  'Premium'
])
param skuName string = 'Basic'

param capacity int = 0
param tags object = {}

resource redis 'Microsoft.Cache/redis@2024-03-01' = {
  name: name
  location: location
  tags: tags
  properties: {
    sku: {
      name: skuName
      family: skuName == 'Premium' ? 'P' : 'C'
      capacity: capacity
    }
    enableNonSslPort: false
    minimumTlsVersion: '1.2'
  }
}

output hostName string = redis.properties.hostName
