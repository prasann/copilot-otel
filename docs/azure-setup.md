# Azure pilot setup

## What you can do now

Azure deployment, including the earlier dashboard refresh, was confirmed complete
by the user on **23 November 2026**. For a new environment or an update, prepare
Azure access and the existing model deployment, then preview and provision the
monitoring baseline using section 5. Offline tests do not connect
to Azure. Optional manual synthetic diagnostics are documented separately in
[troubleshooting](troubleshooting.md#manual-synthetic-diagnostics); they are not
part of routine setup or operation. A native macOS collector supports metadata-only traces.
The [opt-in attribution gateway](../README.md#opt-in-project-attribution) is
running in the background for `personal/copilot-otel` only; the Insiders endpoint
is saved, and real-session attribution was queried back from Azure and
user-confirmed visible in the dashboard after a full Insiders restart.
The gateway does not restart automatically after logout/reboot. Its dashboard
diagnostic columns were deployed separately, preserving portal identity/title,
and definition read-back plus the updated Azure query passed. The full
`azd provision --preview` also proposed Application Insights property changes,
so that full plan was not applied at the time. Two-project live acceptance remains pending.
VS Code Insiders settings were configured globally.

On **24 November 2026** the coaching insights table and rule replaced the earlier
assessment table/stream. The full `what-if` showed only the intended changes plus
default Application Insights creation properties (`Flow_Type`, `Request_Source`),
so the full plan was applied. The first [daily coaching](coaching.md) run was
published and query-verified, and the daily job is installed.

Dashboard definitions and project/time-filtered KQL were verified through CLI.
Earlier dashboard rendering and datasource access were user-confirmed. The latest
attribution view was user-confirmed. The usage-first token layout was subsequently
deployed with live query and definition read-back verification; its rendering and
project/time filter interaction still need browser verification. Progress is tracked
in the [README status and pending checklist](../README.md#current-status).

[azure.yaml](../azure.yaml) uses [infra/main.bicep](../infra/main.bicep) and
[infra/core.bicep](../infra/core.bicep). They now provision Log Analytics,
workspace-based Application Insights, a coaching insights table/direct ingestion rule,
scoped user roles, and a portal Grafana dashboard with repo-backed initial panels.
Native trace ingestion adds a collection endpoint and separate telemetry DCR,
referencing the existing Application Insights/workspace. No Azure Monitor
metrics workspace is needed for this traces-only slice.
**No dedicated Azure Managed Grafana instance or model deployment is created.**

Target: repo-backed Bicep and `azd` for provisioning, Azure CLI authentication for
the coaching model call, and one authenticated Azure Monitor dashboard with Grafana
for both coaching and telemetry. No Docker requirement.

## 1. Choose the pilot subscription and region

- [ ] Choose the subscription and tenant you want to pay for/use.
- [ ] Choose an environment name, for example `copilot-pilot`.
- [ ] Choose a preferred monitoring region, subject to resource availability.
- [ ] Confirm the model deployment's region; it may differ from monitoring.
- [ ] Set a small Azure budget and alerts. Alerts are not a spending hard stop.

Resource creation needs appropriate deployment permissions. Contributor alone
cannot create role assignments: provisioning RBAC also needs an appropriate
role such as Role Based Access Control Administrator at the required scope.
We should scope grants to the pilot resources, not give runtime code broad
subscription permissions.

No need to manually create monitoring resources. Bicep creates these consistently.
Use a fresh pilot environment and resource group, not one containing shared
resources or the older deployment.

## 2. Authenticate locally

Install Azure CLI and Azure Developer CLI if needed, using their official
installation guides linked below. Run these from the repository root:

```bash
az version
azd version
az login --tenant <tenant-id>
az account set --subscription <subscription-id>
az account show --query '{subscription:id,tenant:tenantId,name:name}' -o json
azd auth login --tenant-id <tenant-id>
```

Replace angle-bracket placeholders before running commands. Azure CLI and `azd`
have separate authentication contexts; signing into one is not proof the other
has access to the intended subscription.

Optionally prepare a local `azd` environment now; these commands do not provision
resources. If the environment already exists, use `azd env select` instead:

```bash
azd env new copilot-pilot
azd env set AZURE_SUBSCRIPTION_ID <subscription-id>
azd env set AZURE_LOCATION <monitoring-region>
```

The template obtains `AZURE_PRINCIPAL_ID` from the signed-in `azd` user. It
assigns that user Log Analytics Reader on the workspace, Reader on Application
Insights and the dashboard, and Monitoring Metrics Publisher separately on the
coaching and telemetry data collection rules. The current pilot targets an interactive user, not a
service principal/CI identity. It creates no app registrations or credentials.

The local `.azure/` directory is ignored. Do not commit it or dump its contents
into an issue/chat: deployment outputs can contain connection strings.

## 3. Prepare the existing Azure model deployment

- [ ] Confirm the deployment exists and is available to this signed-in account.
- [ ] Record its resource endpoint, deployment name, actual model ID and version.
- [ ] Record its resource ID and endpoint/API type so we can implement the correct
  API and token audience. A portal URL is not an inference endpoint.
- [ ] For an Azure OpenAI resource, grant your CLI identity
  **Cognitive Services OpenAI User** on that resource for inference. If this is
  another Foundry endpoint type, confirm its corresponding data-plane role;
  do not assume the same role/audience works everywhere.
- [ ] Ensure the endpoint is reachable from this Mac. If it is private-network
  only, confirm the required VPN/private DNS route instead of opening it publicly.
- [ ] Set deployment rate/token limits appropriate to the approved workload.
  These limits are not monetary caps.

Create a local `.env` from [.env.example](../.env.example) only if one does not
already exist. Fill these values locally:

```dotenv
AZURE_OPENAI_ENDPOINT=<resource-inference-endpoint>
AZURE_OPENAI_DEPLOYMENT=<deployment-name>
COACHING_MODEL_ID=<actual-model-id>
COACHING_AZURE_AUTH=azure_cli
```

Do not add an API key or store an access token. Filling this file starts
nothing; only `tools.coaching run` (manually or from its daily job) calls the
model, once per day, with Chronicle's short reports as the only input. The model
must support strict JSON-schema output on the Responses API.

## 4. Check the dashboard experience

- [ ] In Azure portal, open **Azure Monitor > Dashboards with Grafana**.
- [ ] Confirm this experience is available in your tenant/subscription.
- [ ] Keep access Entra-authenticated; do not enable anonymous/public sharing.

This portal experience is distinct from a paid Azure Managed Grafana workspace.
Microsoft documents Azure Monitor Logs and Application Insights traces as
supported sources. The dashboard experience has no additional dashboard charge;
ingestion, retention, and other Azure services can still incur costs.

Summary KQL, project/time filters, and current-identity query access were tested
with synthetic data. Check rendering and browser filter interaction after changes.
Do not create a dedicated Managed Grafana instance to work around an untested limitation.

"Private" here initially means authenticated and RBAC-restricted, not necessarily
private network endpoints. If network isolation is also required, decide that
before provisioning because it affects the Mac's ingestion and inference access.

## 5. Preview and provision the monitoring baseline

After authentication and environment selection, run:

```bash
az bicep build --file infra/main.bicep --stdout > /dev/null
azd provision --preview
```

If Bicep is missing, install it with `az bicep install` and retry. Preview needs
Azure access and may require resource-provider registration. Confirm
`Microsoft.OperationalInsights`, `Microsoft.Insights`, and `Microsoft.Dashboard`
are registered in the selected subscription; ask a subscription administrator
if registration is denied. Check that the selected region supports the dashboard
resource as well as Log Analytics/Application Insights.

Review the preview before applying:

- Resource group: `rg-<environment-name>`; no existing/shared model is touched.
- Workspace: 30-day default retention, shared-key access disabled.
- Application Insights: Entra-only ingestion (`DisableLocalAuth`).
- Coaching table `CopilotInsights_CL`: 90 days of retention.
- Direct data collection rule: typed fields with explicit projection, no
  overflow capture; Monitoring Metrics Publisher scoped to this rule.
- Native OTLP collection endpoint and telemetry DCR: traces only, same region,
  existing Application Insights/workspace reference, publisher scoped to this
  separate rule. Uses the documented Azure OTLP layout, not the legacy exporter.
- Portal resource `Microsoft.Dashboard/dashboards`, not the dedicated
  `Microsoft.Dashboard/grafana` resource type.
- No model resources, Docker hosts, secrets, or subscription-wide role grants.

If the preview matches, provision explicitly:

```bash
azd provision
```

This creates paid ingestion/storage capacity but starts no data collection.
Cloud deployment, regional availability, and reader access still require this
live verification; local compilation alone does not establish them.

Useful non-secret outputs (stored in the ignored local `azd` environment):

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

`LOG_ANALYTICS_WORKSPACE_ID` is the ARM resource ID; the separate
`LOG_ANALYTICS_WORKSPACE_CUSTOMER_ID` is the workspace query GUID.
No connection string is output. Use the
[native macOS installer](../README.md#native-macos-collector) instead.
Its standard OTLP HTTP exporter uses Azure CLI authentication and a bounded
disk queue after metadata filtering. It avoids the legacy Azure Monitor
exporter's internal fire-and-forget transport. The DCR follows Microsoft's
[native OTLP guidance](https://learn.microsoft.com/azure/azure-monitor/containers/opentelemetry-protocol-ingestion)
with both agent and direct trace source declarations; no agent is installed on
Azure. Native tables are `OTelSpans`, `OTelEvents`, and `OTelResources`, not the
legacy Application Insights request/dependency tables.

The insights DCR accepts only the columns declared in
[infra/core.bicep](../infra/core.bicep): run ID, kind (`tip` or `standup`), rank,
category, title, detail, repository, source, model ID, schema version, and
`TimeGenerated` (the run time). A projection cannot detect sensitive text placed
inside an allowed string field; the coaching worker validates items locally
before upload (see [daily coaching](coaching.md#data-boundaries)).

Open the Application Insights resource > **Dashboards with Grafana** after
provisioning. The deployed dashboard is tagged for that gallery and includes
observed input/output/cache-read tokens, a token trend, project/model breakdowns,
the coaching section, and collapsed raw-span details. Token sums use only
deduplicated chat spans, not overlapping agent/tool totals, and are not billing
or guaranteed complete usage. Synthetic
smoke records are excluded from dashboard queries. Earlier synthetic span
samples were deleted and query-verified absent. One service-name-only
resource lookup row remained after Azure rejected its ID filter and an exact
timestamp operation matched zero rows. It contains no token counts, prompts, or
conversation data and is not shown in the dashboard.
[The JSON definition](../dashboards/copilot-pilot.json) is deployed through
the documented `Microsoft.Dashboard/dashboards/dashboardDefinitions` child resource
(`2025-09-01-preview`), not unsupported properties on the parent. Check browser
rendering and datasource selection after provisioning changes. See the
[official export/ARM guidance](https://learn.microsoft.com/azure/azure-monitor/visualize/visualize-use-grafana-dashboards#manage-a-dashboard-as-an-arm-template).
The project field accepts `*` or an exact project ID and applies to telemetry
panels. The time picker filters span start and coaching run time; replayed spans
and resent coaching rows are deduplicated.

The baseline uses public TLS endpoints with Entra/RBAC, not private networking.
It grants data access only to the pilot user; existing inherited subscription/RG
permissions still apply. The deployment identity's Contributor/Owner rights also
control its ability to edit the dashboard; the scoped Reader grant is not an
editing grant for additional users.

Use the same `azd` environment for later updates. ARM's incremental deployments
do not remove a legacy Managed Grafana resource if you reuse an old environment;
review and clean up those resources separately. Only run `azd down` after
confirming the selected environment contains exclusively disposable pilot
resources. Never use it on an environment containing an existing/shared model.

Still pending: second-project metadata verification, verified usage totals, and
the first real 09:00 coaching run with wake/login catch-up. Cloud coaching
retention is configured here; no native client history is deleted. Use the
[README status](../README.md#current-status) for current progress and
the [troubleshooting guide](troubleshooting.md) only when diagnosing a failure.

## 6. End-to-end acceptance order

1. Run offline fixture-backed regression tests without Azure uploads.
2. Use controlled real-client telemetry from explicitly approved projects.
   Query back exact session identities and project aliases, and verify prohibited
   content is absent. Repeat with a second approved project; synthetic records
   cannot establish real-client attribution.
3. Run `tools.coaching run --execute`, inspect the local report and items, then
   `verify` the run. Review usefulness, not just JSON validity.
4. Verify real telemetry and coaching items in the dashboard with the
   same project/time filters. Keep unattributed spans visible; do not sum
   unverified tokens. Explicitly tagged synthetic smoke rows remain excluded.
5. Verify an authorized reader can view the data, an unauthorized identity
   cannot query it, and there is no anonymous dashboard route.

Use manual synthetic diagnostics only to isolate a specific infrastructure,
authentication, or exporter failure. Do not schedule them or
require new synthetic uploads for routine acceptance. Leave existing synthetic
rows to expire under configured retention instead of routinely purging them.

If you encounter a failure, share the command and sanitized error, not
`azd env get-values`, token output, or connection strings.

## Official references

- [Install Azure CLI](https://learn.microsoft.com/cli/azure/install-azure-cli)
- [Install Azure Developer CLI](https://learn.microsoft.com/azure/developer/azure-developer-cli/install-azd)
- [Azure Developer CLI command reference](https://learn.microsoft.com/azure/developer/azure-developer-cli/reference)
- [Azure OpenAI role-based access](https://learn.microsoft.com/azure/ai-foundry/openai/how-to/role-based-access-control)
- [Azure Monitor dashboards with Grafana](https://learn.microsoft.com/azure/azure-monitor/visualize/visualize-grafana-overview)
