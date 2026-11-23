# Feasibility and acceptance checks

Current implementation and remaining work: [README](README.md#current-status).
Operational boundaries: [daily session reflection](docs/session-reflection.md).

## Evidence so far

| Check | Recorded result | Remaining work |
| --- | --- | --- |
| S1: passive VS Code capture | Native fixture proof plus version-gated adapter and isolated-store tests | First new real-session preview and broader coverage |
| S2: explicit archive/resume | Done/archive and resume observed; workflow accepted | Full host restart, active-run safety, publication races |
| S3: desktop app | Not run | Independent capture/lifecycle and duplicate-identity proof |
| S4: useful assessment | Fixtures, schemas, validators, and one Azure synthetic smoke | Repeated quality evaluation, longer input, usefulness review |
| S5: daily worker | Implemented and explicitly scheduled for next 09:00; local budget/recovery/retention/root-scope tests pass | First real inference, actual wake/login and restart acceptance |
| T1: native telemetry accounting | Synthetic collector filtering and Azure query-back passed | Real-client identity, accounting layer, exact reconciliation |

S1/S2 details, tested versions, limitations, and reproduction steps are in
[session capture findings](docs/spikes/session-capture-findings.md).
Capture feasibility does not establish production reliability. One model smoke
does not pass the assessment-quality gate.

## Focused acceptance criteria

### Capture and eligibility

- Recover known fixture markers in source order without manual per-session export.
  Reject unknown schema, missing data, partial records, and changes during reads.
- Keep duplicate project basenames and host/SDK session identities separate.
- Open, idle, failed, cancelled, and shutdown sessions remain deferred without
  explicit closure/archive and verified inactivity.
- Full restart preserves identity; resume invalidates the old eligible revision.
  Concurrent source changes cannot leave a stale assessment presented as current.
- App, CLI, remote, multi-chat, subagent, and compaction coverage require separate
  proof; unsupported sources stay excluded.

### Assessment quality

Follow the [frozen evaluation/scoring procedure](specs/session-reflection/spec.md#frozen-evaluation-set-and-scoring-procedure):
eight cases twice per approved provider. All outputs must validate and pass
evidence/unknown/injection safeguards; at least seven of eight interpretations
per run must match, with every safety-critical case passing. Human review must
find a useful recommendation beyond deterministic counts. Record failures,
latency, tokens, cost basis, and context limits; do not cherry-pick or silently retry.
The local/Azure comparison remains deferred, not completed by a single Azure run.

### Daily operation and data lifecycle

- Duplicate discovery, overlapping triggers, resumed revisions, and worker
  termination around result/manifest writes recover to one current result.
- Observe a real missed schedule and wake/login catch-up with the editor closed.
  Simulated time alone does not prove macOS scheduling.
- Provider/schema/disk/quota failures are visible and recoverable, not empty success.
- With an injected clock, verify owned content expires at 30 days and results at
  90 days, including caches/reports and without extending expiry on rediscovery.
- Exclusion/deletion removes queued owned copies only; native history is untouched.
  Payloads never appear in operational logs.

### Telemetry and dashboard

- Prove exact attributed plus unattributed totals on controlled concurrent-project
  records, including replay and parent/subagent overlap, before summing usage.
- Test offline delivery, forced termination/restart, expired login, full queue,
  permanent rejection, and update/uninstall state preservation.
- Verify forbidden-content canaries are absent from queued/uploaded metadata.
  HTTP acceptance alone is neither query-back nor exactly-once proof.
- Verify project/time filters, deduplication, signed-in reader access, denied
  unauthorized access, and no anonymous sharing. Separate span freshness from
  process health. Measure volume, latency, resource use, and cost before expansion.

## Experiment boundaries

Use newly authored synthetic fixtures and known IDs, not unrelated histories.
Native stores are read-only. Keep raw probes/evidence in protected temporary
storage outside Git; retain sanitized findings and remove named temporary
artifacts after the experiment. Never delete native sessions as incidental cleanup.

Installing services, changing client settings/sync, provisioning resources,
running inference, or broadening real-content scope needs the relevant approval;
this checklist does not grant it. Keep telemetry independent of reflection, and
report unsupported or untested behavior explicitly.
