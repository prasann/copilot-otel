# Working pilot checklist

Current focus: **4. Install the local pipeline**. Azure model, summary ingestion,
and initial dashboard/query smoke checks are complete. User screenshots confirm
dashboard rendering and datasource access; browser filter interaction remains unchecked.

Work through one basic smoke test per connection, then install the pilot.
Check items only after the stated outcome is verified. Record blockers here.
Real-history inference requires separate approval; this checklist does not enable it.

## 1. Connect the Azure model

- [x] Configure local model endpoint, deployment, and version; select Azure CLI authentication without keys.
- [x] Implement a bounded synthetic inference runner using the Responses API and Azure CLI identity.
- [x] Run one synthetic assessment; check usable output and report authentication/API errors explicitly.

## 2. Connect Azure storage

- [x] Provision the monitoring baseline in South India using azd.
- [x] Verify Application Insights, direct ingestion rule, and portal dashboard resource report success; local authentication is disabled for Application Insights.
- [x] Implement a synthetic assessment publisher with an explicit field/content allowlist.
- [x] Publish one synthetic summary and query it back; verify stored fields and exclude raw conversation content.

## 3. Build the first unified dashboard

- [x] Verify the portal dashboard content API/export and keep its definition/setup repo-backed.
- [x] Add assessment summaries grouped by project.
- [x] Add assessment freshness and explicit missing-telemetry status panels.
- [x] Add a labelled synthetic OTLP connection-check panel; deploy/read back its definition and verify exact counts plus wildcard/exact-project/time filters through CLI.
- [ ] Add live project-level usage and collector freshness/error panels after the collector is connected.
- [x] Verify project/time filters and query access for the signed-in identity with actual data through CLI.
- [x] User screenshots confirm portal rendering and working Azure Monitor datasource access: summary/freshness rows and expected synthetic OTLP counts are visible.
- [ ] User visual check later: exercise project/time controls in the browser. Controls are visible; filtering is already CLI-verified. Not a blocker.

## 4. Install the local pipeline

- [ ] Implement metadata-only telemetry normalization and prove source project identity with one controlled client sample.
- [x] Configure the native collector with a metadata allowlist, bounded persistent queue, and visible delivery errors.
  - [x] Provision separate Entra-only native OTLP endpoint/DCR in South India using azd; retain the existing workspace/Application Insights.
  - [x] Install pinned collector via user LaunchAgent; verify health and loopback-only listeners. No clients configured.
  - [x] Prove actual pinned-binary filtering of synthetic content, schema URLs, events/links, and invalid token/model values.
  - [x] Query the two synthetic spans back from Azure and verify their exact stored metadata: 100 input / 20 output / 10 cache-read tokens; invalid values removed; no canaries or event rows.
- [ ] Implement read-only archived-session capture and daily assessment processing.
- [ ] Apply folder-based project attribution; leave unsupported/ambiguous identities explicitly unattributed.
- [ ] Add local retention: owned source snapshots 30 days, assessments 90 days; never delete native history.
- [ ] Add repeatable install/uninstall scripts and launchd jobs; verify startup and health.
  - [x] Add collector install/start/stop/status/uninstall commands; verify repeated uninstall and healthy reinstall. Failed readiness unloads the job; unrecognized plists are rejected.

## 5. Start one real-project pilot

- [ ] Obtain explicit approval for one project's archived conversation content to go to the Azure model.
- [ ] Enable only the approved project and keep OTel content capture off.
- [ ] Verify one archived session's derived summary and real metadata usage appear in the same dashboard.
- [ ] Verify the next scheduled run and a basic restart/retry path; document how to check health or stop collection.

## Blockers / decisions

- No current setup action required from the user.
- GPT-5 mini smoke succeeded: one request, 1,511 input / 948 output tokens (including 128 reasoning tokens). Identity/citations validated. Manual review found the partial outcome useful but some prose overstated proof of a parser fix; not production-quality certification.
- One reviewed summary accepted and queried back with every published field matched. Stored columns contain only the intended assessment fields and Azure system columns. Initial table ingestion took several minutes; no republish was needed. DCR diagnostics reported one row received, no drops, and no transformation errors.
- Repo-backed `dashboardDefinitions/default` deployed using azd and read back through ARM; panels, queries, bindings, and variables match the repo. Both panel queries execute with the synthetic row. Exact project match returns one row; a different project and excluded time range return none. Freshness shows one synthetic assessment.
- Signed-in identity can ingest and query. Separate least-privilege-reader/unauthorized-identity tests have not been performed.
- User requested CLI-first verification; integrated-browser login is unavailable. User-provided portal screenshots on 2026-11-23 confirm rendering and datasource access. Browser filter interaction remains unchecked.
- Real-history inference is not approved.
- Native collector is running with no Copilot clients connected. Both synthetic spans are query-verified with exact metadata; content/schema/scope/event/link canaries and invalid values were removed. Native OTLP uses acknowledged HTTP rather than the legacy fire-and-forget exporter. Queue crash/replay and permanent-rejection behavior have not been live-tested; this is not delivery certification.
- Collector checkpoint: 10 targeted lifecycle/privacy/infrastructure tests pass, Bicep lint and editor diagnostics pass, and the five-panel dashboard definition matches ARM read-back. User screenshots also confirm the connection table shows two synthetic spans and 100 input / 20 output / 10 cache-read tokens.
- Batch capture, retention jobs, and daily assessment launchd installation remain unimplemented. No real session history has been read or sent by this smoke test.

## Deferred until the pilot is useful

- Full repeated synthetic evaluation and local-versus-Azure model comparison.
- Broader client/project coverage, multi-machine rollout, and advanced dashboard analysis.
- Extensive reliability/concurrency testing beyond the basic installation smoke checks.

## Completed foundation work

- [x] Commit planning checkpoint and create feature branch.
- [x] Author eight synthetic development cases with evidence and reference answers.
- [x] Define assessment output schema and rubric.
- [x] Build/test offline output validation and deterministic baseline.
- [x] Author held-out evaluation cases separate from rubric tuning.
- [x] Add explicit suite selection and ungraded, hash-bound review templates.
- [x] Add Azure environment placeholders; endpoint/model/auth to be supplied later.
- [x] Build local synthetic combined preview with shared folder attribution and replay checks.
- [x] Replace legacy azd infrastructure with Entra-only monitoring, assessment ingestion, and portal Grafana resource shell.
