# Copilot telemetry

Collect observed Copilot usage from VS Code Insiders and the desktop app.
The native macOS collector filters telemetry to an allowlist before its disk
queue and Azure upload. Coaching is independent and reads none of this data.

Run commands from the repository root with the Python environment and
[Azure resources](azure-setup.md) prepared.

## Install the collector

Sign in to Azure CLI and select the intended `azd` environment, then:

```bash
.venv/bin/python -m tools.native_collector install
.venv/bin/python -m tools.native_collector status
curl --fail http://127.0.0.1:13133
```

Installation downloads a pinned, SHA256-checked collector and starts the
**Copilot Telemetry Collector** user LaunchAgent. It does not configure clients.
It runs after login; sleep or logout can interrupt collection. Local health
does not prove Azure delivery.

## Connect VS Code Insiders

These settings are application-wide, across projects. Agent Host and the
Copilot Chat extension have separate telemetry settings; enabling one does not
enable the other. Agent Host settings cover its providers, including Copilot
SDK chats.

For Agent Host sessions:

```json
{
  "chat.agentHost.otel.enabled": true,
  "chat.agentHost.otel.exporterType": "otlp-http",
  "chat.agentHost.otel.otlpEndpoint": "http://127.0.0.1:4318",
  "chat.agentHost.otel.captureContent": false,
  "chat.agentHost.otel.captureIdentity": false,
  "chat.agentHost.otel.outfile": "",
  "chat.agentHost.otel.dbSpanExporter.enabled": false
}
```

Fully quit and reopen Insiders after Agent Host changes. A window reload can
leave the shared host running with its previous startup configuration.

For Copilot Chat extension telemetry:

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

Run **Developer: Reload Window** after extension-only changes. Use a new
controlled session to verify delivery. Environment or enterprise settings may
override configuration. CLI and Stable clients require separate configuration
and verification; these settings do not enable them.

## Project attribution with exclusions

Direct exports to `127.0.0.1:4318` remain `unattributed` with source `unknown`.
The optional foreground gateway on `127.0.0.1:4319` matches exact session IDs
against read-only VS Code and desktop catalogs. It sends aliases to a separate
collector receiver on `127.0.0.1:4320`, which filters before the disk queue.
It reads no conversation/event files and makes no model calls.

Create a private JSON policy outside Git using full absolute paths. JSON
values do not expand `~` or environment variables:

```json
{
  "version": 2,
  "editor": "/Users/you/Library/Application Support/Code - Insiders",
  "projects_root": "/Users/you/projects",
  "excluded_workspaces": []
}
```

Each export discovers local sessions whose project resolves beneath
`projects_root`. Aliases are `<category>/<project>`: the first folder under the
root and the project folder's name, so `personal/apps/demo` becomes
`personal/demo`. A project directly beneath the root becomes
`projects/<folder>`. Non-Git workspaces use the opened workspace folder. See
[attribution troubleshooting](troubleshooting.md#attribution) for supported
layouts and failure reasons.

Aliases expose category/project names, not absolute paths. Each component must
be 1-63 ASCII letters, digits, dots, underscores, or hyphens; `synthetic/demo` is
reserved. Distinct projects that would share an alias remain unattributed with
`identity_conflict`. Multiple sessions in one project are allowed.

Add absolute directories to `excluded_workspaces` to exclude sessions in them
and their descendants from attribution. Paths are resolved, so symlinks escaping
the root are ineligible. **Exclusions do not stop collection**: those spans still pass
through as `unattributed`. Enabling collection remains a separate client choice.
Version-1 policies retain their explicit `projects` allowlist and VS Code-only
catalog behavior.

After reviewing the scope:

```bash
chmod 600 /absolute/path/to/attribution-policy.json
.venv/bin/python -m tools.native_collector stop
.venv/bin/python -m tools.native_collector install \
  --attribution-policy /absolute/path/to/attribution-policy.json
.venv/bin/python -m tools.attribution \
  --policy /absolute/path/to/attribution-policy.json
```

Keep the gateway terminal open; it is not installed as a login service.
In another terminal:

```bash
curl --fail http://127.0.0.1:4319/health
```

Expect `status: "ok"` and `collector: "reachable"`. The
`cloud_delivery: "not_checked"` field is deliberate: this checks local
reachability only.

Change the enabled VS Code endpoint above to `http://127.0.0.1:4319/vscode`,
then restart the appropriate host. Desktop uses the launch below. Policy
changes require a gateway restart. Switching policy versions or changing a
version-1 allowlist also requires reinstalling the stopped collector.
Restart the gateway after code updates; reinstall the stopped collector for
filter changes. Neither rewrites historical rows.

Source-prefixed routes (`/vscode/v1/traces`, `/desktop/v1/traces`, and matching
metrics/logs routes) label configured ingress. Source is not authenticated
origin; other local processes can use these ports. Unprefixed routes remain
supported with source `unknown`. A labelled route scopes exact-ID lookup to
that client, while alias collisions are checked across both catalogs.
Loopback is not an authentication boundary against other processes or users
on the same Mac.

Unmatched or conflicting identities stay unattributed. There is no guess from
the active project, paths, timestamps, or parent traces. Missing, locked, or
incompatible catalogs fail closed with content-free warnings; version 2 needs
both catalogs for collision checks. Each catalog is bounded to 1,000 candidate
sessions. See [attribution troubleshooting](troubleshooting.md#attribution).

## Connect the desktop app

The launch configuration below was checked with desktop 1.5.0 / runtime
1.0.115-7, which accepts loopback HTTP. Client requirements can change; recheck
after updates rather than assuming every emitted span is attributable.

Start the collector and gateway first. Fully quit the app, then launch its
actual executable from a terminal. Launching an already-running app does not
replace its runtime environment.

```bash
env -u COPILOT_OTEL_FILE_EXPORTER_PATH \
  -u OTEL_EXPORTER_OTLP_HEADERS \
  -u OTEL_EXPORTER_OTLP_TRACES_HEADERS \
  -u OTEL_EXPORTER_OTLP_METRICS_HEADERS \
  -u OTEL_EXPORTER_OTLP_LOGS_HEADERS \
  COPILOT_OTEL_ENABLED=true \
  COPILOT_OTEL_EXPORTER_TYPE=otlp-http \
  OTEL_EXPORTER_OTLP_ENDPOINT=http://127.0.0.1:4319/desktop \
  OTEL_EXPORTER_OTLP_TRACES_ENDPOINT=http://127.0.0.1:4319/desktop/v1/traces \
  OTEL_EXPORTER_OTLP_METRICS_ENDPOINT=http://127.0.0.1:4319/desktop/v1/metrics \
  OTEL_EXPORTER_OTLP_LOGS_ENDPOINT=http://127.0.0.1:4319/desktop/v1/logs \
  OTEL_EXPORTER_OTLP_PROTOCOL=http/json \
  OTEL_EXPORTER_OTLP_TRACES_PROTOCOL=http/json \
  OTEL_EXPORTER_OTLP_METRICS_PROTOCOL=http/json \
  OTEL_EXPORTER_OTLP_LOGS_PROTOCOL=http/json \
  OTEL_EXPORTER_OTLP_COMPRESSION=none \
  OTEL_EXPORTER_OTLP_TRACES_COMPRESSION=none \
  OTEL_EXPORTER_OTLP_METRICS_COMPRESSION=none \
  OTEL_EXPORTER_OTLP_LOGS_COMPRESSION=none \
  OTEL_INSTRUMENTATION_GENAI_CAPTURE_CONTENT=NO_CONTENT \
  OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT=false \
  "/Applications/GitHub Copilot.app/Contents/MacOS/github"
```

This does not configure login startup or Finder/Dock launches. Managed policy
can override settings. Keep `NO_CONTENT` set and message-content capture false.
`NO_CONTENT` does not suppress all identity metadata: the collector removes
pseudonymous identities and paths before queue/upload, while validated
conversation UUIDs and project aliases remain.

The gateway receives unfiltered telemetry only in memory. It accepts
uncompressed OTLP HTTP/JSON, not protobuf or gzip. It has no disk spool or
automatic forwarding retries; bounds are 1 MiB per request/response, 10,000
spans per export, and 10 seconds per socket operation. Downstream errors and
partial-success responses reach the client. Client retries can replay spans,
and interruptions can lose telemetry.

## Read the dashboard

Open **Azure Monitor > Dashboards with Grafana**. Queries use the signed-in
reader's permissions and refresh every five minutes.

| View | Meaning |
| --- | --- |
| Tokens, trends, project/model tables | Reported input/output/cache-read tokens from deduplicated `chat` spans |
| Project AIUs and estimated usage value | Reported AI units from eligible top-level `invoke_agent` observations |
| Latest tips and standup | Most recent coaching run in the selected time range |
| Recurring themes | Repeated tip categories across runs |
| Inspect data | Individual spans, attribution reasons, source comparison, and coverage gaps |

Project accepts `*` or an exact project ID. Telemetry source selects All,
VS Code, Desktop, or Unknown. Project/source filters apply only to telemetry;
coaching covers all projects. The time picker applies to both paths. Queries
deduplicate replayed spans and resent coaching rows before time filtering,
with lookbacks capped at 30 days for spans and 90 days for coaching.

Unknown/unprefixed source records remain included under All; they are not
backfilled as VS Code. Unattributed activity stays visible. Explicitly tagged
collector smoke records are excluded from normal activity panels.

### Usage is not billing

These are observed, client-reported values, not complete usage or an invoice.
Missing token/cache reporting is not treated as zero; inspect `MissingUsage`
and `MissingCache`. Transport loss and overlapping or duplicate client
instrumentation can affect totals.

The AIU table estimates **observed AIUs x USD 0.01**, assuming one observed
AIU approximately equals one billing credit. That equivalence is not verified.
The estimate excludes allowances, discounts, invoice reconciliation, Azure
model calls, and ingestion/storage. It does not extrapolate unreported usage.
See [GitHub's AI credit pricing](https://docs.github.com/en/billing/concepts/product-billing/github-copilot-billing).

`github.copilot.nano_aiu` measures AI units (one AIU is one billion nano AIU).
`github.copilot.cost` is a model billing multiplier, **not money**, and is never
summed as spend. Only numeric, finite, nonnegative values are retained.

Eligible roots are `invoke_agent` spans with a nonempty string `server.address`
and an integer `server.port` in 1-65535; those raw markers are discarded before
queue/upload. Other invocations are excluded from AIU totals as unknown.
A parent span ID alone does not establish top-level work. These are unverified
client reports; marker semantics and field availability can differ by client
and harness.

`EligibleRoots`, `RootsReportingAIUs`, `MissingAIURoots`, and
`UnclassifiedInvocations` expose coverage. All-missing AIUs are unavailable,
not zero; an explicitly reported zero remains zero. `FirstClassifiedRoot` is
the first eligible observation in the selected range. Only newly collected
fields are available; no historical backfill is performed.

Do not export one logical invocation through multiple routes: span-ID
deduplication cannot remove independently generated duplicate spans.

## Check delivery and stop collection

Use a controlled real session and query recent Azure rows; local health and
old dashboard data do not establish current delivery. Optional synthetic
diagnostics are in [troubleshooting](troubleshooting.md#manual-synthetic-diagnostics).

```bash
.venv/bin/python -m tools.native_collector stop
.venv/bin/python -m tools.native_collector start
.venv/bin/python -m tools.native_collector uninstall
```

State, queue, and warn/error logs remain under
`~/Library/Application Support/copilot-otel`. Inspect
`logs/collector.stderr.log`; logs are not rotated. Expired authentication
requires `az login`. Uninstall removes the recognized LaunchAgent, not retained
data or client telemetry settings.

To disable attribution but keep collection, restore all enabled client endpoints
(including per-signal overrides) to `http://127.0.0.1:4318`, restart the affected
clients, stop the gateway with Ctrl-C, and reinstall the stopped collector
without `--attribution-policy`.

To stop collection entirely, disable the enabled VS Code telemetry settings,
fully quit/reopen the appropriate host, and quit/reopen desktop normally without
the telemetry environment variables. Verify those variables are no longer in
its runtime, then stop or uninstall the collector. Historical Azure records
remain until retention expires.
