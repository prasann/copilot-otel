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

Provider access, downloads/provisioning, spend cap, longer-input resource testing,
and real-content permission are still outstanding. No provider has been evaluated.

## Azure configuration placeholder

The user selected an existing Azure deployment for the first candidate; its
endpoint, deployment name, and model/version will be supplied later. Authentication
uses the signed-in Azure CLI identity (`az login`), not an API key.
Copy [the environment template](../../.env.example) to a local `.env` and fill it
when ready. `.env` is ignored by Git; do not paste credentials into chat.

The current offline tools do not load `.env` or make model calls. The template is
for the next runner, not an already implemented integration. Leave inference
disabled until endpoint/API compatibility, auth, and synthetic-only scope are
verified. The future runner must explicitly use Azure CLI credentials, verify
the target resource's token audience and data-plane permissions, and surface
expired-login/access failures without falling back to another identity. Do not
save access tokens in `.env`, logs, or generated request files.

The proposed first run is 16 requests (eight evaluation cases twice), with no
automatic retries and a per-request output-token ceiling. These bounds are not
a monetary cap. The user intends to configure deployment-side limits; verify
their actual semantics rather than assuming rate/token limits cap total spend.
One Azure candidate does not complete the originally proposed local/Azure comparison.
