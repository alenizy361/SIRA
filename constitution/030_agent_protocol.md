# 030 — Agent Protocol

Every one of the 23 agents in the roster (`docs/SPEC_SOURCE.md` section 9,
`constitution/agents/`) is defined by the same contract, twice: once as a
machine-readable spec (`packages/agent-specs/*.yaml` or `.json`, validated
against `packages/agent-specs/schema.json`) and once as the human-readable
document in `constitution/agents/<agent_id>.md` this file's siblings are
part of. The two must never drift — a change to one is incomplete without
the matching change to the other. Specs are validated at API startup by
`apps/api/app/agents/loader.py` (per `schema.json`'s own description); an
agent whose spec fails validation does not run, it is disabled.

## The required fields

- **`id`** — stable snake_case identifier (`^[a-z][a-z0-9_]*$`), e.g. `ceo`,
  `backend_engineer`. This is the filename stem in `constitution/agents/`
  and the join key everywhere else in the system.
- **`display_name`** — `{en, ar}`. Both are required; there is no
  English-only or Arabic-only agent, per
  `constitution/000_mission.md`'s bilingual requirement.
- **`mission`** — the one-paragraph reason this agent exists.
- **`scope`** — what part of the company this agent owns, and by
  implication what it does not own.
- **`inputs`** — what triggers or feeds this agent's work (a goal, a task
  assignment, an event, a schedule).
- **`required_context`** — what must be present before the agent can start;
  if missing, the task transitions to `BLOCKED`
  (`constitution/020_architecture.md`).
- **`allowed_tools`** / **`denied_tools`** — the exact tool names this agent
  may or may never invoke, enforced by `packages/tool-registry/`, not by
  instructing the agent politely not to use them
  (`constitution/050_security.md`).
- **`allowed_actions`** / **`forbidden_actions`** — business-level actions
  (e.g. `finance.create_purchase_request` vs. `finance.transfer_money`),
  evaluated by the permission engine against risk level
  (`constitution/040_permissions.md`).
- **`risk_ceiling`** — the highest risk level (`R0`–`R4`) this agent may
  execute without a human approval record attached. Note the schema
  deliberately excludes `R5` from this field's enum
  (`packages/agent-specs/schema.json`): `R5` is a universal prohibition
  enforced independently of any actor's ceiling
  (`packages/contracts/risk_levels.py`), never something an agent is
  "allowed up to."
- **`budget_ceiling`** — `{currency: "SAR", daily_max, monthly_max}`. Zero
  for agents that don't spend money; see
  `constitution/080_financial_controls.md` for the Marketing Agent's
  concrete numbers.
- **`concurrency_ceiling`** — how many runs of this agent may be in flight
  at once, configured per deployment.
- **`required_outputs`** — the artifacts this agent must produce for a task
  to be considered attempted (e.g. Product Manager's eight-part
  requirement doc, `constitution/agents/product_manager.md`).
- **`required_evidence`** — what must be attached before `COMPLETED` is
  reachable (`constitution/070_quality.md`).
- **`quality_checks`** — automated gates run before review (lint, type
  check, tests, scans).
- **`escalation_conditions`** — situations that route work to another
  agent or a human rather than being handled in place.
- **`stop_conditions`** — timeout, max retries, cancellation triggers
  (`constitution/010_operating_principles.md` rule 8).
- **`kpis`** — the measurable outcomes this agent is accountable for.
- **`memory_policy`** — `{read, write}`: which of the five memory layers
  (`constitution/060_memory.md`) this agent may read from and write to.
- **`communication_protocol`** — see below.
- **`review_requirements`** — who must review this agent's work, and under
  what condition (mirrors `constitution/070_quality.md`'s independence
  rule).
- **`enabled`** / **`disabled_reason`** — an agent may be shipped disabled
  until its integration is configured, but its contract, UI presence, and
  permissions must already be real and complete
  (`docs/SPEC_SOURCE.md` section 9 preamble;
  `constitution/010_operating_principles.md` rule 2).

## No free-form chat loops

Agents do not converse. There is no agent-to-agent chat channel where two
agents exchange open-ended messages until something happens. Every
communication is a typed message attached to a concrete object: a goal, a
plan, a task, a review, an incident, or a decision
(`docs/SPEC_SOURCE.md` section 8). Concretely:

- A question from one agent to another is a `review.requested` or
  `approval.requested` message attached to the task in question, not a
  direct message.
- A disagreement is a `request-changes` or `reject` decision on a review,
  with a concise reason, not a back-and-forth thread.
- An escalation is a new task or incident, owned by the escalation target,
  linking back to the originating task via `correlation_id`.

This is what makes the system auditable: every agent-to-agent interaction is
a row someone can query later, tied to the goal it served, not a transcript
buried in a chat log.
