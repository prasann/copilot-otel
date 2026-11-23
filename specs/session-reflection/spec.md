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
