# Pilot checklist

Recorded checks below are historical evidence, not a live health report.
The [README](../../README.md) describes the implementation and commands;
[PLAN](../../PLAN.md) holds remaining design requirements.

## Completed foundation

- [x] Author separate development/evaluation fixtures, rubric, and output schemas.
- [x] Implement deterministic baseline, offline validation, and ungraded review templates.
- [x] Retire the standalone synthetic demo and unused folder mapper; retain safe regression fixtures.
- [x] Demonstrate native VS Code default-chat capture, archive, and resume with disposable synthetic probes.
- [x] Provision the Entra-only monitoring baseline with `azd`: Log Analytics,
  Application Insights, separate trace/assessment rules, and portal Grafana.
- [x] Run one bounded Azure GPT-5 mini synthetic assessment using Azure CLI identity.
- [x] Publish one manually reviewed synthetic summary and query back every stored field.
- [x] Deploy/read back the initial dashboard and test project/time-filtered queries.
- [x] Confirm earlier dashboard rendering/datasource access through user screenshots.
- [x] Install the pinned native collector with metadata filtering before a bounded
  persistent queue; verify synthetic filtering with the real binary and Azure query-back.
- [x] Implement collector install/start/stop/status/uninstall, ownership checks,
  failed-readiness cleanup, and stable friendly executable naming.
- [x] Configure application-wide Insiders metadata export with content/identity
  capture and extra local OTel stores off.
- [x] Add the repo-backed dashboard overview, activity trend, operations, summaries,
  and collapsed diagnostic details.

## Next: verify the metadata path

- [ ] Reload VS Code Insiders, use a new session, and verify native spans reach Azure.
- [ ] Provision the latest visual refresh and check rendering and browser project/time controls.
- [ ] Prove native project identity and choose one accounting layer before adding usage totals.
- [ ] Reconcile concurrent projects, subagent overlap, and replay with exact known totals.
- [ ] Add verified usage and collector delivery/error panels only after those checks.

Until then, native records stay `unattributed` with accounting unverified. Last
span time is freshness, not current collector health. The initial synthetic
span/assessment queries passed; that is not real-client verification.

## Daily reflection: implementation and activation

- [x] Build a version-gated, read-only adapter for new explicitly allowlisted default-chat sessions.
- [x] Test inactive archived eligibility, resume invalidation, pending-tool checks,
  source changes during inference, and pre-publication checks in isolated stores.
- [x] Implement locked, recoverable processing with 5 attempts/local day, 20,000
  total request characters, 2,000 output tokens, and no automatic failed-request retry.
- [x] Add an explicit reviewed-summary publisher, without automatic model-prose upload.
- [x] Test owned snapshot expiry at 30 days and assessment/receipt expiry at 90 days;
  enforce a 64 MiB reflection-state cap and never delete native history.
- [x] Implement gated 09:00 launchd install/uninstall and once-per-day catch-up checks.
- [x] Add approved-root scope for all current/future projects, with a fresh
  root cutoff, stable folder aliases, prefix isolation, and shared daily limits.
- [x] Explicitly enable the pilot and install its persistent daily job, deferring
  the first scheduled assessment until the next 09:00 local run.
- [x] Give the job the readable **Copilot Daily Session Review** label and plist
  name, with a description of its purpose and manual-publishing boundary.
- [ ] Inspect a newly created real session's local metadata-only capture preview.
- [ ] Explicitly enable inference and review the first real local assessment.
- [ ] Prove the same source contract after a complete editor/host restart.
- [ ] Observe a real 09:00 run, missed-run catch-up, and restart/recovery behavior.
- [ ] Verify one approved archived session's derived summary in the existing dashboard.

The user explicitly expanded content analysis from this repository to **all new
archived sessions under the approved projects root**, including future projects,
using the existing Azure model. The same 5-attempt daily budget applies across
projects. Tool arguments/output, attachments, and file contents remain excluded;
no backfill is authorized.

The initial narrow preview excluded three preexisting sessions; after root
expansion, four preexisting sessions were excluded and no new candidates existed.
The policy and daily job are now enabled, but the first scheduled assessment is
deferred until the next 09:00 local run. No real model requests have been made.
A newly created session is required for live capture/inference acceptance; the
current session is intentionally not backfilled.

## Remaining acceptance and deferred work

- [ ] Queue crash/replay, offline recovery, permanent rejection, expiry of CLI login,
  and saturation tests. Synthetic HTTP/query-back success is not delivery certification.
- [ ] Separate least-privilege-reader and unauthorized-identity access checks.
- [ ] Full repeated assessment-quality evaluation, long-input tests, and local/Azure comparison.
- [ ] Broader client/project and multi-machine coverage after the bounded pilot is useful.

Use the [acceptance checklist](../../SPIKES.md) and
[assessment scoring contract](spec.md#frozen-evaluation-set-and-scoring-procedure)
before claiming reliability or quality.

## Recorded limitations

- One synthetic model request succeeded (1,511 input / 948 output tokens, including
  128 reasoning tokens). Identity/citations validated, but manual review found
  prose overstating proof of a parser fix. This is not quality certification.
- Initial ingestion took several minutes. Retrying query-back, not upload, verified
  the reviewed summary and exact synthetic span metadata.
- Earlier synthetic assessment (one row) and spans (two rows) were deleted and
  query-verified absent. Known generated sample files were removed; the attempt
  ledger and cleanup audit were preserved.
- One service-name-only resource lookup row remains after Azure rejected its ID
  filter and an exact timestamp operation matched zero rows. It contains no token
  counts, prompts, or conversation data and is not shown in the dashboard.
- The capture probe findings are scoped to a tested internal-schema harness.
  Active-run/revision guards now have isolated regression tests, but live
  cancellation/publication timing, full host restart, and broader coverage remain
  unproven; see [capture findings](../../docs/spikes/session-capture-findings.md).
