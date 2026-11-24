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
run them as routine activity. They do not prove real-client attribution or
real-session assessment quality.

Run commands from the repository root with the intended Azure CLI identity and
`azd` environment selected; see [Azure setup](azure-setup.md). Uploads, inference,
and storage can incur charges.

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

### Model endpoint and assessment publishing

Configure local `.env` using [.env.example](../.env.example) and
[Azure setup](azure-setup.md). Only for this diagnostic, explicitly set
`ASSESSMENT_ALLOW_SYNTHETIC_INFERENCE=true`, then:

```bash
.venv/bin/python -m tools.azure_pilot assess --execute
# Inspect the assessment; author a short derived summary, not a quotation.
.venv/bin/python -m tools.azure_pilot publish <assessment-file> \
  --summary "<reviewed synthetic summary>" --execute
.venv/bin/python -m tools.azure_pilot query <receipt-file>
```

Publishing is optional if only inference connectivity is in question. One
assessment invocation sends one tool-free model request, with no automatic
retries, `store=false`, and at most 2,000 output tokens. Failed calls count
against the checkout-local 16-attempt ceiling; this is not a monetary cap.
Responses, receipts, and the budget ledger stay in ignored `.pilot/`.
**Do not reset the ledger to bypass the limit.** Retry query-back rather than
republishing. Restore `ASSESSMENT_ALLOW_SYNTHETIC_INFERENCE=false` afterward;
this flag is independent of real-session inference approval.

### Existing synthetic data

Normal dashboard queries exclude explicitly tagged collector and assessment
smoke records. Inspect these through their diagnostic query-back commands, not
the normal activity panels. Let existing records expire under Azure retention:
30 days for spans and 90 days for assessments. Routine operation does not
require a purge or new synthetic uploads.
