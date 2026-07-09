targetScope = 'subscription'

@minLength(1)
@maxLength(64)
@description('Name of the environment used to generate resource names. Set via `azd env new`.')
param environmentName string

@minLength(1)
@description('Primary location for all resources.')
param location string

@description('Object ID of the user who should get Grafana Admin. Defaults to the azd principal.')
param principalId string = ''

var tags = {
  'azd-env-name': environmentName
  workload: 'copilot-otel'
}

var abbrs = {
  resourceGroup: 'rg'
  logAnalytics: 'log'
  appInsights: 'appi'
  grafana: 'amg'
}

var rgName = '${abbrs.resourceGroup}-${environmentName}'
var resourceToken = uniqueString(subscription().id, environmentName, location)

resource rg 'Microsoft.Resources/resourceGroups@2024-03-01' = {
  name: rgName
  location: location
  tags: tags
}

module core 'core.bicep' = {
  name: 'core'
  scope: rg
  params: {
    location: location
    tags: tags
    principalId: principalId
    logAnalyticsName: '${abbrs.logAnalytics}-${resourceToken}'
    appInsightsName: '${abbrs.appInsights}-${resourceToken}'
    grafanaName: '${abbrs.grafana}-${resourceToken}'
  }
}

output AZURE_LOCATION string = location
output AZURE_RESOURCE_GROUP string = rg.name
output APPLICATIONINSIGHTS_CONNECTION_STRING string = core.outputs.appInsightsConnectionString
output APPLICATIONINSIGHTS_NAME string = core.outputs.appInsightsName
output LOG_ANALYTICS_WORKSPACE_ID string = core.outputs.logAnalyticsId
output GRAFANA_ENDPOINT string = core.outputs.grafanaEndpoint
output GRAFANA_NAME string = core.outputs.grafanaName
