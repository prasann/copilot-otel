---
title: Copilot reflection and observability
description: An azd-managed Azure Monitor baseline for metadata-only telemetry and derived session assessments.
ms.topic: how-to
---

# Copilot reflection and observability

One authenticated Azure Monitor dashboard with Grafana is the target reading
experience for session assessments and metadata-only usage/reliability telemetry.
Prompts, replies, code, tool payloads, and evidence excerpts stay out of dashboard
storage. Real-history model inference requires separate approval.

## Status

- Offline synthetic assessment preparation/validation and a combined local demo work.
- The monitoring baseline and initial dashboard definition have been deployed
  in South India using `azd`.
- One synthetic GPT-5 mini assessment succeeded using Azure CLI authentication.
  One reviewed summary was ingested and queried back with stored fields verified.
- Dashboard definition read-back and both panel queries/project-time filtering
  have been checked through Azure CLI. Visual rendering remains a user check.
- Native capture/export, daily scheduling, real-history inference, and reliable
  telemetry delivery are not enabled. Usage panels clearly mark that missing source.

See [PLAN.md](PLAN.md) for the architecture,
[SPIKES.md](SPIKES.md) for feasibility evidence, and
[the first-slice spec](specs/session-reflection/spec.md) for evaluation details.

## Azure setup

Follow [the Azure pilot setup checklist](docs/azure-setup.md) for authentication,
subscription/region selection, least-privilege roles, existing model configuration,
deployment review, and remaining acceptance checks.

The existing [azure.yaml](azure.yaml) uses [Bicep](infra/main.bicep) to create:

| Resource | Purpose |
| --- | --- |
| Log Analytics workspace | Shared query destination; 30-day default retention |
| Workspace-based Application Insights | Future metadata-only OTel destination; Entra-only ingestion |
| Assessment table and direct data collection rule | Typed derived-summary ingestion; 90-day table retention |
| Azure Monitor Grafana dashboard and definition | Derived-summary/freshness panels and explicit telemetry status, without a dedicated Grafana instance |
| Scoped role assignments | Pilot user can read data/dashboard and publish to the assessment rule |

No Azure OpenAI/model deployment, API keys, app registrations, Docker hosts, or
dedicated Managed Grafana Standard instance are created. The existing model is
configured separately and is never managed or deleted by this template.

After signing into Azure CLI and `azd`, run from the repository root, replacing
placeholders:

```bash
azd env new copilot-pilot
azd env set AZURE_SUBSCRIPTION_ID <subscription-id>
azd env set AZURE_LOCATION <monitoring-region>
azd provision --preview
# Review resource, role, region, and cost changes before applying:
azd provision
```

Use a fresh environment/resource group for this pilot. Existing inherited
permissions still apply. The baseline uses public TLS endpoints restricted by
Entra/RBAC, not private networking.

The portal dashboard uses your signed-in user's data permissions, not a Grafana
managed identity. Provisioning installs the repo-backed
[dashboard definition](dashboards/copilot-pilot.json) through the documented
`dashboardDefinitions` child resource API. Browser rendering and datasource
selection still need a signed-in portal check. Usage is explicitly unavailable
until native telemetry is connected.

Local `.env` and `.azure/` configuration stay ignored. Commit templates and
non-secret examples, not environment outputs, credentials, or raw histories.
Ingestion/storage/query costs still apply even though the portal Grafana
experience has no separate dashboard hosting charge.

## Local synthetic demo

Requires Python 3.11+ and the dependencies in [requirements.txt](requirements.txt).
With a configured local virtual environment:

```bash
.venv/bin/python -m tools.local_demo
.venv/bin/python -m unittest discover -s tests
```

The demo prints a combined JSON preview with an explicitly authored assessment,
synthetic token usage, category-aware folder attribution, unattributed usage, and
replay deduplication. No history is read, services started, model called, or data
uploaded. It is not a running dashboard or a real OTel receiver.
Infrastructure tests also compile Bicep when Azure CLI is installed; install its
Bicep compiler first. They are explicitly skipped when Azure CLI is absent.

## Azure synthetic smoke

Follow [the setup checklist](docs/azure-setup.md), populate local `.env`, then
explicitly enable `ASSESSMENT_ALLOW_SYNTHETIC_INFERENCE=true` for synthetic calls:

```bash
.venv/bin/python -m tools.azure_pilot assess --execute
# Inspect the returned assessment file and write a short derived summary yourself:
.venv/bin/python -m tools.azure_pilot publish <assessment-file> \
  --summary "<reviewed synthetic summary, not a quotation>" --execute
.venv/bin/python -m tools.azure_pilot query <receipt-file>
```

The runner only loads development fixtures, not arbitrary history files. Calls
use `az rest` with explicit token audiences and the Azure CLI identity, with no
API keys or saved tokens. One command sends one model request, no tools, no
automatic retries, `store=false`, low reasoning effort, and at most 2,000 output
tokens. Failed attempts count against the checkout-local 16-attempt maximum.
This is not a monetary cap or a full quality evaluation.

Responses and receipts live under ignored `.pilot/`. Do not delete the attempt
ledger to bypass the budget or commit the generated model responses. The configured
model version is a configuration label; the returned model ID is recorded separately,
and does not prove a specific serving version.

The synthetic publisher uses only typed allowlisted columns and a manually
reviewed summary, not raw model prose. It rejects complete fixture-event quotations;
this is not a general semantic redactor and must not be used for real histories.
Source timestamps are synthetic and labelled as such. A successful upload means
accepted, not query-verified: if query-back reports propagation pending, retry
only the read command later, not the upload.

## Deployment validation

Install Azure CLI, Azure Developer CLI, and Bicep. Local compilation does not need
a cloud deployment:

```bash
az bicep install  # only if the compiler is missing
az bicep lint --file infra/main.bicep
az bicep build --file infra/main.bicep --stdout > /dev/null
```

Then use `azd provision --preview` with your selected environment to check actual
Azure changes. Preview and deployment require appropriate Azure permissions and
supported regions. The pilot has been deployed; subsequent runs update that
environment and must still be reviewed.

## Legacy assets

[otel-collector-config.yaml](otel-collector-config.yaml), the branch-spend
[dashboards](dashboards/), and
[session-story attribution design](docs/session-story-attribution-plan.md) are
legacy experiments, not the new production pipeline. **Do not enable the old
collector/settings or upload raw content.** The old collector lacks the required
allowlist, durable delivery, and Entra exporter authentication for this baseline.
Do not use the old session-to-story joins as verified project attribution.

If upgrading an older environment, incremental provisioning does not delete its
previous Managed Grafana instance. Review those resources separately. Only use
`azd down` when the selected environment contains exclusively disposable
pilot-owned resources, never a shared model or other shared infrastructure.

## Contributing and license

See [CONTRIBUTING.md](CONTRIBUTING.md) and [LICENSE](LICENSE).
