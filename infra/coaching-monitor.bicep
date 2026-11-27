targetScope = 'resourceGroup'

@description('Existing workspace name; no dashboard or core infrastructure is redeployed.')
param workspaceName string
@description('Existing coaching publisher user object ID.')
param publisherId string
@description('Daily status email destination. Required; configure privately, never commit it.')
@secure()
@minLength(3)
param recipient string
param location string = resourceGroup().location
@description('ACS geography; only operational status is sent, never report contents.')
param emailDataLocation string = 'India'
param name string = 'coaching-status'
@description('Leave disabled until live testing is ready.')
param enabled bool = false
@description('IST schedule anchor without a Z suffix. Use a past midnight so Azure does not skip a future start less than 24 hours away.')
param firstRun string = utcNow('yyyy-MM-ddT00:00:00')
@allowed(['', 'received', 'missing/failed', 'unknown'])
param testOutcome string = ''

var suffix = uniqueString(resourceGroup().id, name)
var columns = [
  { name: 'TimeGenerated', type: 'datetime' }
  { name: 'RunId', type: 'string' }
  { name: 'RunDay', type: 'string' }
  { name: 'Status', type: 'string' }
  { name: 'ExpectedItems', type: 'int' }
]

resource workspace 'Microsoft.OperationalInsights/workspaces@2023-09-01' existing = {
  name: workspaceName
}
resource runs 'Microsoft.OperationalInsights/workspaces/tables@2022-10-01' = {
  parent: workspace
  name: 'CopilotCoachingRuns_CL'
  properties: {
    plan: 'Analytics'
    retentionInDays: 30
    totalRetentionInDays: 30
    schema: { name: 'CopilotCoachingRuns_CL', columns: columns }
  }
}
resource rule 'Microsoft.Insights/dataCollectionRules@2024-03-11' = {
  name: '${name}-runs'
  location: location
  kind: 'Direct'
  properties: {
    streamDeclarations: {
      'Custom-CopilotCoachingRunInput': { columns: columns }
    }
    destinations: {
      logAnalytics: [{ name: 'workspace', workspaceResourceId: workspace.id }]
    }
    dataFlows: [{
      streams: ['Custom-CopilotCoachingRunInput']
      destinations: ['workspace']
      outputStream: 'Custom-CopilotCoachingRuns_CL'
      transformKql: 'source | project TimeGenerated, RunId, RunDay, Status, ExpectedItems'
      captureOverflow: false
    }]
  }
  dependsOn: [runs]
}
resource publisher 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: rule
  name: guid(rule.id, publisherId, 'publisher')
  properties: {
    principalId: publisherId
    principalType: 'User'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '3913510d-42f4-4e42-8a64-420c390055eb')
  }
}

resource email 'Microsoft.Communication/emailServices@2023-03-31' = {
  name: '${name}-email'
  location: 'global'
  properties: { dataLocation: emailDataLocation }
}
resource domain 'Microsoft.Communication/emailServices/domains@2023-03-31' = {
  parent: email
  name: 'AzureManagedDomain'
  location: 'global'
  properties: {
    domainManagement: 'AzureManaged'
    userEngagementTracking: 'Disabled'
  }
}
resource communication 'Microsoft.Communication/communicationServices@2023-03-31' = {
  name: '${name}-${suffix}'
  location: 'global'
  properties: {
    dataLocation: emailDataLocation
    linkedDomains: [domain.id]
  }
}
resource app 'Microsoft.Logic/workflows@2019-05-01' = {
  name: '${name}-daily'
  location: location
  identity: { type: 'SystemAssigned' }
  properties: {
    state: enabled ? 'Enabled' : 'Disabled'
    definition: loadJsonContent('../monitoring/workflow.json')
    parameters: {
      workspace: { value: workspace.properties.customerId }
      endpoint: { value: 'https://${communication.properties.hostName}' }
      sender: { value: 'DoNotReply@${domain.properties.mailFromSenderDomain}' }
      recipient: { value: recipient }
      query: { value: loadTextContent('../monitoring/status.kql') }
      firstRun: { value: firstRun }
      testOutcome: { value: testOutcome }
    }
  }
}
resource reader 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: workspace
  name: guid(workspace.id, app.id, 'reader')
  properties: {
    principalId: app.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '73c42c96-874c-492b-b04d-ab87d138a893')
  }
}
// ACS email Entra authentication requires read/write on this resource.
resource sender 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: communication
  name: guid(communication.id, app.id, 'sender')
  properties: {
    principalId: app.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'b24988ac-6180-42a0-ab88-20f7382dd24c')
  }
}
resource deliveryLogs 'Microsoft.Insights/diagnosticSettings@2021-05-01-preview' = {
  scope: communication
  name: 'email-delivery'
  properties: {
    workspaceId: workspace.id
    logAnalyticsDestinationType: 'Dedicated'
    logs: [
      { category: 'EmailSendMailOperational', enabled: true }
      { category: 'EmailStatusUpdateOperational', enabled: true }
    ]
  }
}

output workflowName string = app.name
output communicationName string = communication.name
output statusEndpoint string = rule.properties.endpoints.logsIngestion
output statusRule string = rule.properties.immutableId
