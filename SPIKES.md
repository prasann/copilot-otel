# Feasibility spikes

**Status: S1/S2 investigated on 2026-11-23; core capture/archive feasible for the
tested scope.** Restart/concurrency remain implementation acceptance tests.
See [session capture findings](docs/spikes/session-capture-findings.md).

Validate the assumptions in [PLAN.md](PLAN.md) before committing to implementation.
The first decision is whether passive, finished-session reflection works for the
primary client, VS Code. A working CLI example is not a substitute.

## Decision sequence

| Spike | Question | Time box | Prerequisite | Initial state |
| --- | --- | --- | --- | --- |
| S1 | Can we capture useful VS Code history passively and locally? | 2 hours | None | Go for tested local default-chat scope |
| S2 | Can we identify explicitly finished revisions without guessing? | 2 hours | S1 passes | Go for archive-based feasibility; reliability tests deferred |
| S3 | Can the desktop app use the same approach without duplicate sessions? | 90 minutes | S2 passes | Waiting |
| S4 | Can a bounded model assessment produce trustworthy, useful findings? | 3 hours | S2 feasible; execution requires inference access/cost approval | Next: fixtures/rubric |
| S5 | Can a small native daily worker recover and manage data safely? | 2 hours plus one overnight/wake observation | S4 passes | Waiting |
| T1 | Can metadata-only telemetry be attributed without dashboard joins? | 2 hours | Separate controlled telemetry approval | Deferred; independent |

Time boxes are investigation limits, not delivery estimates. Stop at expiry and
record evidence and the unresolved blocker. No automatic extension of scope.
S3 and S4 are independent after S2; app coverage need not delay a VS Code-only pilot.

**Spend the first four hours on S1/S2 only.** If the primary capture/lifecycle
contract fails, stop downstream work. If it passes, review the evidence before
starting S3-S5. T1 cannot make an unsuccessful reflection approach viable.

## Common rules

- Use newly created synthetic sessions in a dedicated fixture workspace, with
  unique markers and known session IDs. Never enumerate/read unrelated histories
  to find examples. Inspect only the necessary configuration/schema metadata.
- Record installed editor/app/runtime versions and harness. Inspect actual sync
  settings before creating fixtures; do not enable sync or upload content to solve
  a gap. Stop for approval if isolation requires changing existing user settings.
- Synthetic interactions may use the normal Copilot inference path to create test
  sessions. This is separate from permission to assess real histories with a model.
- No production capture, global hooks/extensions, persistent LaunchAgents,
  dependency installation, model downloads, cloud provisioning, or resource
  deletion as incidental spike setup. Ask for the relevant approval if needed.
- Prefer existing tools and the smallest disposable probe. Native history is
  read-only. Keep raw fixtures, snapshots, and probe outputs in protected temporary
  storage outside Git. Only sanitized findings and deliberately authored,
  non-sensitive fixtures may be committed.
- After each spike, remove only its named temporary artifacts/processes. Preserve
  useful evidence deliberately, with a recorded expiry. Do not delete native
  sessions or synced copies without explicit approval.
- Do not turn a spike into the installer, dashboard, extension, or batch platform.
  A successful probe validates a specific assumption, not the whole system.

## S1. Passive VS Code capture

**Hypothesis:** an unattended local reader can retrieve the content needed for
reflection from the installed VS Code Copilot harness without manual export,
cloud sync, or unsupported access to another chat participant.

### Experiment

1. Establish the exact supported read/export interfaces. If none is adequate,
   inspect native storage metadata for a proposed read-only file/SQLite adapter.
   Record whether each dependency is documented, internal, or experimental.
2. Create two isolated synthetic repositories with the same folder basename and
   distinct configured project aliases. In each, create a session containing
   distinct user/assistant markers and one harmless tool invocation.
3. Keep one session open while reading a consistent snapshot; then close and
   reopen the editor. Verify the same session identity and observable revision.
4. Recover the known IDs only. Compare the reader output with a one-time native
   JSON export as a reference, not as the final ingestion mechanism.
5. Test a controlled missing/unsupported-schema fixture and a partial/compacted
   record where available. Do not force expensive compaction to finish the spike;
   record untested coverage and withhold that capability.

### Pass criteria

- Both sessions are captured without per-session export, custom prompt, or CLI.
- All authored user/assistant markers present in the reference are recovered in
  order, or the source is explicitly classified as incomplete and not claimed as
  full-transcript capture. Critical content gaps make the hypothesis fail.
- Session identity survives editor restart; the two projects never cross-label.
- Source reads are non-mutating; missing/unsupported data produces an explicit
  failure, not a successful empty transcript.
- A documented capture contract identifies exact fields, format/version checks,
  coverage limits, and maintenance risk. Any internal adapter needs review before
  its use in implementation.

**Stop/no-go:** only manual export works, access requires cloud sync, useful
content is unavailable, or capture requires brittle UI scraping/private commands
without an accepted maintenance path.

**Deliverable:** sanitized capture contract, fixture/reference comparison, exact
probe invocation, and supported-interface versus adapter recommendation. Do not
build an extension merely to wrap the same inaccessible data.

## S2. Finished-session and revision detection

**Hypothesis:** source evidence distinguishes eligible completed/archived
revisions from active, merely quiet, interrupted, and resumed sessions.

### Experiment

Use the S1 reader and an explicitly recorded lifecycle matrix:

| Scenario | Expected eligibility |
| --- | --- |
| Agent finishes one turn; conversation remains open | Deferred |
| Conversation is idle, including beyond a proposed daily cutoff | Deferred |
| Tool fails or a run is cancelled, without explicit session closure/archive | Deferred |
| Editor closes/restarts without verified session completion | Deferred |
| Source reports verified explicit session completion | Eligible only with no active run |
| Session is explicitly archived | Eligible only with no active run |
| Eligible session is resumed/unarchived | Earlier revision is no longer current/eligible |
| Session changes while an assessment stub is running | Do not publish the stale revision as current |
| Resumed session is explicitly completed/archived again | New revision supersedes, not adds another completion |

For each applicable row, record the source field/event, reason, timestamp,
revision, and observed behavior. Unsupported rows remain unsupported, not inferred.
If lifecycle evidence is missing, evaluate one narrowly scoped, temporary hook
only after reviewing its payload and getting permission for its installation.
An `agentStop`/`Stop` event alone cannot pass this spike.

### Pass criteria

- Every exercised negative scenario remains deferred.
- At least one real primary-client completion/archive path supplies verifiable
  eligibility; all positive paths claimed as supported pass.
- Resume and concurrent-change scenarios prevent stale publication and duplicate
  completion counting in the probe.
- A restart does not lose evidence or manufacture completion from process exit.
- State provenance can be explained without a model, idle heuristic, filesystem
  modification time alone, or assuming that "tests passed" means session finished.

**Workflow check:** if only explicit archive works, report that limitation.
Technical feasibility does not mean passive workflow feasibility: requiring the
user to remember a new archive action for every session is a conditional result,
not an unconditional go. Revisit that tradeoff with the user; do not silently relax
the selected completion policy.

**Stop/no-go:** no dependable primary-client finish/archive signal, or normal
turns/process exits cannot be distinguished from eligible closure.

**Deliverable:** lifecycle truth table and evidence, supported paths, resume/race
probe output, and a hard go/no-go for strict finished-session analysis.

## S3. Copilot desktop app coverage

**Hypothesis:** local app sessions have usable content/lifecycle evidence, and
copies visible in other clients can be reconciled without guessing.

### Experiment and pass criteria

1. Create a known synthetic local app session and exercise the applicable S1/S2
   cases. Record whether standalone app chats differ from repository sessions.
2. Verify actual local storage and exact IDs rather than assuming app/CLI records
   have identical schemas. Do not enable cloud sync to make a local record appear.
3. Where the existing configuration exposes the same session in multiple clients,
   prove canonical identity and one logical result. If not available, mark
   cross-client deduplication unproven; do not manufacture a duplicate by syncing.
4. Distinguish locally complete sessions from remote/cloud-only or partial records.
   Unavailable data and ambiguous duplicates must remain excluded and counted.

**Pass:** local content and strict eligibility work for each claimed app session
type; duplicate handling is either proven or transparently excludes overlap.
**Defer:** app unavailable, remote-only data, or unsupported lifecycle. This must
not invalidate a proven VS Code-only pilot.

**Deliverable:** per-client/type coverage matrix and shared versus separate adapter
decision. No requirement to support CLI or cloud sessions in this spike.

## S4. Evidence-backed assessment quality

**Hypothesis:** a small, tool-free model call adds useful interpretation beyond
deterministic facts without inventing success, effort, or productivity.

### Prerequisites and experiment

- Author eight small synthetic transcript fixtures with expected evidence and
  permissible conclusions before testing models:
  1. Visible test success with a narrowly evidenced outcome.
  2. Agent claims success but no verification is visible.
  3. Repeated failed attempts followed by a visible correction.
  4. Deliberate user scope change, not an agent failure.
  5. Multiple tasks in one session, including unresolved work.
  6. Resumed work with an earlier revision that must not be double-counted.
  7. Missing/compacted tool output requiring explicit uncertainty.
  8. Transcript prompt injection asking the assessor to run tools or invent results.
- Include explicit closure metadata independently of the transcript's apparent
  outcome. Model interpretation does not decide eligibility.
- Build one deterministic-facts baseline and one concise, versioned assessment
  rubric with evidence references and structured output. Do not build an agent.
- Evaluate one feasible local candidate and one Azure candidate on the same
  fixtures, twice each. First confirm available hardware/runtime, model licenses,
  endpoint access, and an approved synthetic inference cost ceiling. Missing
  candidates are blocked, not automatically installed/downloaded/provisioned.
- Include a representative longer synthetic input to check context limits,
  truncation reporting, and latency; it is not permission to read real sessions.

### Pass criteria

- All responses validate against the output contract and all cited evidence exists.
- Zero tool execution, invented evidence, unsupported success/time-saved claims, or
  obedience to transcript instructions across both runs of the eight fixtures.
- At least seven of eight fixtures per run have the expected task/outcome/friction
  interpretation against the prewritten answer key; all safety/unknown cases must
  pass regardless of the aggregate score.
- Record wall time, token use/estimated cost, local memory pressure where relevant,
  and quality failures. Do not call tokens or a model multiplier actual billed cost.
- In a one-time review, the user finds at least one evidence-backed recommendation
  useful beyond the deterministic baseline. Passing the fixture score alone does
  not establish usefulness or broad model reliability.

**Decision:** choose one initial provider only if it meets the evidence safeguards
and agreed resource/cost limits. Revise the rubric within the time box if needed;
keep the final evaluation fixtures separate from tuning examples. A comparison is
incomplete if only one candidate ran; a single-provider pilot needs explicit
approval rather than claiming the comparison passed.

**Stop/no-go:** interpretations remain unreliable or add no useful information.
Keep deterministic facts/native Chronicle as the simpler alternative. No model
training, deployment, or real-content upload is part of this spike.

**Deliverable:** synthetic fixture set, rubric/version, scored outputs, resource
and cost comparison, and provider/content-approval recommendation.

## S5. Daily batch and local data lifecycle

**Hypothesis:** a small native process can produce a daily local report reliably
without Docker, an open editor, or an always-on LLM agent.

### Experiment

Use synthetic captured records and a deterministic assessor stub. A temporary,
explicitly approved LaunchAgent may test scheduling; do not install a permanent
service. Exercise:

- Duplicate discovery, overlapping triggers, changed/resumed revisions, and worker
  termination between result creation and manifest commit.
- A missed daily schedule, actual sleep/wake and login behavior, and catch-up on
  the next available trigger. Simulated time alone does not prove macOS wake behavior.
- Provider failure, malformed output, disk/quota exhaustion, and source-schema error.
- Retention with an injected clock: content at 30 days, results/reports at 90 days,
  no expiry reset on rediscovery/render, and references to expired evidence.
- Explicit exclusion/deletion of a fixture while queued, without modifying native
  Copilot history or leaving owned payload copies.

### Pass criteria

- One current result per canonical session revision/rubric/model version; no duplicate
  completion totals after retry/crash.
- Active or changed revisions are deferred; no half-written report is published.
- After injected failure, status identifies blocked/failed/pending work and recovery
  completes without manual database repair or an empty success report.
- Actual missed-run recovery is observed once, with Docker stopped and editor
  closed. Record elapsed delay rather than promising an exact wake-time guarantee.
- Retention and deletion affect only system-owned paths; logs contain no fixture
  content, files have owner-only access, and the final daily report opens in VS Code.

**Deliverable:** disposable probe, recovery/retention observations, measured
resource use, and minimal production-worker recommendation. Remove its temporary
LaunchAgent/process after the wake observation.

## T1. Optional telemetry attribution proof

This is the separate telemetry gate from [PLAN.md](PLAN.md), not a prerequisite
for reflection. Keep it deferred until its controlled capture is approved.

Use two concurrent synthetic projects, one branch change, a resumed session, and
one subagent case where supported. Identify one accounting layer and its native
project/source identity. Reconcile attributed plus unattributed tokens to known
reference totals, including a replayed record. Verify a synthetic forbidden-content
marker never reaches the metadata-only output.

**Pass:** exact totals on the controlled dataset, no cross-project leakage or
parent/child double counts, replay does not inflate totals, and no query-time
session mapping join is required. Report unsupported model-level breakdowns.
**Fail:** identity requires guessing, global active-project labels, or the previous
mapping joins. Defer that telemetry scope; do not block reflection or deploy Azure.

**Deliverable:** accounting contract, sanitized reference records, reconciliation
results, and client-specific go/no-go. No backend provisioning or dashboard work.

## Evidence and decision record

Update this record only after execution; elapsed time and observed results must
not be replaced by expectations from documentation.

| Spike | Result | Evidence | Decision |
| --- | --- | --- | --- |
| S1 | Go, scoped | [Findings](docs/spikes/session-capture-findings.md) | Native capture demonstrated for tested version/default chats; wider coverage unproven |
| S2 | Go, scoped | [Findings](docs/spikes/session-capture-findings.md) | Archive/resume demonstrated and workflow accepted; restart/concurrency retained as acceptance tests |
| S3 | Not run | None | App coverage unproven |
| S4 | Not run | None | Inference remains blocked for real content |
| S5 | Not run | None | Scheduling/recovery unproven |
| T1 | Deferred | None | Telemetry remains disabled |

For each completed spike record: tested versions/harness, date, exact procedure
and invocation, fixture IDs, observed versus expected results, elapsed time,
evidence location/expiry, limitations, and cleanup. Select **go**, **conditional**,
**no-go**, or **blocked**; unsupported capabilities are never silently counted as
passing. Conditional results require a reviewed scope/policy change.

### Overall decision

- **Current:** primary local capture and explicit archive demonstrated on synthetic
  VS Code fixtures; accepted as technically feasible for that scope.
- **Next:** S4 synthetic fixtures/rubric, then approved provider evaluation.
- **After S1/S2:** approve further spikes only if passive VS Code capture and strict
  eligibility are supported under an acceptable maintenance/workflow tradeoff.
- **Before production rollout:** require useful assessment evidence from S4 and
  reliable local operation from S5, including restart/concurrency acceptance tests.
  App and telemetry support remain separate scope decisions.
