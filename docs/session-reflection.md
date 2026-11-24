# Daily session reflection

New installations keep **inference and the daily job disabled until explicitly
enabled**. The current pilot was explicitly enabled after the initial metadata
preview: all new projects under the approved projects root are included, and the
first scheduled assessment is deferred until the next 09:00 local run.
The [README](../README.md#daily-session-reflection) shows the data flow.

## 1. Initialize once and inspect the preview

From the repository root, using the existing Python environment:

```bash
.venv/bin/python -m tools.session_reflection init \
  --project personal/copilot-otel=/absolute/path/to/copilot-otel
.venv/bin/python -m tools.session_reflection preview
.venv/bin/python -m tools.session_reflection status
```

Replace the path. Additional `--project category/name=/absolute/path` entries
must be explicitly approved; overlapping roots are rejected. The default source
is VS Code Insiders on macOS, with native runtime records under `~/.copilot`.
Other storage locations can be supplied with `--editor-data` / `--copilot-home`
at initialization; that does not establish support for other clients.

### Enable all projects in one approved tree

```bash
.venv/bin/python -m tools.session_reflection configure-root \
  --projects-root /absolute/path/to/projects --execute
.venv/bin/python -m tools.session_reflection preview
```

This includes future projects automatically, not only folders present during
setup. Project aliases follow `<category>/<project>` under this root; a project
directly under the root uses `projects/<name>`. Nested workspaces share their
top-level category/project alias. This is assessment routing, not proof of native
telemetry attribution.

The root expansion records a fresh cutoff. Existing explicit project approvals
keep their earlier cutoff; all other projects exclude sessions started before
root approval. Repeating the same command does not reset either cutoff or budget.
Sources outside the root, similarly prefixed sibling folders, and symlinks
escaping it are not authorized. All projects share the same five-attempt budget.
Do not enable a whole tree unless its conversation-text scope is approved for the
configured Azure model.

Initialization writes an owner-only local policy and fixes a creation-time
cutoff. Reinitialization is refused: it cannot reset the cutoff or request ledger.
Preexisting sessions stay excluded even if archived or resumed later.
Do not delete or edit the ledger/cutoff to bypass these boundaries.

Preview prints only source IDs, project aliases, revision, projected event/character
counts, digests, and eligibility reasons. It does not print message text or make
network requests. No eligible sessions is normal immediately after initialization.
Create a **new real session** in an approved project and mark it Done/archive
before expecting a capture. Do not copy old history into a new session as backfill.

## 2. Understand the captured input

The adapter accepts only the tested local Agent Host default-chat family:
catalog schema 22 / payload 2, mapping schema 20, and runtime event version 1.
It uses the persisted host-to-SDK identity mapping, not matching IDs by guesswork.
Native stores are opened read-only; snapshots must remain stable across reads.

Allowed input:

- User/assistant message text, excluding assistant analysis/reasoning.
- Deterministic tool completion/success flags, **not proof that tests passed**.
- Session identity/revision, partial-coverage notice, and deterministic counts.

Omitted: tool names/arguments/output, attachments, native file reads, model
reasoning, injected context blocks, and fenced code. Detected key/token patterns
block capture. These checks are **not a general secret or semantic redactor**:
ordinary prose, inline code, or pasted text can still contain sensitive details.
Request bodies pass to Azure CLI over standard input, not process arguments.

Review the content scope before enabling Azure inference. Conclusions cannot
claim independent verification from omitted tool output.

Active turns, pending tools/hooks/external requests, unanswered user messages,
partial streams, unsupported events/versions, multiple chats, and changing sources
are deferred or reported explicitly. Ambiguous cancellation remains deferred.
The adapter does not prove desktop/CLI/remote/subagent/compacted-history coverage.

## 3. Explicitly enable and run

**Do this only after preview review and approval to send the allowed message
projection to the configured Azure model.** Configure [.env.example](../.env.example)
as described in [Azure setup](azure-setup.md); use Azure CLI identity, not keys:

```bash
az login
.venv/bin/python -m tools.session_reflection enable --execute
.venv/bin/python -m tools.session_reflection run --execute
.venv/bin/python -m tools.session_reflection status
```

The synthetic smoke opt-in flag does not enable real inference. The local
reflection policy is the separate real-inference gate.

| Limit | Behavior |
| --- | --- |
| 5 attempts per local day | Reserved durably before requests; failed/interrupted attempts count |
| 20,000 request characters | Includes instructions/schema/metadata; oversized input is deferred, never truncated |
| 2,000 output tokens | Tool-free Responses API call, `store=false`, no automatic retries |
| 64 MiB reflection state | Explicit quota failure rather than unbounded capture |

These are resource bounds, not a financial cap or provider-side spending guarantee.
The exact prepared request is retained with the normalized snapshot. An unchanged
request is not resubmitted, including after failure or an uncertain crash. A new
source revision/model/rubric can form a different request, still within daily limits.
There is deliberately no automatic failed-request retry/reset command.

An exclusive lock prevents overlapping workers. Source/activity checks occur
before inference and before committing a result. Resume or change invalidates
local current status; later archived revisions supersede earlier results. Crash
recovery reports reserved requests as interrupted instead of retrying them.
There is no cross-process exactly-once or linearizable source-state guarantee.

Commands report no-new, no-eligible, deferred, failed, and completed states
separately. Failure output excludes source/model payloads. Inspect `status` and
the protected local files for request state; do not paste them into issues.
Output validation proves identity/citation integrity, not truthful interpretation.

## 4. Optional daily schedule

After a manual run has been reviewed:

```bash
.venv/bin/python -m tools.session_reflection install --execute
```

Installation defers the first scheduled assessment until the **next 09:00 local**
run, never immediately on install. The user LaunchAgent then runs at **09:00 local**, with login and hourly triggers for
missed-run catch-up. Before 09:00 it does no inference; after that it processes at
most one scheduled sweep per local date. Sessions archived after the day's sweep
wait until the next day. Manual runs remain possible within the same daily budget.
The Mac is not woken; an hour is not a guaranteed wake-time latency.

The job uses this checkout and virtual environment; keep both available.
It runs as the logged-in user, without Docker or an open editor. Azure CLI login
must remain valid. It **does not automatically publish assessments**.

The job is named **Copilot Daily Session Review**, not a generic Python/helper
label. Its file is `~/Library/LaunchAgents/Copilot Daily Session Review.plist`;
the plist also describes its purpose. Inspect the recognized job with:

```bash
launchctl print "gui/$(id -u)/Copilot Daily Session Review"
```

```bash
# Immediately prevent future model requests (does not interrupt an in-flight call):
.venv/bin/python -m tools.session_reflection disable --execute
# Remove only the recognized scheduled job, preserving all state:
.venv/bin/python -m tools.session_reflection uninstall --execute
```

Disable leaves the job installed but inference-gated. Uninstall prevents future
scheduled triggers, not a provider request already sent. Use status to inspect
recorded runs; the presence of a plist is not a live health signal.

## 5. Review and publish a derived summary

Results are **local and unreviewed** by default. Inspect the returned result ID's
JSON privately. If the conclusions are useful and supported, author a concise
derived summary yourself:

```bash
.venv/bin/python -m tools.session_reflection publish <result-id> \
  --summary "<reviewed derived findings, not source quotations>" --reviewed --execute
.venv/bin/python -m tools.session_reflection query <result-id>
```

The publisher rechecks source identity/activity/revision, rejects complete source
event quotations and detected code/key patterns, and sends only the typed
assessment columns plus your reviewed summary. No raw model prose is copied
automatically. These checks cannot certify that arbitrary prose is safe;
`--reviewed` is an explicit human content review, not a bypass for raw history.

Publication writes an attempting receipt before upload. If acceptance is
uncertain, **query the receipt, never resend**. A previously attempted result
cannot be republished by this command. Receipts stay local; no cloud resources
or model deployments are created.

The existing dashboard includes origin `archived_session_assessment` while
excluding synthetic smoke. `SessionEndedAt` is null because a trustworthy session
end timestamp has not been established. The dashboard time picker uses assessment
creation time, not an invented source end time.

Cloud rows are historical assessments of archived revisions. Later local resumes
are **not synchronized as cloud invalidation events**; dashboard rows are not
proof that the source remains archived. Native telemetry remains independently
unattributed/accounting-unverified.

## Data and remaining acceptance checks

Owned state is under
`~/Library/Application Support/copilot-otel/reflection`, separate from the
collector queue. Policy/manifest files and payloads are owner-only.

- Normalized snapshots and exact model requests expire 30 days after capture.
- Local assessments and publish receipts expire 90 days after assessment creation.
- Every command prunes expired owned data; scheduled runs do so once installed.
  With the job uninstalled and no commands running, nothing performs cleanup.
- Expiry uses ledger timestamps, not filesystem touch time. Request-key/budget
  tombstones remain without content so retention cannot trigger automatic retries.
- Operational errors contain no payloads; scheduled stderr is local and not yet
  rotated. Quota exhaustion stops capture and requires operator attention.
- Native/synced histories and Azure rows are never deleted by local pruning.
  Cloud assessment retention is separately configured to 90 days.

Fixture-backed capture, budget, replay/resume, crash-state, retention, scheduler
configuration, and publisher tests pass locally. **First eligible real capture,
real inference/summary query-back, full editor restart, actual sleep/wake behavior,
and broader quality/access testing still require live acceptance.**

Synthetic assessment fixtures remain as regression tests. The standalone demo
has been removed; optional synthetic Azure/collector smoke commands remain only
as [manual troubleshooting diagnostics](troubleshooting.md#manual-synthetic-diagnostics),
not routine uploads or substitutes for real-session acceptance. Existing
synthetic cloud records expire under configured retention.
