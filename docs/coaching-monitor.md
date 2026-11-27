# Daily status email (optional)

The separate `infra/coaching-monitor.bicep` deployment adds a **Consumption Logic
App**, an Azure Communication Services (ACS) sender with a free Azure-managed
domain, and a metadata-only completion table/DCR in the existing workspace.
There is no Function, storage account, always-on compute, email connector, LLM,
or dependency on the Mac for monitoring.

The recurrence is **daily at 15:00 Asia/Kolkata (09:30 UTC)**, explicitly configured
as `India Standard Time`, not the developer host's timezone. It still runs if the
Mac is asleep, offline, or never started the job. The deadline snapshot is fixed at
15:00 IST; a later coaching run cannot turn a missed deadline into success. No
paid coaching retry is started.

`received` requires an explicit successful completion record and the matching
number of distinct dashboard rows. Zero-item completions are valid. No run,
failed execution, missing completion signal, and incomplete publication produce
`missing/failed`. Query/access/partial-response failures produce `unknown`,
never `received`. Log ingestion latency may conservatively produce a missing
status when a local upload was accepted just before the deadline.

## Private recipient configuration

There is **no recipient default** in the repository. Supply the required secure
`recipient` deployment parameter in a private parameter file **outside the
checkout**, not in a checked-in file or shell-history command. Azure stores it in
the Logic App's secure workflow parameter **recipient**
(`Microsoft.Logic/workflows/<name>-daily`, `properties.parameters.recipient`).
To change it, edit the private parameter file and redeploy. The workflow's
`parameters.recipient` declaration contains only its type, never a default address.

Email action inputs and outputs are hidden from workflow run history. Operators
with Azure resource access and the email provider can access necessary delivery
information; restrict resource/log access accordingly. Do not copy deployment
parameters, exported resource settings, or delivery logs into Git.

## Deploy and activate

Deployment needs resource-write and role-assignment permission within the chosen
resource group, not tenant administrator consent. The workflow identity receives
Log Analytics Reader on the existing workspace and Contributor on its own ACS
resource (ACS email Entra authentication requires read/write on that resource).
The local publisher receives Monitoring Metrics Publisher on the new status DCR.
There are no stored SMTP passwords, API keys, or third-party connectors.

Private parameters must include `workspaceName`, `publisherId`, and `recipient`.
The optional `emailDataLocation` defaults to India and `location` to the resource
group's location. `firstRun` is the schedule's local anchor without a `Z` suffix;
it defaults to today's midnight, and past occurrences are skipped. Do not set it
to tomorrow at 15:00: Azure can skip the first day when a future start is less than
24 hours away. Verify the trigger's `nextExecutionTime` after enabling or testing.
Deployment is disabled by default:

```bash
az deployment group create --resource-group <group> --name coaching-monitor \
  --template-file infra/coaching-monitor.bicep \
  --parameters @/private/path/monitor.parameters.json --output none
```

Install the publisher using deployment outputs `statusEndpoint` and `statusRule`:

```bash
.venv/bin/python -m tools.coaching monitor-install --execute \
  --endpoint <statusEndpoint> --rule <statusRule>
```

This configures private local `policy.json` and switches the existing owned
LaunchAgent to a stable source/virtualenv snapshot under the state directory's
`runtime/`, independent of a development worktree. The 09:00/hourly schedule is
preserved; `RunAtLoad` is disabled to avoid a paid catch-up during configuration.
Refresh the snapshot with the same command after future worker changes. No
Chronicle or model call is made by installation.

`python -m tools.coaching signal <existing-run-id>` verifies and signals an already
published local receipt without running coaching. A signal is timestamped when
sent; this command cannot backdate a missed deadline. Signal failures return an
explicit warning and nonzero exit; the daily email cannot claim receipt without
the signal. `signals/` retains content-free local signal receipts for 90 days.

For live testing, redeploy with `enabled=true` and `testOutcome=received`. Run the recurrence manually from the Azure Logic App
Overview, then repeat with `testOutcome=missing/failed`. Emails have explicit
**[TEST]** subjects and separate deterministic operation IDs. A second run of
either test checks the existing operation and does not send another email.
Finally redeploy with `enabled=true`, `testOutcome=''`, and a past-midnight
`firstRun` anchor. Manual trigger tests can advance the next occurrence; reset the
anchor if necessary and confirm the native `nextExecutionTime` is the intended
next 15:00 IST before considering activation complete.

The workflow serializes concurrent runs and uses one deterministic ACS Operation-Id
per IST day. HTTP retries reuse the same ID; ACS rejects duplicate submissions
with `OperationIdAlreadyExists`, and the workflow resumes polling the original
operation. Query results cannot cause a second daily message. Inaccessible or
failed operations fail visibly. ACS retains operation status for at least 24 hours; do not
manually resubmit historical runs as a backfill mechanism. Email send operations
are polled until ACS reports `Succeeded`, not assumed delivered from HTTP 202.
Recipient-server delivery evidence is in `ACSEmailStatusUpdateOperational` in the
workspace. Inbox placement still depends on the recipient's mail system.

## Cost and stopping

At one small email/day, ACS email is about **US$0.008/month** plus negligible data
transfer ($0.00025/email and $0.00012/MB). Consumption Logic App actions are
usage-billed with no always-on charge; one short workflow/day and tiny log volumes
should keep the monitor **under US$1/month**, excluding existing infrastructure
and coaching costs. This is an estimate, not a billing guarantee.

To stop immediately: **Azure portal > Logic App > Overview > Disable**, or:

```bash
az logic workflow update --resource-group <group> --name <name>-daily --state Disabled
```

Monitoring remains enabled until explicitly disabled; there is no expiry.
To remove it later, delete only this deployment's workflow, ACS/email service and
domain, status DCR/table, diagnostic setting and scoped role assignments. Keep the
existing workspace, dashboard and coaching ingestion rule. Remove `status_endpoint`
and `status_rule` from local policy to stop metadata signals. Disabling email does
not disable the coaching job.
