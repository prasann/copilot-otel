# First-slice execution

1. Establish authored synthetic cases and expected conclusions before model use.
2. Define a versioned output schema and a tool-free rubric with event citations.
3. Implement an offline validator for output shape and reference integrity; keep
   semantic scoring explicit rather than pretending valid JSON means correctness.
4. Approve one local candidate, one Azure candidate, endpoint access, and cost cap.
5. Run both against the same held-out cases twice; preserve scored, synthetic-only
   results and compare with a deterministic-facts baseline.
6. Review usefulness before building the capture/batch platform.

No automatic inference-provider fallback. Missing access, unknown model behavior,
and invalid output must remain visible blockers.

Restart and resume/publication tests remain required before production rollout.
Use snapshot identity, pre-publication rechecks, and eventual supersession rather
than promising atomic coordination with VS Code.
