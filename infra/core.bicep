@description('Azure region for all resources.')
param location string

@description('Tags applied to every resource.')
param tags object

@description('Principal ID that should receive Grafana Admin. Empty skips the assignment.')
param principalId string = ''

param logAnalyticsName string
param appInsightsName string
param grafanaName string

// -----------------------------
// Log Analytics workspace
// -----------------------------
resource logAnalytics 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: logAnalyticsName
  location: location
  tags: tags
  properties: {
    sku: {
      name: 'PerGB2018'
    }
    retentionInDays: 30
    features: {
      enableLogAccessUsingOnlyResourcePermissions: true
    }
  }
}

// -----------------------------
// Application Insights (workspace-based)
// -----------------------------
resource appInsights 'Microsoft.Insights/components@2020-02-02' = {
  name: appInsightsName
  location: location
  tags: tags
  kind: 'web'
  properties: {
    Application_Type: 'web'
    WorkspaceResourceId: logAnalytics.id
    IngestionMode: 'LogAnalytics'
    publicNetworkAccessForIngestion: 'Enabled'
    publicNetworkAccessForQuery: 'Enabled'
  }
}

// -----------------------------
// Azure Managed Grafana
// -----------------------------
resource grafana 'Microsoft.Dashboard/grafana@2023-09-01' = {
  name: grafanaName
  location: location
  tags: tags
  sku: {
    name: 'Standard'
  }
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    apiKey: 'Enabled'
    deterministicOutboundIP: 'Disabled'
    publicNetworkAccess: 'Enabled'
    zoneRedundancy: 'Disabled'
  }
}

// -----------------------------
// Role assignments
// -----------------------------
// Monitoring Reader for Grafana MSI so it can query App Insights / Log Analytics
var monitoringReaderRoleId = subscriptionResourceId(
  'Microsoft.Authorization/roleDefinitions',
  '43d0d8ad-25c7-4714-9337-8ba259a9fe05'
)

resource grafanaMonitoringReader 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(resourceGroup().id, grafana.id, 'Monitoring Reader')
  scope: resourceGroup()
  properties: {
    principalId: grafana.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: monitoringReaderRoleId
  }
}

// Grafana Admin for the deploying user
var grafanaAdminRoleId = subscriptionResourceId(
  'Microsoft.Authorization/roleDefinitions',
  '22926164-76b3-42b3-bc55-97df8dab3e41'
)

resource userGrafanaAdmin 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(principalId)) {
  name: guid(grafana.id, principalId, 'Grafana Admin')
  scope: grafana
  properties: {
    principalId: principalId
    principalType: 'User'
    roleDefinitionId: grafanaAdminRoleId
  }
}

output logAnalyticsId string = logAnalytics.id
output appInsightsName string = appInsights.name
output appInsightsConnectionString string = appInsights.properties.ConnectionString
output grafanaName string = grafana.name
output grafanaEndpoint string = grafana.properties.endpoint
