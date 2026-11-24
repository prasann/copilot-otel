# Feasibility and acceptance checks

Current implementation and remaining work: [README](README.md#current-status).
Coaching operations: [daily Copilot coaching](docs/coaching.md).

## Evidence so far

| Check | Recorded result | Remaining work |
| --- | --- | --- |
| C1: Chronicle as coaching source | `copilot -p "/chronicle tips"` and `standup` run unattended; VS Code Agent Host sessions appear in Chronicle's store | Behaviour across Copilot CLI upgrades; output-format drift |
| C2: normalization and publishing | First real run: 14 validated items published, query-verified, and returned by all coaching panel queries | Rejection rate over a few weeks; repository labelling of standup items |
| C3: daily job | Installed; launch at load returns `not_due` after the day's run | Real 09:00 run, missed-run wake/login catch-up, expired `az`/`copilot` sign-in |
| C4: usefulness | Tips are specific to observed work | After ~2 weeks, recurring themes lead to at least two concrete habit changes |
| T1: native telemetry accounting | Synthetic collector filtering and Azure query-back passed; real attribution for one project visible in the dashboard | Controlled live two-project identity/query-back, accounting layer, exact reconciliation |

## Focused acceptance criteria

### Coaching

- A failed Chronicle call, model call, validation, or upload yields a visible
  `failed` run with a fixed reason, publishes nothing partial, and is not retried
  automatically.
- Reports with secret patterns never reach the Azure model; items with paths,
  code fences, or newlines never reach Log Analytics.
- Upload recovery via `publish <run-id>` does not double-count in the dashboard.
- Local reports expire after 30 days; results, receipts, and run rows after 90.

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
this checklist does not grant it. Keep telemetry independent of coaching, and
report unsupported or untested behavior explicitly.
