# 070 — Quality

## Evidence required for completion

"Done" is a claim backed by artifacts, never a sentence an agent asserts on
its own authority (`constitution/010_operating_principles.md` rule 9). A
task's `required_evidence` (`constitution/030_agent_protocol.md`) is
checked before the state machine allows a transition into `COMPLETED`
(`constitution/020_architecture.md`). Depending on the kind of work, that
evidence includes:

- **Test run output/logs** — actual pass/fail results, not a paraphrase of
  what tests were "supposed to" show.
- **Screenshots or video** — for UI work, especially anything touching RTL
  layout, mobile viewport, or accessibility (Playwright captures,
  `constitution/agents/frontend_engineer.md`).
- **Lint and type-check results.**
- **A code diff and a PR link** — the actual change, in the isolated branch
  it was made on (`constitution/010_operating_principles.md` rule 7).
- **A migration dry-run against staging** — for any schema change
  (`constitution/agents/database_engineer.md`).
- **A security scan report** — for changes touching dependencies, auth, or
  external-facing surface (`constitution/agents/security.md`).
- **Before/after metrics** — for anything claiming a measurable improvement
  (`constitution/agents/analytics.md`).
- **An Independent Reviewer decision record** — approve / request-changes /
  reject, with a concise reason (`constitution/agents/independent_reviewer.md`).

An agent that reports completion without the evidence its own spec requires
has not completed the task — it has produced an unverified claim, which QA
is explicitly empowered to reject.

## Testing layers

Mirroring `tests/` in the monorepo (`constitution/020_architecture.md`):

- **Unit** (`tests/unit/`) — the smallest testable units, run on every
  change, fast enough to run constantly.
- **Integration** (`tests/integration/`) — real interactions between
  services (orchestrator ↔ Postgres/Redis, API ↔ permission engine).
- **End-to-end** (`tests/e2e/`) — full user-facing flows through
  `apps/web`, including Playwright coverage of RTL/Arabic locale behavior
  (`constitution/agents/frontend_engineer.md`,
  `constitution/agents/qa.md`).
- **Security** (`tests/security/`) — the Security Agent's non-destructive
  scans, dependency audits, and permission-boundary checks
  (`constitution/agents/security.md`, `constitution/050_security.md`).
- **Performance/load** (`tests/load/`) — latency and throughput under
  realistic load, owned jointly by Database Engineer (query performance)
  and DevOps/SRE (infrastructure capacity).

A change is not "tested" because one of these layers passed — it is tested
to the depth its risk level and required_evidence demand. An R2 schema
migration needs a staging dry-run; an R0 documentation fix does not.

## QA must be independent of the implementing agent

The agent that built a change is never the agent that supplies the evidence
sign-off for it. This is structural, not a suggestion: the QA Agent
(`constitution/agents/qa.md`) "may not modify the implementation being
reviewed except through a separate explicitly assigned fix task," and the
Independent Reviewer Agent (`constitution/agents/independent_reviewer.md`)
"may not approve its own work." An agent-spec's `review_requirements`
(`constitution/030_agent_protocol.md`) must always name a reviewer distinct
from the executor — the permission engine's evaluation of `reviewers`
(`constitution/040_permissions.md`) checks this, not just that "a review
happened."
