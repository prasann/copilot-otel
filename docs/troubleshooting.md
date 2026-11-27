# Troubleshooting

Check local services, client endpoints, attribution reasons, and Azure
query-back before generating test traffic. A reachable collector or gateway
does not prove Azure delivery; old dashboard rows do not prove current health.

## Attribution

Follow the [client configuration and restart instructions](telemetry.md#connect-vs-code-insiders)
and [gateway checks](telemetry.md#project-attribution-with-exclusions). Some spans have no
session identity and correctly remain `unattributed` with `missing_identity`.
Do not guess their project from the active workspace, timestamps, or parent trace.

### Client-source attribution

Version-2 discovery reads both the configured VS Code catalog and the local
desktop `~/.copilot/session-store.db`, using the same root/exclusions. Neither
source requires registering projects. Exclusions prevent project attribution,
not telemetry collection.

Use `/vscode/v1/traces` or `/desktop/v1/traces` on gateway 4319 for a configured
`telemetry.source` label. The source label remains present when project lookup
fails. It is not authenticated origin or evidence that the ID was found.
Unprefixed exports and direct 4318 exports use `unknown`; missing source values
also show as Unknown in Grafana. Do not relabel those rows as VS Code.
An All source filter retains them in totals; coaching ignores that filter.

`unknown_session` means no eligible exact ID match. The desktop workspace UUID
is not its runtime conversation UUID, and not every emitted span is covered by
the local catalog. Do not treat unknown IDs as proof of a particular client.
Check the labelled endpoint before suspecting the desktop/VS Code lookup.
`identity_conflict` also covers distinct workspaces collapsing to the same alias.
On the shared endpoint, an ID present in both catalogs conflicts; labelled
routes namespace the ID by client.

`lookup_failed` requires checking both metadata sources for availability,
permissions, locks, supported schema, and the 1,000-session bound. Version 2
fails closed when either catalog cannot be read, because global alias
collisions could otherwise be concealed. It never creates a missing catalog.
`unsupported_source` can identify a malformed known row; `lookup_incomplete`
warns that some metadata cannot be used. No database or payload paths are logged.

### Desktop launch and delivery

Follow the [direct executable launch](telemetry.md#connect-the-desktop-app).
Fully quit the existing app first; a second launch does not update the current
runtime's variables. Check selected OTel variables only, never dump the whole
process environment or logs containing credentials/content. Public current
HTTPS guidance differs from the verified 1.5.0 runtime's local HTTP behavior;
recheck after updates. Managed policy may override your environment.
HTTP 415 means the gateway requires uncompressed `application/json`, not
protobuf/gzip. Per-signal endpoint/protocol/compression variables take precedence
over global ones; point all enabled signals to the local gateway.

Keep content capture at `NO_CONTENT` and the legacy message-content switch
false. Pseudonymous identity may still be emitted; the collector's allowlist
strips it before the queue/cloud. Source-prefix routes require the updated gateway, and preserving
source requires reinstalling the stopped collector with the same policy.
Dashboard changes also need deployment; none of those steps is implied by a
local `/health` success.

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
  <receipt-file-from-smoke-output>
```

The first command sends two explicitly tagged synthetic canary spans to the
direct collector receiver on `127.0.0.1:4318`. The second checks their stored
metadata and verifies prohibited canaries are absent. It does not send new
spans. If ingestion is delayed, repeat query-back rather than sending more data.

Do not inject synthetic spans carrying real session identities through the
attribution gateway: they can be classified as real activity and pollute the
normal dashboard.

Normal activity panels exclude explicitly tagged smoke records. Inspect these
through query-back and let them expire under Azure retention (30 days for spans).

## Daily coaching

Check `.venv/bin/python -m tools.coaching status` first; it lists recent runs,
their status, and Copilot credits. Scheduled output goes to `worker.log` in the
coaching state folder. Failures report a fixed reason, never report text:

| Reason | Meaning | Action |
| --- | --- | --- |
| `chronicle_<kind>_failed_exit_N` | Copilot CLI failed (sign-in, flags, credit cap) | Run `copilot -p "/chronicle tips" -s` manually |
| `chronicle_<kind>_possible_secret` | A secret pattern appeared in the report | Nothing was saved or sent to Azure; run the Chronicle command manually to find the source session |
| `Item contains disallowed content` / `ValidationError` | Model output failed validation | Inspect the content-free item/schema diagnostics in the local result; see [upload recovery](coaching.md#recovering-a-failed-upload) |
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
