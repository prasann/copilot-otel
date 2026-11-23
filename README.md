# Copilot reflection and observability

Metadata-only Copilot telemetry and evidence-backed session assessments in one
authenticated Azure Monitor dashboard with Grafana. Native macOS collector,
Python tools, and `azd`/Bicep infrastructure. No Docker or dedicated Grafana server.

## Current status

**Working pilot; live acceptance is incomplete.** Implementation and offline
regression tests are in place. Recorded Azure checks used synthetic data; they
do not establish real-client delivery, real-session assessment quality, or
current service health.

| Area | Implemented / recorded evidence | Still pending |
| --- | --- | --- |
| Session reflection | Read-only native capture, bounded worker, retention, daily scheduling, reviewed-summary publisher | First eligible real capture, assessment, and published-summary query-back |
| Metadata telemetry | Native macOS collector, filtering before disk queue, synthetic privacy and Azure query-back checks | Insiders reload and real-span delivery; project attribution and usage accounting |
| Azure dashboard | Monitoring baseline and latest dashboard deployed (user-confirmed); initial query checks and earlier rendering confirmed | Verify latest rendering and portal controls |
| Assessment quality | Rubric, schemas, separate evaluation fixtures, one bounded Azure synthetic model call | Repeated scored evaluation, longer inputs, local/Azure comparison |

The last recorded pilot setup on **23 November 2026** enabled reflection for new
archived sessions under the approved projects root, including future projects,
and installed **Copilot Daily Session Review**. Its first run was scheduled for
**24 November at 09:00 local**; no real-session model request had been made.
Insiders metadata export was also configured globally, but real-span arrival
was not verified. These are recorded setup results, not a live health report.
New installations keep inference and scheduling disabled until explicitly enabled.

Azure deployment, including the latest dashboard refresh, was confirmed complete
on **23 November 2026**. Live acceptance checks below remain separate.

### Pending acceptance

- [ ] Inspect a new archived session's metadata-only preview, review its first
  local assessment, then publish and query back a human-reviewed derived summary.
- [ ] Verify capture identity and eligibility after a full editor/host restart,
  including cancellation, resume, and publication timing.
- [ ] Observe a real 09:00 run, sleep/wake or login catch-up, and worker recovery.
- [ ] Reload Insiders and verify native spans in Azure.
- [ ] Check the deployed dashboard's rendering and project/time filters.
- [ ] Prove project identity and reconcile concurrent projects, subagent overlap,
  and replay against known totals before adding usage or delivery/error panels.
- [ ] Test queue crash/replay, offline recovery, permanent rejection, expired
  Azure CLI authentication, and saturation.
- [ ] Test a separate least-privilege reader and an unauthorized identity.
- [ ] Complete the [assessment-quality evaluation](specs/session-reflection/spec.md#frozen-evaluation-set-and-scoring-procedure)
  and longer-input tests. One synthetic result overstated evidence of a parser
  fix; successful validation is not quality certification.
- [ ] Validate other clients, multi-chat/subagent/compacted histories, remote
  sessions, and multi-machine coverage before expanding support.

See [feasibility and acceptance checks](SPIKES.md) for detailed criteria and
[capture findings](docs/spikes/session-capture-findings.md) for the tested source
contract. Usage totals and broader rollout remain deferred, not completed.

## Architecture today

```mermaid
flowchart LR
  subgraph Mac["Mac"]
    Client["VS Code Insiders<br/>content + identity capture OFF"]
    Filter["Native OTel Collector<br/>loopback :4318<br/>metadata allowlist"]
    Queue[("Disk queue<br/>128 requests / 64 MiB")]
    History["New archived sessions<br/>explicit project allowlist"]
    Capture["Read-only capture<br/>version + activity checks"]
    Assess["Daily assessment worker<br/>explicit activation"]
    Review["Local validation<br/>human-reviewed summary"]
    Client -->|OTLP HTTP| Filter --> Queue
    History --> Capture --> Assess --> Review
  end
  subgraph Azure["Azure Monitor - Entra / RBAC"]
    OTLP["Native OTLP endpoint + rule<br/>Application Insights reference"]
    DCR["Assessment ingestion rule"]
    Logs[("Log Analytics<br/>OTelSpans: 30 days<br/>CopilotAssessments_CL: 90 days")]
    Dashboard["Portal dashboard with Grafana<br/>shared project + time filters"]
    OTLP --> Logs
    DCR --> Logs --> Dashboard
  end
  Queue -->|"HTTPS / Azure CLI identity"| OTLP
  Review -->|"allowlisted derived fields only"| DCR
```

These are **independent paths**: telemetry does not trigger assessment. The
collector discards client logs and aggregate metrics, and strips content before
queueing traces. The real-session worker reads only new, allowlisted archived sessions; inference
and scheduling require explicit activation. Model inference uses a separately configured existing Azure
deployment; Bicep does not create or manage it.

## Try it locally

Requires Python 3.11+. Run from the repository root:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m unittest discover -s tests
```

Tests use authored fixtures and isolated native stores. They do not read real
history, call models, upload data, or install services.

Infrastructure tests compile Bicep if Azure CLI is present; its Bicep compiler
must also be installed. The native-binary privacy test runs only if the pinned
collector is already installed. Neither test installs these tools for you.

## Deploy Azure

Follow [Azure setup](docs/azure-setup.md) for authentication, permissions, model
configuration, and acceptance checks. After `az login` and `azd auth login`:

```bash
azd env new copilot-pilot
azd env set AZURE_SUBSCRIPTION_ID <subscription-id>
azd env set AZURE_LOCATION <monitoring-region>
azd provision --preview
# Review resources, roles, region, and cost before applying:
azd provision
```

Use `azd env select` for an existing environment. Bicep creates Log Analytics,
Entra-only Application Insights, separate trace/assessment ingestion rules, a
trace collection endpoint, scoped user roles, and the dashboard definition.
Endpoints use public TLS with RBAC, not private networking. Ingestion and storage
can incur charges.

## Native macOS collector

After provisioning and signing into Azure CLI:

```bash
.venv/bin/python -m tools.native_collector install
.venv/bin/python -m tools.native_collector status
curl --fail http://127.0.0.1:13133
```

Installation downloads a pinned, SHA256-checked collector and starts a user
LaunchAgent named **Copilot Telemetry Collector**. It does not configure clients.
It runs after login; sleeping or logging out can interrupt collection.

### Connect VS Code Insiders

Use these application-wide settings only for the metadata scope you intend to
collect. They apply across projects:

```json
{
  "github.copilot.chat.otel.enabled": true,
  "github.copilot.chat.otel.exporterType": "otlp-http",
  "github.copilot.chat.otel.protocol": "http/json",
  "github.copilot.chat.otel.otlpEndpoint": "http://127.0.0.1:4318",
  "github.copilot.chat.otel.captureContent": false,
  "github.copilot.chat.otel.captureIdentity": false,
  "github.copilot.chat.otel.outfile": "",
  "github.copilot.chat.otel.dbSpanExporter.enabled": false
}
```

Run **Developer: Reload Window**, then use a new session. Environment or enterprise
settings may override these values; verify actual records. CLI, Stable, and desktop
clients need separate configuration and verification.

### Check delivery and control the service

```bash
# Synthetic privacy/delivery check; no model call:
.venv/bin/python -m tools.native_collector smoke
.venv/bin/python -m tools.native_collector query-smoke <receipt-file>

.venv/bin/python -m tools.native_collector stop
.venv/bin/python -m tools.native_collector start
# Stop before reinstalling to update configuration.
.venv/bin/python -m tools.native_collector uninstall
```

If query-back is pending, retry the read, not `smoke`. Local health is not proof
of Azure delivery. State, queue, and warn/error logs are retained under
`~/Library/Application Support/copilot-otel`; inspect `logs/collector.stderr.log`
for failures. Logs are not yet rotated. Expired authentication requires `az login`.
Uninstall removes the recognized LaunchAgent, not its retained data.

## Azure synthetic smoke

<details>
<summary>Optional diagnostic only; not the real-session workflow</summary>

Configure local `.env` using [.env.example](.env.example) and
[Azure setup](docs/azure-setup.md). Explicitly opt in with
`ASSESSMENT_ALLOW_SYNTHETIC_INFERENCE=true`, then:

```bash
.venv/bin/python -m tools.azure_pilot assess --execute
# Inspect the assessment; author a short derived summary, not a quotation.
.venv/bin/python -m tools.azure_pilot publish <assessment-file> \
  --summary "<reviewed synthetic summary>" --execute
.venv/bin/python -m tools.azure_pilot query <receipt-file>
```

One invocation sends one tool-free model request, with no automatic retries,
`store=false`, and at most 2,000 output tokens. Failed calls count against the
checkout-local 16-attempt ceiling. This is not a monetary cap. Generated responses,
receipts, and the budget ledger stay in ignored `.pilot/`; **do not reset the
ledger to bypass the limit**. Retry query-back rather than republishing.

</details>

## Reading the dashboard

```text
Assessed sessions | Observed spans | Model labels | Last observed span
------------------+----------------+--------------+-------------------
Activity over time                 | Observed operations
----------------------------------------------------------------------
Assessment summaries
  > Inspect data: freshness and individual spans
  > Pilot notes: privacy, accounting, and analysis scope
```

Open **Azure Monitor > Dashboards with Grafana** in the portal. The
[definition](dashboards/copilot-pilot.json) is deployed by Bicep, not imported
manually. Queries use the signed-in reader's permissions. Project accepts `*`
or an exact project ID; refresh is five minutes.

- Spans are observations, **not billable calls or token totals**. Invocation and
  model-call spans may overlap, so their tokens are not summed.
- Native spans remain `unattributed` with accounting unverified. Project filters
  cannot reconstruct missing identity.
- Queries deduplicate replayed spans and assessment revisions before time filtering.
  Lookbacks are capped at 30 days for spans and 90 days for assessments.
- Synthetic smoke records are excluded. Real assessments appear only after
  explicit reviewed-summary publishing; no data is not a health signal.
- Published assessments describe historical archived revisions, not live archive
  state. Later resumes are tracked locally, not synchronized back to Azure.

## Daily session reflection

The worker reads only new, explicitly archived, inactive sessions from the tested
local VS Code Agent Host default-chat source. Results stay local until reviewed
and explicitly published. Initialize a new installation's policy once:

```bash
.venv/bin/python -m tools.session_reflection init \
  --project personal/copilot-otel=/absolute/path/to/copilot-otel
.venv/bin/python -m tools.session_reflection preview
.venv/bin/python -m tools.session_reflection status
```

Initialization fixes a no-backfill cutoff. Only sessions created afterward under
the explicit project roots can be captured. To explicitly approve an entire
projects tree in one step, including future projects:

```bash
.venv/bin/python -m tools.session_reflection configure-root \
  --projects-root /absolute/path/to/projects --execute
```

Root expansion gets its own fresh cutoff without resetting earlier approvals or
the budget. Preview prints IDs, counts, and eligibility reasons, **not conversation
text**, and makes no model calls.

All approved projects share **5 attempts per local day**, **20,000 characters for
the entire request**, and **2,000 output tokens per request**. Oversized or
uncertain sessions are deferred; failed or interrupted attempts are not
automatically retried. Owned snapshots expire after 30 days, results/receipts
after 90 days; reflection state is capped at 64 MiB. Native history is untouched.
These limits are not a monetary cap.

Follow [the operating guide](docs/session-reflection.md) for content scope,
explicit activation, the optional 09:00 local job, reviewed publishing, and
stop/uninstall commands. Installing the code alone enables neither inference nor
the job. Scheduling does not wake the Mac or automatically upload results.

## Repository map

| Location | Responsibility |
| --- | --- |
| [tools/](tools/) | Native capture, daily reflection, shared assessment validation, optional smoke tools, collector lifecycle |
| [collector/native-config.json](collector/native-config.json) | Trace privacy filter, authentication, durable queue |
| [infra/](infra/) | Azure resources and scoped RBAC |
| [dashboards/copilot-pilot.json](dashboards/copilot-pilot.json) | Single current dashboard |
| [tests/](tests/) | Assessment, attribution, collector, dashboard, and Bicep checks |
| [specs/session-reflection/](specs/session-reflection/) | Rubric, schemas, fixtures, and assessment evaluation contract |
| [docs/](docs/) | Azure setup, reflection operations, and capture findings |
| [SPIKES.md](SPIKES.md) | Recorded feasibility evidence and detailed acceptance criteria |

Older Docker/content-capture and branch-mapping experiments have been removed;
Git history retains them. Removing files does not remove older Azure resources.
Review those separately, and never run `azd down` on shared infrastructure or
an environment containing a shared model.

See [CONTRIBUTING.md](CONTRIBUTING.md) and [LICENSE](LICENSE).
