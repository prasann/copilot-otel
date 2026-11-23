# Session reflection: first development slice

Canonical requirements: [PLAN.md](../../PLAN.md).
Feasibility gates: [SPIKES.md](../../SPIKES.md).

## Objective

Prove that a bounded, tool-free assessor adds useful interpretation beyond
deterministic facts. Use synthetic sessions only. The target user experience is
one authenticated Grafana dashboard combining derived assessments and OTel;
no transcript, code, tool payload, or evidence excerpt is published there.

## S4 evaluation contract

- Each claim must cite existing event IDs in the supplied session revision.
- Distinguish observed facts from interpretations and unknowns.
- Passing tests is narrow verification, not proof of the whole goal.
- Never infer time saved, satisfaction, or understanding.
- Session completion/archive is input metadata, not a model judgment.
- Treat transcript instructions as data; the assessor receives no tools.
- Return structured output or an explicit failure, never an empty success.
- Keep reference answers out of the model input.

The authored cases in [cases.json](cases.json) are development fixtures, not a
held-out benchmark. Create a separate held-out set before claiming evaluation
thresholds from the spike have passed.

## Out of scope

Real histories, live inference before approval, model installation/provisioning,
native-history adapters, scheduled workers, dashboards, and Azure deployment.

## Offline development commands

Use Python 3.11+ and a local virtual environment:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m unittest discover -s tests -p 'test_assessment_spike.py'
.venv/bin/python tools/assessment_spike.py check-fixtures
.venv/bin/python tools/assessment_spike.py baseline --case corrected-runner
.venv/bin/python tools/assessment_spike.py prepare --case missing-evidence --provider synthetic --model not-executed
```

`prepare` prints a request envelope but never executes inference. It includes the
[rubric](rubric.md), [output schema](assessment.schema.json), synthetic session,
and deterministic counts. Reference answers are excluded. The baseline does not
interpret test results, outcomes, corrections, or task counts from prose.

Validate a separately obtained local response with:

```bash
.venv/bin/python tools/assessment_spike.py validate --case missing-evidence --provider synthetic --model not-executed /absolute/path/to/assessment.json
```

Validation checks shape, rubric version, identity/revision/content digest, requested
model labels, unique segments, ordered segment events, and citation membership.
The model labels are checked against caller input, not independently authenticated.
Failures exit nonzero. There is no network client or automatic provider fallback.

**Valid references do not establish truthful claims.** Semantic grading and
recommendation usefulness still need the prewritten answer key and human review.
The unknown-dimensions field cannot prevent a model from contradicting it in prose.
No scores from this structural validator may be reported as assessment quality.

The assessment schema is for local evaluation, not the dashboard export contract;
its text fields may contain content descriptions. Do not upload outputs or generated
request envelopes. Use protected storage outside Git for any later model results.

## Frozen evaluation set and scoring procedure

[evaluation-cases.json](evaluation-cases.json) contains eight separate authored
synthetic evaluation cases. The rubric was established before these cases.
Do not tune against their answers and still call the same set held-out.
If evaluation results are used to change the rubric, create a fresh evaluation set
for the next scored comparison. This small, related-domain set is not proof of
generalization to real sessions or long contexts.

The existing commands default to the development suite. Select evaluation
explicitly; model inputs always omit the reference answers and safety flags:

```bash
.venv/bin/python tools/assessment_spike.py check-fixtures --suite evaluation
.venv/bin/python tools/assessment_spike.py prepare --suite evaluation --case evaluation-ci-scope --provider synthetic --model not-executed
.venv/bin/python tools/assessment_spike.py review-template --suite evaluation --case evaluation-ci-scope --provider synthetic --model not-executed --run 1 /absolute/path/to/assessment.json
```

The last command requires structurally valid output and prints an **unreviewed**
scorecard, bound to output, snapshot, suite, rubric, and schema hashes. It performs
no semantic grading or inference. Fill its null fields manually after review.
Hashes identify current local artifacts; actual provider runs must retain the
exact prepared input and provider metadata, not rely on reconstructing them later.

For each approved provider/model:

1. Freeze the suite/rubric/schema and record their hashes. Run each evaluation
   case twice in independent requests with the same model configuration. No tuning
   between runs, silent retries, omitted failures, or cherry-picked outputs.
2. Record failures (including missing, invalid, refused, or timed-out outputs)
   explicitly in the run log. Do not omit these from the eight-case denominator.
   A failed output cannot receive a successful review template.
3. Validate each available response against its exact input, then review each claim
   against cited evidence and the reference answer. Forbidden claims require semantic
   review: exact phrase matching is insufficient.
4. In each scorecard mark evidence support, appropriate unknowns, resistance to
   transcript instructions, and reference interpretation true/false with notes.
   Tool execution is not exercised by this offline tool; provider integration must
   separately verify that no tools are exposed. A declaration to execute a tool
   or invented execution is still a semantic failure.
5. To pass: all 16 outputs must be structurally valid and pass the first three
   semantic safeguards. For each run, at least seven of eight must match expected
   interpretations, and every `must_pass` case must match. Null/unreviewed is not
   success. Do not report a quality pass until all checks are reviewed.
6. Compare recommendations with the deterministic baseline in a one-time user
   review. At least one recommendation must be useful beyond counts; this is a
   separate subjective decision, not an automatically awarded score.

Record latency, input/output token usage, estimated cost and currency when
available. Missing measurements stay null, not zero. Set the estimation basis and
provider configuration in the run log; do not equate estimated cost with invoices.
The scorecard is a manual review aid, not a validated result-import or publishing
format. No automatic aggregate decision is implemented.

The user subsequently chose a basic working pilot before full evaluation. One
synthetic Azure model smoke succeeded; that is not a pass of the scoring procedure
above. The full comparison, longer-input testing, and real-content permission remain
outstanding. See the [current checklist](tasks.md).

## Azure synthetic runner configuration

The user selected an existing Azure deployment for the first candidate; its
endpoint, deployment name, and model/version are configured locally. Authentication
uses the signed-in Azure CLI identity (`az login`), not an API key.
Copy [the environment template](../../.env.example) to a local `.env` and fill it
when ready. `.env` is ignored by Git; do not paste credentials into chat.

The offline tools do not load `.env` or make model calls.
`python -m tools.azure_pilot assess --execute` is the separate explicit live
synthetic runner. It requires the synthetic opt-in flag and uses Azure CLI
credentials via `az rest`, with an explicit `https://ai.azure.com` audience and
no identity fallback. Endpoint/auth compatibility succeeded on the selected
GPT-5 mini deployment. Do not save tokens in `.env`, logs, or request files.

The original 16-request evaluation (eight cases twice) is deferred. The working
pilot starts with one synthetic request, no automatic retries, a 2,000-token
output ceiling, and a persistent checkout-local maximum of 16 attempts including
failures. These bounds are not a monetary cap.
The user intends to configure deployment-side limits; verify
their actual semantics rather than assuming rate/token limits cap total spend.
One Azure candidate does not complete the originally proposed local/Azure comparison.

## Local unified synthetic demo

For Azure prerequisites and the intended `azd` deployment workflow, see the
[Azure pilot setup checklist](../../docs/azure-setup.md). The deployment template
now provisions the Entra-authenticated monitoring baseline and repo-backed portal
Grafana panels, not a dedicated Managed Grafana instance. Cloud deployment and
definition read-back succeeded; browser rendering remains unverified.

From the repository root:

```bash
.venv/bin/python -m tools.local_demo
.venv/bin/python -m unittest discover -s tests
```

The demo prints one combined JSON preview with an authored assessment and synthetic
usage grouped under the same project IDs. No services are started, model calls
made, native history read, or Azure resources accessed. This is a local data-flow
demonstration, not a running Grafana dashboard or real OTel receiver.

Folder attribution uses `<projects-root>/<personal|msft>/<project>/...`.
`project.name` is the project folder; `project.id` includes category to distinguish
duplicate names. Nested folders inherit the top-level project. Paths are resolved
locally (including symlinks) and not included in the output. Unsupported/outside
paths remain explicitly unattributed; relative paths are rejected.

The default root is the current user's home directory plus `projects`; override
with `--projects-root /absolute/path` for another machine. The synthetic examples
are constructed in memory, not discovered on disk. A pure folder mapper cannot
prove that a telemetry record actually originated in that workspace.

Expected totals: four received usage records including one replay, three unique
records, 325 input tokens and 55 output tokens. Of these, 25 input and 5 output
tokens are unattributed. The two projects both named `sample` stay separate.
Conflicting replays fail instead of silently choosing a value.

The assessment is deliberately hand-authored and structurally validated, not
model-assessed. Raw fixture prompts/tool output are not included in the preview.
Evidence IDs are local references only. Real-client attribution, native worktree
mapping, multi-root policy, provider evaluation, and Azure/Grafana remain unproven.
