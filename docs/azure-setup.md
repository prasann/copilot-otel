# Azure setup

Provision the shared dashboard and ingestion resources, then enable
[telemetry](telemetry.md) or [daily coaching](coaching.md) separately.
Provisioning alone starts no collection or model calls.

The [Bicep template](../infra/main.bicep) creates Log Analytics, Entra-only
Application Insights, separate trace/coaching ingestion rules, a trace
collection endpoint, scoped user roles, and an Azure Monitor dashboard with
Grafana. It creates no model deployment, dedicated Azure Managed Grafana
instance, app registration, or credentials.

## 1. Choose a subscription and region

Use an environment and resource group dedicated to this project. Choose a
monitoring region that supports Log Analytics, Application Insights, and the
dashboard resource. The coaching model may use another region.

Set a small Azure budget and alerts. Alerts are not a spending hard stop;
ingestion, retention, and model inference can incur charges. The Azure Monitor
dashboard experience has no additional dashboard charge.

Resource deployment needs appropriate permissions. Contributor alone cannot
create role assignments; provisioning RBAC also needs a role such as Role Based
Access Control Administrator at the required scope. Keep grants scoped to the
project's resources.

The baseline uses public TLS endpoints with Entra/RBAC, not private networking.
If you require network isolation, plan the Mac's ingestion and inference access
before provisioning.

## 2. Authenticate locally

Install [Azure CLI](https://learn.microsoft.com/cli/azure/install-azure-cli) and
[Azure Developer CLI](https://learn.microsoft.com/azure/developer/azure-developer-cli/install-azd).
From the repository root:

```bash
az login --tenant <tenant-id>
az account set --subscription <subscription-id>
az account show --query '{subscription:id,tenant:tenantId,name:name}' -o json
azd auth login --tenant-id <tenant-id>

azd env new copilot-pilot
azd env set AZURE_SUBSCRIPTION_ID <subscription-id>
azd env set AZURE_LOCATION <monitoring-region>
```

Replace placeholders before running commands. Use `azd env select` instead of
`azd env new` for an existing environment. Azure CLI and `azd` have separate
authentication contexts; check that both use the intended tenant/subscription.

The template obtains `AZURE_PRINCIPAL_ID` from the signed-in `azd` user and
assigns that user:

| Resource | Role |
| --- | --- |
| Log Analytics workspace | Log Analytics Reader |
| Application Insights and dashboard | Reader |
| Each ingestion rule | Monitoring Metrics Publisher |

This setup targets an interactive user, not a service principal/CI identity.
Inherited subscription/resource-group permissions still apply. Dashboard
editing needs separate write permissions; Reader does not grant them.

The local `.azure/` directory is ignored. Do not commit it or share environment
dumps, tokens, or connection strings.

## 3. Prepare a model for coaching

Skip this step if you only want telemetry. Use an existing Azure OpenAI
deployment with strict JSON-schema output on the Responses API.

Grant your Azure CLI identity **Cognitive Services OpenAI User** on the model
resource. Confirm the inference endpoint is reachable from this Mac; a private
endpoint needs the appropriate VPN/private DNS route. A portal URL is not an
inference endpoint.

Copy [.env.example](../.env.example) to `.env` if it does not already exist:

```dotenv
AZURE_OPENAI_ENDPOINT=<https-resource-origin-without-openai-suffix>
AZURE_OPENAI_DEPLOYMENT=<deployment-name>
COACHING_MODEL_ID=<actual-model-id>
COACHING_AZURE_AUTH=azure_cli
```

Do not add an API key or store an access token. Set suitable deployment
rate/token limits; these are not monetary caps. Filling `.env` starts nothing.
See [coaching data boundaries](coaching.md#data-boundaries) before enabling runs.

## 4. Preview and provision

Confirm **Azure Monitor > Dashboards with Grafana** is available in your
tenant/subscription. Keep it Entra-authenticated; do not enable anonymous or
public sharing.

```bash
az bicep build --file infra/main.bicep --stdout > /dev/null
azd provision --preview
```

If Bicep is missing, run `az bicep install`. Preview needs Azure access and may
require registration of `Microsoft.OperationalInsights`, `Microsoft.Insights`,
and `Microsoft.Dashboard`; contact a subscription administrator if denied.

Review the resource group (`rg-<environment-name>`), region, changes, and roles:

| Resource | Expected configuration |
| --- | --- |
| Workspace | 30-day default retention; shared-key access disabled |
| Application Insights | Workspace-based; Entra-only ingestion |
| `CopilotInsights_CL` | 90-day retention; typed fields and explicit projection |
| Trace endpoint and rule | Native OTLP traces; same monitoring region; separate scoped publisher role |
| Dashboard | `Microsoft.Dashboard/dashboards`, not `Microsoft.Dashboard/grafana` |

No existing/shared model should change, and no subscription-wide role grants
should be proposed. If the preview matches your intent:

```bash
azd provision
```

The [dashboard definition](../dashboards/copilot-pilot.json) is deployed by
Bicep through its `dashboardDefinitions` child resource, not imported manually.
Local edits do not update a deployed dashboard.

## 5. Enable and verify

Follow [telemetry setup](telemetry.md) and/or [coaching setup](coaching.md#setup).
Use controlled real-client sessions to check exact project identities and the
absence of prohibited content in Azure. For coaching, run once and use
`tools.coaching verify <run-id>` to query back the rows.

Open the dashboard and check rendering, datasource access, project/source/time
filters, and recent data. Confirm an authorized reader can query it and an
unauthorized identity cannot. Local health checks or compilation do not prove
Azure delivery or access control.

Synthetic uploads are optional diagnostics for a specific failure, not routine
activity. See [troubleshooting](troubleshooting.md#manual-synthetic-diagnostics).

## Outputs and updates

Useful non-secret outputs from the selected environment:

```bash
azd env get-value AZURE_RESOURCE_GROUP
azd env get-value APPLICATIONINSIGHTS_RESOURCE_ID
azd env get-value LOG_ANALYTICS_WORKSPACE_ID
azd env get-value GRAFANA_DASHBOARD_RESOURCE_ID
azd env get-value INSIGHTS_DCR_IMMUTABLE_ID
azd env get-value INSIGHTS_LOGS_INGESTION_ENDPOINT
azd env get-value INSIGHTS_STREAM_NAME
azd env get-value TELEMETRY_TRACES_ENDPOINT
azd env get-value TELEMETRY_DCR_RESOURCE_ID
```

`LOG_ANALYTICS_WORKSPACE_ID` is the ARM resource ID;
`LOG_ANALYTICS_WORKSPACE_CUSTOMER_ID` is the query GUID. Native trace tables are
`OTelSpans`, `OTelEvents`, and `OTelResources`, not the legacy Application
Insights request/dependency tables.

Use the same `azd` environment for later updates and review each preview.
After reprovisioning, re-run `tools.coaching enable --execute` to refresh its
cached ingestion settings. Stop the collector before reinstalling it to apply
collector configuration changes.

Incremental deployments do not remove obsolete resources. Review any older
resources separately. Only run `azd down` after confirming the selected
environment contains exclusively disposable project resources; never use it
on shared infrastructure or an environment containing a shared model.
Cloud retention does not delete native Copilot history.

## References

- [Azure Developer CLI commands](https://learn.microsoft.com/azure/developer/azure-developer-cli/reference)
- [Azure OpenAI role-based access](https://learn.microsoft.com/azure/ai-foundry/openai/how-to/role-based-access-control)
- [Azure Monitor dashboards with Grafana](https://learn.microsoft.com/azure/azure-monitor/visualize/visualize-grafana-overview)
- [Dashboard ARM deployment](https://learn.microsoft.com/azure/azure-monitor/visualize/visualize-use-grafana-dashboards#manage-a-dashboard-as-an-arm-template)
- [Native OTLP ingestion](https://learn.microsoft.com/azure/azure-monitor/containers/opentelemetry-protocol-ingestion)
