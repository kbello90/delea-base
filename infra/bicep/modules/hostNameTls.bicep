// Turns on SNI TLS for a host name already bound to the app, with the managed certificate's
// thumbprint. A separate module because the same binding is declared first without TLS (the
// certificate can only be issued for a bound host name).

param siteName string
param hostName string
param thumbprint string

resource site 'Microsoft.Web/sites@2023-12-01' existing = {
  name: siteName
}

resource binding 'Microsoft.Web/sites/hostNameBindings@2023-12-01' = {
  parent: site
  name: hostName
  properties: {
    siteName: siteName
    hostNameType: 'Verified'
    sslState: 'SniEnabled'
    thumbprint: thumbprint
  }
}
