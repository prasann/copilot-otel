targetScope = 'subscription'

@minLength(1)
@maxLength(64)
@description('Name of the environment used to generate resource names. Set via `azd env new`.')
param environmentName string

@minLength(1)
@description('Primary location for all resources.')
param location string

@minLength(36)
@maxLength(36)
@description('Object ID of the signed-in azd user who will query data and publish assessments.')
param principalId string

var tags = {
  'azd-env-name': environmentName
  workload: 'copilot-otel'
}

var abbrs = {
  resourceGroup: 'rg'
  logAnalytics: 'log'
  appInsights: 'appi'
  dashboard: 'dash'
  assessmentRule: 'dcr'
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
    dashboardName: '${abbrs.dashboard}-${resourceToken}'
    assessmentRuleName: '${abbrs.assessmentRule}-${resourceToken}'
  }
}

output AZURE_LOCATION string = location
output AZURE_RESOURCE_GROUP string = rg.name
output APPLICATIONINSIGHTS_NAME string = core.outputs.appInsightsName
output APPLICATIONINSIGHTS_RESOURCE_ID string = core.outputs.appInsightsId
output LOG_ANALYTICS_WORKSPACE_ID string = core.outputs.logAnalyticsId
output LOG_ANALYTICS_WORKSPACE_CUSTOMER_ID string = core.outputs.logAnalyticsCustomerId
output GRAFANA_DASHBOARD_RESOURCE_ID string = core.outputs.dashboardId
output ASSESSMENT_DCR_RESOURCE_ID string = core.outputs.assessmentRuleId
output ASSESSMENT_DCR_IMMUTABLE_ID string = core.outputs.assessmentRuleImmutableId
output ASSESSMENT_LOGS_INGESTION_ENDPOINT string = core.outputs.assessmentIngestionEndpoint
output ASSESSMENT_STREAM_NAME string = core.outputs.assessmentStream
output ASSESSMENT_TABLE_NAME string = core.outputs.assessmentTableName
output TELEMETRY_TRACES_ENDPOINT string = core.outputs.telemetryTracesEndpoint
output TELEMETRY_DCR_RESOURCE_ID string = core.outputs.telemetryRuleId
