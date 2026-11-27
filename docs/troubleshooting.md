# Troubleshooting

Use real sessions as the primary end-to-end acceptance evidence. Check local
service status, client endpoints, attribution reasons, and Azure query-back
before generating test traffic. A reachable collector or gateway does not prove
Azure delivery; historical dashboard rows do not prove current service health.

For attribution, follow the [client configuration and restart instructions](../README.md#connect-vs-code-insiders)
and [gateway checks](../README.md#project-attribution-with-exclusions). Some spans have no
session identity and correctly remain `unattributed` with `missing_identity`.
Do not guess their project from the active workspace, timestamps, or parent trace.

### Desktop and client-source attribution

Version-2 discovery now reads both the configured VS Code catalog and the local
desktop `~/.copilot/session-store.db`, using the same root/exclusions. Neither
source requires registering projects. Exclusions prevent project attribution,
not telemetry collection.

Use `/vscode/v1/traces` or `/desktop/v1/traces` on gateway 4319 for a configured
`telemetry.source` label. The source label remains present when project lookup
fails. It is not authenticated origin or evidence that the ID was found.
Unprefixed exports and direct 4318 exports use `unknown`; historical missing
source values also show as Unknown in Grafana. Do not relabel old rows as VS Code.
An All source filter retains them in totals; coaching ignores that filter.

`unknown_session` means no eligible exact ID match. The desktop workspace UUID
is not its runtime conversation UUID, and not every emitted span is covered by
the local catalog. Do not treat unknown IDs as proof of a particular client.
Check the labelled endpoint before suspecting the desktop/VS Code lookup.
`identity_conflict` also covers distinct canonical repositories or non-Git
workspaces collapsing to the same alias; registered worktrees of one repository
do not conflict merely because their paths differ.
On the shared endpoint, an ID present in both catalogs conflicts; labelled
routes namespace the ID by client.

`lookup_failed` requires checking both metadata sources for availability,
permissions, locks, supported schema, and the 1,000-session bound. Version 2
fails closed when either catalog cannot be read, because global alias
collisions could otherwise be concealed. It never creates a missing catalog.
`unsupported_source` can identify a malformed known row; `lookup_incomplete`
warns that some metadata cannot be used. No database or payload paths are logged.

For desktop activation follow the [direct executable launch](../README.md#connect-the-desktop-app).
Fully quit the existing app first; a second launch does not update the current
runtime's variables. Check selected OTel variables only, never dump the whole
process environment or logs containing credentials/content. Public current
HTTPS guidance differs from the verified 1.5.0 runtime's local HTTP behavior;
recheck after updates. Managed policy may override your environment.
HTTP 415 means the gateway requires uncompressed `application/json`, not
protobuf/gzip. Per-signal endpoint/protocol/compression variables take precedence
over global ones; point all enabled signals to the local gateway.

Keep `NO_CONTENT` and the legacy message-content switch off. Pseudonymous
identity may still be emitted; the collector's allowlist strips it before the
queue/cloud. Source-prefix routes require the updated gateway, and preserving
source requires reinstalling the stopped collector with the same policy.
The source-aware dashboard JSON also needs deployment; none of those steps
is implied by a local `/health` success.

### Git worktree project rollup

Version 2 uses the same resolver for VS Code and desktop: exact catalog identity
first, then the actual workspace's nearest Git checkout and verified primary
checkout. Repository subdirectories roll up to that primary; nested independent
repositories stay independent. Non-Git directories keep the previous
workspace-based alias. Version 1's explicit allowlist is unchanged.

For Git repositories, derive the alias from the canonical primary checkout:
the first folder under `projects_root` is the category and the checkout's final
folder is the project name. For example, `personal/apps/demo` and its registered
worktrees share `personal/demo`; a direct root child uses `projects/<name>`.
This repository rollup supersedes workspace-folder naming for Git projects only.
Directory names and remote URLs never establish repository ownership.

Excluding the primary checkout excludes all its worktrees; excluding one
worktree or session subdirectory leaves its siblings eligible. These are
attribution exclusions, not collection exclusions: incoming spans still pass
through as `unattributed`. Raw paths/config values are not exported or logged.

For an unexpected `unattributed` result, check the following privately:

- The session directory must still exist. A removed worktree or stale catalog
  `cwd` yields `lookup_incomplete`, not a folder-name guess.
- A linked checkout's `.git` gitfile must identify its registered administrative
  directory under the primary's `.git/worktrees/`. Its `commondir` and `gitdir`
  must link back consistently to the common directory and actual checkout.
  Copying a gitfile alone does not establish registration.
- The primary checkout must exist inside `projects_root`. Exclusions apply to
  actual session/checkout paths and the primary; an absolute exclusion outside
  the root can exclude one external worktree without excluding its siblings.
  An inside-root symlink escaping to an external directory stays ineligible.
- Metadata must be user-owned, regular files or real directories, and not
  group/other writable. Symlinked Git markers/administrative directories, bad
  backreferences, read errors, or metadata changes during an export fail the
  batch with `lookup_failed` rather than risk hiding an alias conflict.

Bare-common-directory worktrees, separate Git directories, absorbed submodule
gitfiles, includes, `core.worktree` overrides, worktree-specific config, and
unsupported Git config syntax/topology yield `unsupported_source` or
`lookup_failed`. Do not remove legitimate configuration just to make attribution
pass; these layouts are deliberately unsupported initially. A standalone nested
repository with an ordinary `.git` directory is supported.

For a manually moved checkout, inspect and repair Git registration separately
using Git's documented `worktree repair` procedure only when you intend that
repository change. The gateway never repairs, prunes, or executes Git. Once
registration and catalog paths agree, the next export rereads them; no cache
flush is needed. A primary move that changes category/name changes only future
aliases. Old dashboard rows are not rewritten.

The VS Code 1,000-candidate bound now includes out-of-root local sessions because
external worktrees must be considered before root filtering. Catalog or metadata
overflow fails with `lookup_failed`; it is never silently truncated. Repeated
workspaces share reads only within a batch. Records without a local `file:///`
project URI (including remote or unscoped sessions) remain ineligible and are not
treated as broken local Git metadata.

Git resolution reads only bounded filesystem metadata, never executes Git,
hooks, global config, or network lookups. Each export starts fresh, with no
cross-export cache. Limits are 64 ancestor steps per workspace, 64 KiB per
metadata file, and 8 MiB of metadata reads per export; overflow fails closed.

Deploy only the updated gateway runtime and restart that gateway when approved.
Keep the collector/dashboard/source/AIU configuration unchanged; health checks
alone still do not establish Azure delivery or correct project grouping.

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
