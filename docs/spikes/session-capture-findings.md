# S1/S2 findings: VS Code session capture and explicit archive

**Date:** 2026-11-23

**Decision:** capture/archive technically feasible for the tested VS Code Copilot
harness. Not a production-readiness claim; restart and concurrency checks remain
implementation acceptance tests, not make-or-break feasibility blockers.

The useful result is that native session content and explicit archive state are
available locally, without per-session export or a custom extension. There is no
demonstrated automatic "the user's goal is finished" signal. The user confirmed
that marking sessions Done/archive fits their workflow.

## Scope and environment

- VS Code Insiders 1.148.0, commit
  `0d15d4d60248e35fac1a3fe234d74ed780e15691`, macOS arm64.
- Bundled Copilot extension `0.76.2026112007`; fixture runtime records identify
  Copilot `1.0.114`.
- Actual exported participant: `agent-host-copilotcli`, the core VS Code Agent
  Host participant, not the legacy Local participant or an interactive CLI.
- Two synthetic repositories with the same folder basename, distinct alpha/beta
  paths, no remotes, and known fixture prompts/files.
- Workspace history sync explicitly off and local indexing explicitly on.
  The user confirmed the Copilot target and sync-off UI state. This is not an
  audit of account-level sync, remote services, or all client privacy settings.
- Approximately 45 minutes from initial investigation through focused guard
  checks, including user-operated fixture UI and export identification.
- Only fixture-scoped catalog queries and identified native session files were
  read. Existing unrelated conversation contents were not inspected.

Initial capture references were one-time JSON exports. Those exports were used
only to identify/compare the synthetic prompts and final responses. Subsequent
reads, lifecycle checks, and window-closed capture used native storage directly.

## Actual capture contract observed

This harness differs from the older extension-owned path considered in the plan:

| Store | Observed role | Tested schema |
| --- | --- | --- |
| `<VS_CODE_USER_DATA>/User/globalStorage/agent-host.db` | Catalog identity, configured project URI, explicit `isArchived`, source revision, catalog payload hash | SQLite `user_version=22`; `sessions_v2` payload wrapper contains `data` |
| `<VS_CODE_USER_DATA>/agentSessionData/<host-id>/session.db` | Explicit default-chat provider mapping | SQLite `user_version=20`; `session_metadata` key `defaultChatProviderData` contains `sdkSessionId` |
| `<COPILOT_HOME>/session-state/<sdk-id>/events.jsonl` | Ordered native user/assistant/tool and lifecycle events | First event `session.start`; its data has `version=1`, runtime identity, context/cwd, and runtime version |
| `<COPILOT_HOME>/session-store.db` | Targeted discovery by exact fixture cwd | Its session IDs were runtime IDs, not VS Code host IDs; no explicit archive column was observed |

The catalog stores a `copilotcli:/<host-id>` identity while the session tools/UI
use `ahp-session:/<host-id>`. The corresponding SDK UUID is different. Use the
persisted mapping; do not join these IDs by equality or choose the newest session.

The inspected catalog payload includes `isArchived`, `project.uri`, and
`workingDirectories`, but no demonstrated persisted live-active-run flag. The
host's session tool can report `inProgress`; an unattended equivalent remains to
be designed/verified. A runtime lock file existed even for idle sessions, so mere
lock-file existence is not an active-run test.

The bundled extension also has a `vscode.metadata.json` archive path, but that
file was absent for these core Agent Host fixtures. It is not the tested adapter.
Do not generalize these findings to the legacy Local harness, the desktop app,
Stable, remote/cloud sessions, or future formats.

### Narrow read-only probe

The disposable probe used standard Python `sqlite3`, with `mode=ro` connections
and an explicit catalog read transaction. Its catalog query was restricted to
each known synthetic host identity:

```sql
SELECT payload, source_revision
FROM sessions_v2
WHERE session_uri = ?;
```

The identity lookup in that fixture's session database was:

```sql
SELECT value
FROM session_metadata
WHERE key = 'defaultChatProviderData';
```

It validated versions, exact project URI/cwd, SDK identity, event IDs, and complete
JSONL lines. It compared the exported user prompt and final assistant reply to
the corresponding native message events. It reread catalog state and event bytes
to reject a source that changed during capture.

Separate hashes were used for the raw event stream and the user/assistant
conversation projection. Operational records such as shutdown and usage
checkpoints can change the raw stream without changing the conversation. Neither
that limited message projection nor the catalog source revision alone is a final
assessment key: an assessment that uses tool evidence must include that evidence
in its own versioned digest.

The source databases were not modified. This probe is an internal-schema adapter,
not proof of a supported public unattended export API or a production reader.

## Observed results

| Experiment | Actual observation | Interpretation |
| --- | --- | --- |
| Initial alpha/beta capture | Both exact exported prompts and final replies were recovered natively; project paths stayed distinct despite identical basenames | Core content capture and project separation demonstrated |
| Completed turns, conversations left open | Both fixtures remained `isArchived=false` after their marker replies | Turn completion is not archive/session completion |
| Alpha explicitly marked Done/archive | Catalog revision changed 6 to 7; `isArchived` changed false to true; raw transcript stayed unchanged | Archive evidence is independent of transcript content |
| Alpha unarchived and resumed | Same host/SDK IDs; new resume marker appeared; revision became 11 and archive flag returned to false | Previously archived snapshot is stale; identity is stable across resume |
| Alpha rearchived | Revision became 12 with archive true | Same logical session, new eligible candidate revision; not another completed task |
| Both fixture windows closed and reopened | Original markers/IDs retained; alpha remained archived, beta remained unarchived | Fixture-window persistence demonstrated; full application/host restart was not exercised |
| Beta window closure/runtime shutdown | A `session.shutdown` appeared while beta remained unarchived | Shutdown cannot be treated as explicit task/session completion |
| Beta running synthetic `sleep 45` | Host tool reported `inProgress`; local catalog archive flag was false | Running fixture correctly remains deferred |
| Beta marked Done/archive during that run | After the action the host reported `idle,archived`; runtime recorded `abort` with `reason=user_initiated` | Archive can interrupt work; it does not prove successful outcome |
| Active-archive timing detail | Sleep completed at 05:42:45.756Z; next assistant turn started at 05:42:45.766Z; abort at 05:42:46.297Z | The observed interruption was during the subsequent model turn, not proof that archive killed the sleep process |
| Repeated unchanged native capture | Conversation hashes were stable across repeated reads for both fixtures | Repeatability observed, not a production exactly-once guarantee |
| Missing known fixture | Explicit failure instead of empty success | Probe failure behavior verified |
| Unsupported schema / incomplete JSONL / catalog change during read | Focused injected tests rejected schema 999, a removed trailing newline, and a changed source revision | Read guards verified on synthetic faults; no real upgrade or publication race was simulated |

One additional beta fixture appeared during the user's test/export interaction.
It was kept distinct, not merged by workspace name or newest timestamp. Its
shutdown/resume/abort events were not used as a substitute for explicit archive.

## Make-or-break conclusion

### What is feasible now

- A small, version-gated, read-only adapter can capture new core Agent Host
  sessions created through the tested VS Code interface.
- Explicit Done/archive can serve as the user's declared analysis boundary.
- No new VS Code extension, CLI workflow, global capture hook, Docker service,
  cloud sync, or Azure deployment was required for these checks.
- Resume must invalidate prior eligibility and supersede an earlier assessment.
  Process exit, quiet time, and model claims must not substitute for archive.

### Remaining implementation checks and scope limits

1. **Active-run safety and publication:** an archived flag is only a candidate.
   The test did not observe every intermediate state while archive cancellation
   was happening. Prove conservative handling of pending tools/queued messages,
   archive cancellation lag, and an unarchive/change between the last source check
   and result publication. The injected read race is not that proof. Use immutable
   snapshots, pre-publication rechecks, and eventual supersession after resume.
   A personal retrospective does not require cross-process linearizability.
2. **Full restart:** verify the same contract after a complete VS Code/Agent Host
   process restart in a separate controlled run; reopening two windows while the
   main working window stayed open is insufficient.
3. **Coverage:** actual compaction, edited/dismissed messages, multiple chats and
   subagents, malformed-but-new schema variants, and remote sources remain
   unproven. Limit any pilot to demonstrated local default-chat coverage.
4. **Maintenance:** accept and document dependence on internal schemas, fail-closed
   upgrades, and fixture-based compatibility checks before choosing this adapter.
5. **Assessment usefulness:** S4 has not run. Accessible history does not establish
   reliable interpretation or useful recommendations.

**Decision after workflow review:** the primary capture/archive hypothesis is
supported for the tested scope, and explicit Done/archive is acceptable to the
user. Full restart and concurrency are normal reliability acceptance tests, not
reasons to delay synthetic assessment evaluation. Proceed to S4's fixture/rubric
work; actual provider execution needs endpoint/model and cost approval. Real-content
inference remains blocked. Telemetry stays independent and deferred.

## Cleanup and reproduction

The user requested findings to be retained but raw evidence to be removed.
The disposable probe, reference exports, fixture workspaces, and owned temporary
evidence are removed after validation. No persistent worker, hook, extension,
model runtime, or Azure resource was installed.

The user closed the two fixture windows. Native Copilot session records are not
automatically deleted by deleting fixtures; they remain archived in their
client-managed stores. Removing those records or any account-synced copies would
require explicit client deletion and a separate confirmation.

To reproduce, create fresh synthetic alpha/beta workspaces and use new known IDs:

1. In the Copilot target, read a harmless input file and return distinct exact
   user/file/reply markers; compare once with a native JSON export.
2. Query only the resulting fixture identities and validate the store mapping
   and schema contract above.
3. Capture unarchived, archived, resumed, and rearchived states; keep an unchanged
   second fixture as the negative control.
4. Close/reopen fixture windows, separately test full host restart, and exercise
   active archive and publication races without accessing unrelated history.

This document preserves findings, not raw transcripts or a complete replayable
evidence archive. No broad historical backfill is authorized.
