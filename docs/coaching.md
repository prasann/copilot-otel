# Daily Copilot coaching

The coaching worker turns Copilot CLI's own session analysis into dashboard rows.
It never reads conversation files itself.

```text
copilot -p "/chronicle tips"     ─┐
copilot -p "/chronicle standup"  ─┴─► local reports ─► Azure model ─► validated items ─► CopilotInsights_CL
```

## Data boundaries

| Step | Sees | Leaves the Mac to |
| --- | --- | --- |
| `/chronicle tips`, `/chronicle standup` | Copilot's local session store (`~/.copilot/session-store.db`), including VS Code Agent Host sessions | Copilot's model service, which already holds those sessions |
| Normalization | Only the two Chronicle reports (~1-2 KB each) | Your Azure OpenAI deployment (`store: false`, strict JSON schema, 2,000 output tokens) |
| Publishing | Validated items only | Log Analytics `CopilotInsights_CL` via the insights ingestion rule |

Chronicle runs non-interactively with `-s`, `--no-ask-user`,
`--no-custom-instructions`, `--disable-builtin-mcps`, `--sync-session=off`, and
`--max-ai-credits 30`, from an empty working directory. No `--allow-*` flags are
passed and `COPILOT_ALLOW*` variables are removed, so Copilot can only use its
default read-only session tools.

Reports containing private-key or GitHub/AWS token patterns stop before the Azure
call. Items are rejected unless they match the schema: a fixed category, a title
up to 160 characters, a detail up to 400 characters, and an empty or `owner/repo`
repository. Newlines, code fences, local paths, and secret patterns fail the run.
Nothing is published from a failed run.

## Setup

1. Provision the current infrastructure (`azd provision`) so the `INSIGHTS_*`
   outputs exist.
2. Fill the Azure model settings in `.env` from `.env.example`.
3. Sign in to `copilot`, `az`, and `azd`.

```bash
.venv/bin/python -m tools.coaching enable --execute
.venv/bin/python -m tools.coaching run --execute
.venv/bin/python -m tools.coaching verify <run-id>
.venv/bin/python -m tools.coaching install --execute
```

`enable` caches the ingestion endpoint, rule, and workspace IDs in the local policy
so the scheduled job does not need `azd`. Re-run it after reprovisioning.

## Schedule

The LaunchAgent **Copilot Daily Coaching**
(`~/Library/LaunchAgents/Copilot Daily Coaching.plist`) runs at 09:00 local time
and checks hourly, so a missed run catches up after wake or login. Installation
defers the first scheduled run to the next 09:00. The job does not wake the Mac.

## Limits and cost

- One attempt per local day, scheduled or manual. A failed attempt is not retried
  automatically; `run --execute --retry` spends another attempt deliberately.
- Each Chronicle call is capped at 30 Copilot AI credits; the first real run used
  about 10 credits for both reports. `status` shows recorded credits per run.
- One Azure model call per run (about 800 input and 1,100 output tokens observed).

## Recovering a failed upload

If Chronicle and the model succeeded but the upload failed (for example, a rule
change still propagating, which took about 7 minutes after provisioning), resend
the validated rows without spending more credits:

```bash
.venv/bin/python -m tools.coaching publish <run-id>
.venv/bin/python -m tools.coaching verify <run-id>
```

Dashboard queries deduplicate by `RunId`, `Kind`, and `Rank`, so a resend that
overlaps an earlier accepted upload does not double-count.

## Local state and retention

State lives in `~/Library/Application Support/copilot-otel/coaching` (mode 0700):

| Path | Content | Kept |
| --- | --- | --- |
| `reports/` | Raw Chronicle reports | 30 days |
| `results/` | Validated items, credits, model token usage | 90 days |
| `receipts/` | Exact rows sent and their upload status | 90 days |
| `runs.db` | One row per attempt | 90 days |
| `worker.log` | Scheduled-run JSON output (no report text) | Not rotated |

Cloud rows are retained for 90 days.

## Stop or remove

```bash
.venv/bin/python -m tools.coaching disable --execute
.venv/bin/python -m tools.coaching uninstall --execute
```

Uninstall removes the LaunchAgent and keeps local state. Delete the state folder
manually if you no longer need it.
