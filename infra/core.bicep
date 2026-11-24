@description('Azure region for all resources.')
param location string

@description('Tags applied to every resource.')
param tags object

@description('Object ID of the pilot user. No application credentials are created.')
param principalId string

param logAnalyticsName string
param appInsightsName string
param dashboardName string
param ruleName string

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
      enableLogAccessUsingOnlyResourcePermissions: false
      disableLocalAuth: true
    }
  }
}

resource appInsights 'Microsoft.Insights/components@2020-02-02' = {
  name: appInsightsName
  location: location
  tags: tags
  kind: 'web'
  properties: {
    Application_Type: 'web'
    WorkspaceResourceId: logAnalytics.id
    IngestionMode: 'LogAnalytics'
    DisableLocalAuth: true
    publicNetworkAccessForIngestion: 'Enabled'
    publicNetworkAccessForQuery: 'Enabled'
  }
}

resource dashboard 'Microsoft.Dashboard/dashboards@2025-08-01' = {
  name: dashboardName
  location: location
  tags: union(tags, {
    GrafanaDashboardResourceType: 'microsoft.insights/components'
  })
}

resource dashboardDefinition 'Microsoft.Dashboard/dashboards/dashboardDefinitions@2025-09-01-preview' = {
  parent: dashboard
  name: 'default'
  properties: {
    serializedData: replace(
      loadTextContent('../dashboards/copilot-pilot.json'),
      '__WORKSPACE_RESOURCE_ID__',
      logAnalytics.id
    )
  }
}

var insightColumns = [
  { name: 'TimeGenerated', type: 'datetime' }
  { name: 'SchemaVersion', type: 'int' }
  { name: 'RunId', type: 'string' }
  { name: 'Kind', type: 'string' }
  { name: 'Rank', type: 'int' }
  { name: 'Category', type: 'string' }
  { name: 'Title', type: 'string' }
  { name: 'Detail', type: 'string' }
  { name: 'Repository', type: 'string' }
  { name: 'Source', type: 'string' }
  { name: 'ModelId', type: 'string' }
]
var tableName = 'CopilotInsights_CL'
var inputStream = 'Custom-CopilotInsightInput'

resource insights 'Microsoft.OperationalInsights/workspaces/tables@2022-10-01' = {
  parent: logAnalytics
  name: tableName
  properties: {
    plan: 'Analytics'
    retentionInDays: 90
    totalRetentionInDays: 90
    schema: {
      name: tableName
      columns: insightColumns
    }
  }
}

resource insightRule 'Microsoft.Insights/dataCollectionRules@2024-03-11' = {
  name: ruleName
  location: location
  tags: tags
  kind: 'Direct'
  properties: {
    description: 'Derived Copilot coaching items only. Publisher validates fields before sending.'
    streamDeclarations: {
      '${inputStream}': {
        columns: insightColumns
      }
    }
    destinations: {
      logAnalytics: [
        {
          name: 'insights'
          workspaceResourceId: logAnalytics.id
        }
      ]
    }
    dataFlows: [
      {
        streams: [inputStream]
        destinations: ['insights']
        outputStream: 'Custom-${tableName}'
        transformKql: 'source | project ${join(map(insightColumns, column => column.name), ', ')}'
        captureOverflow: false
      }
    ]
  }
  dependsOn: [insights]
}

resource insightPublisher 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(insightRule.id, principalId, 'Monitoring Metrics Publisher')
  scope: insightRule
  properties: {
    principalId: principalId
    principalType: 'User'
    roleDefinitionId: subscriptionResourceId(
      'Microsoft.Authorization/roleDefinitions',
      '3913510d-42f4-4e42-8a64-420c390055eb'
    )
  }
}

resource workspaceReader 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(logAnalytics.id, principalId, 'Log Analytics Reader')
  scope: logAnalytics
  properties: {
    principalId: principalId
    principalType: 'User'
    roleDefinitionId: subscriptionResourceId(
      'Microsoft.Authorization/roleDefinitions',
      '73c42c96-874c-492b-b04d-ab87d138a893'
    )
  }
}

resource dashboardReader 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(dashboard.id, principalId, 'Reader')
  scope: dashboard
  properties: {
    principalId: principalId
    principalType: 'User'
    roleDefinitionId: subscriptionResourceId(
      'Microsoft.Authorization/roleDefinitions',
      'acdd72a7-3385-48ef-bd42-f606fba81ae7'
    )
  }
}

resource appInsightsReader 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(appInsights.id, principalId, 'Reader')
  scope: appInsights
  properties: {
    principalId: principalId
    principalType: 'User'
    roleDefinitionId: subscriptionResourceId(
      'Microsoft.Authorization/roleDefinitions',
      'acdd72a7-3385-48ef-bd42-f606fba81ae7'
    )
  }
}

// Native OTLP HTTP acknowledges ingestion; avoid the legacy fire-and-forget exporter.
resource telemetryEndpoint 'Microsoft.Insights/dataCollectionEndpoints@2024-03-11' = {
  name: '${ruleName}-otlp'
  location: location
  tags: tags
  properties: {
    networkAcls: {
      publicNetworkAccess: 'Enabled'
    }
  }
}

var traceStreams = [
  'Microsoft-OTel-Traces-Spans'
  'Microsoft-OTel-Traces-Events'
  'Microsoft-OTel-Traces-Resources'
]

resource telemetryRule 'Microsoft.Insights/dataCollectionRules@2024-03-11' = {
  name: '${ruleName}-otlp'
  location: location
  tags: tags
  properties: {
    description: 'Metadata-only native OTLP traces; filtering occurs before local queue and upload.'
    dataCollectionEndpointId: telemetryEndpoint.id
    references: {
      applicationInsights: [
        {
          resourceId: appInsights.id
          name: 'application'
        }
      ]
    }
    dataSources: {
      otelTraces: [
        {
          streams: traceStreams
          enrichWithResourceAttributes: ['*']
          enrichWithReference: 'application'
          replaceResourceIdWithReference: true
          name: 'agent-traces'
        }
      ]
    }
    directDataSources: {
      otelTraces: [
        {
          streams: traceStreams
          enrichWithResourceAttributes: ['*']
          enrichWithReference: 'application'
          replaceResourceIdWithReference: true
          name: 'traces'
        }
      ]
    }
    destinations: {
      logAnalytics: [
        {
          workspaceResourceId: logAnalytics.id
          name: 'workspace'
        }
      ]
    }
    dataFlows: [
      {
        streams: traceStreams
        destinations: ['workspace']
      }
    ]
  }
}

resource telemetryPublisher 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(telemetryRule.id, principalId, 'Monitoring Metrics Publisher')
  scope: telemetryRule
  properties: {
    principalId: principalId
    principalType: 'User'
    roleDefinitionId: subscriptionResourceId(
      'Microsoft.Authorization/roleDefinitions',
      '3913510d-42f4-4e42-8a64-420c390055eb'
    )
  }
}

output logAnalyticsId string = logAnalytics.id
output logAnalyticsCustomerId string = logAnalytics.properties.customerId
output appInsightsName string = appInsights.name
output appInsightsId string = appInsights.id
output dashboardId string = dashboard.id
output insightRuleId string = insightRule.id
output insightRuleImmutableId string = insightRule.properties.immutableId
output insightIngestionEndpoint string = insightRule.properties.endpoints.logsIngestion
output insightStream string = inputStream
output insightTableName string = tableName
output telemetryTracesEndpoint string = '${telemetryEndpoint.properties.logsIngestion.endpoint}/datacollectionRules/${telemetryRule.properties.immutableId}/streams/Microsoft-OTLP-Traces/otlp/v1/traces'
output telemetryRuleId string = telemetryRule.id
