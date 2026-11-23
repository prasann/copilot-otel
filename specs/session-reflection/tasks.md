# Working pilot checklist

Current focus: **4. Install the local pipeline**. Azure model, summary ingestion,
and initial dashboard/query smoke checks are complete. Visual verification is deferred.

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
- [ ] Add live project-level usage and collector freshness/error panels after the collector is connected.
- [x] Verify project/time filters and query access for the signed-in identity with actual data through CLI.
- [ ] User visual check later: portal rendering, Azure Monitor datasource selection, project/time controls. Not a blocker for CLI development.

## 4. Install the local pipeline

- [ ] Implement metadata-only telemetry normalization and prove source project identity with one controlled client sample.
- [ ] Configure the native collector with a metadata allowlist, bounded durable queue, and visible delivery errors.
- [ ] Implement read-only archived-session capture and daily assessment processing.
- [ ] Apply folder-based project attribution; leave unsupported/ambiguous identities explicitly unattributed.
- [ ] Add local retention: owned source snapshots 30 days, assessments 90 days; never delete native history.
- [ ] Add repeatable install/uninstall scripts and launchd jobs; verify startup and health.

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
- User requested CLI-first verification; browser login is unavailable. Defer visual checks to the user rather than block installation work.
- Real-history inference is not approved.
- Native telemetry, batch capture, retention jobs, and launchd installation are not yet enabled. No real session history has been read or sent by this smoke test.

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
