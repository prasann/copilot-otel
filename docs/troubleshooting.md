# Troubleshooting

Use real sessions as the primary end-to-end acceptance evidence. Check local
service status, client endpoints, attribution reasons, and Azure query-back
before generating test traffic. A reachable collector or gateway does not prove
Azure delivery; historical dashboard rows do not prove current service health.

For attribution, follow the [client configuration and restart instructions](../README.md#connect-vs-code-insiders)
and [gateway checks](../README.md#opt-in-project-attribution). Some spans have no
session identity and correctly remain `unattributed` with `missing_identity`.
Do not guess their project from the active workspace, timestamps, or parent trace.

## Manual synthetic diagnostics

Keep offline synthetic fixtures and regression tests: they exercise attribution,
failure handling, and privacy filtering without uploading data. Live synthetic
checks below are optional, manual diagnostics after a relevant infrastructure or
exporter change, or when isolating a specific failure. Do not schedule them or
run them as routine activity. They do not prove real-client attribution.

Run commands from the repository root with the intended Azure CLI identity and
`azd` environment selected; see [Azure setup](azure-setup.md). Uploads and
storage can incur charges.

### Collector filtering and Azure delivery

With the native collector already installed and running:

```bash
.venv/bin/python -m tools.native_collector smoke
.venv/bin/python -m tools.native_collector query-smoke \
  --receipt <receipt-file-from-smoke-output>
```

The first command sends two explicitly tagged synthetic canary spans to the
direct collector receiver on `127.0.0.1:4318`. The second checks their stored
metadata and verifies prohibited canaries are absent. It does not send new
spans. If ingestion is delayed, repeat query-back rather than sending more data.

Do not inject synthetic spans carrying real session identities through the
attribution gateway: they can be classified as real activity and pollute the
normal dashboard.

### Daily coaching

Check `.venv/bin/python -m tools.coaching status` first; it lists recent runs,
their status, and Copilot credits. Scheduled output goes to `worker.log` in the
coaching state folder. Failures report a fixed reason, never report text:

| Reason | Meaning | Action |
| --- | --- | --- |
| `chronicle_<kind>_failed_exit_N` | Copilot CLI failed (sign-in, flags, credit cap) | Run `copilot -p "/chronicle tips" -s` manually |
| `chronicle_<kind>_possible_secret` | A secret pattern appeared in the report | Nothing was saved or sent to Azure; run the Chronicle command manually to find the source session |
| `Item contains disallowed content` / `ValidationError` | Model output failed validation | Inspect the report; retry tomorrow or with `--retry` |
| `RuntimeError` with a `results/` file present | Upload failed after a valid result | `tools.coaching publish <run-id>` |
| `RuntimeError` with reports but no result | Azure model request failed before a validated result was saved | Check Azure sign-in and TLS connectivity; do not rerun Chronicle just to test connectivity |

An `SSLEOFError` / `UNEXPECTED_EOF_WHILE_READING` from Azure CLI can be a TLS
key-share negotiation failure, not a missing certificate. Coaching uses a
process-local P-256-first OpenSSL configuration for its Azure calls (see
[setup](coaching.md#setup)). If it still fails, check whether an explicit
`OPENSSL_CONF` overrides it and contact network support. Never disable certificate
verification as a workaround.

After reprovisioning, re-run `tools.coaching enable --execute` so the cached
ingestion IDs are current. A newly changed ingestion rule can reject uploads with
`InvalidStream` for several minutes.

### Existing synthetic data

Normal dashboard queries exclude explicitly tagged collector smoke records.
Inspect these through their diagnostic query-back command, not the normal
activity panels. Let existing records expire under Azure retention (30 days for
spans). Older workspaces may still contain the retired `CopilotAssessments_CL`
table; nothing reads it, so delete it with
`az monitor log-analytics workspace table delete`.
