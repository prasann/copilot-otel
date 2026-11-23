@description('Azure region for all resources.')
param location string

@description('Tags applied to every resource.')
param tags object

@description('Object ID of the pilot user. No application credentials are created.')
param principalId string

param logAnalyticsName string
param appInsightsName string
param dashboardName string
param assessmentRuleName string

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

var assessmentColumns = [
  { name: 'TimeGenerated', type: 'datetime' }
  { name: 'SessionEndedAt', type: 'datetime' }
  { name: 'SchemaVersion', type: 'int' }
  { name: 'AssessmentId', type: 'string' }
  { name: 'SessionId', type: 'string' }
  { name: 'Revision', type: 'int' }
  { name: 'SnapshotSha256', type: 'string' }
  { name: 'ProjectId', type: 'string' }
  { name: 'ProjectName', type: 'string' }
  { name: 'ProjectCategory', type: 'string' }
  { name: 'Origin', type: 'string' }
  { name: 'Outcome', type: 'string' }
  { name: 'Summary', type: 'string' }
  { name: 'Limitations', type: 'string' }
]
var tableName = 'CopilotAssessments_CL'
var inputStream = 'Custom-CopilotAssessmentInput'

resource assessments 'Microsoft.OperationalInsights/workspaces/tables@2022-10-01' = {
  parent: logAnalytics
  name: tableName
  properties: {
    plan: 'Analytics'
    retentionInDays: 90
    totalRetentionInDays: 90
    schema: {
      name: tableName
      columns: assessmentColumns
    }
  }
}

resource assessmentRule 'Microsoft.Insights/dataCollectionRules@2024-03-11' = {
  name: assessmentRuleName
  location: location
  tags: tags
  kind: 'Direct'
  properties: {
    description: 'Derived assessments only. Publisher must validate content before sending.'
    streamDeclarations: {
      '${inputStream}': {
        columns: assessmentColumns
      }
    }
    destinations: {
      logAnalytics: [
        {
          name: 'assessments'
          workspaceResourceId: logAnalytics.id
        }
      ]
    }
    dataFlows: [
      {
        streams: [inputStream]
        destinations: ['assessments']
        outputStream: 'Custom-${tableName}'
        transformKql: 'source | project ${join(map(assessmentColumns, column => column.name), ', ')}'
        captureOverflow: false
      }
    ]
  }
  dependsOn: [assessments]
}

resource assessmentPublisher 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(assessmentRule.id, principalId, 'Monitoring Metrics Publisher')
  scope: assessmentRule
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

output logAnalyticsId string = logAnalytics.id
output logAnalyticsCustomerId string = logAnalytics.properties.customerId
output appInsightsName string = appInsights.name
output appInsightsId string = appInsights.id
output dashboardId string = dashboard.id
output assessmentRuleId string = assessmentRule.id
output assessmentRuleImmutableId string = assessmentRule.properties.immutableId
output assessmentIngestionEndpoint string = assessmentRule.properties.endpoints.logsIngestion
output assessmentStream string = inputStream
output assessmentTableName string = tableName
