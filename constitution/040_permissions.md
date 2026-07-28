# 040 — Permissions

## The R0–R5 risk model

Every action any agent proposes is classified into exactly one of six risk
levels before it is allowed to execute. This is the single vocabulary used
everywhere in this system — in agent specs' `risk_ceiling` field
(`constitution/030_agent_protocol.md`), in the state machine's
`WAITING_FOR_APPROVAL` phase (`constitution/020_architecture.md`), and in
code (`packages/contracts/risk_levels.py`, the canonical `RiskLevel` enum
shared by the permission engine, orchestrator, and API — never redefined
locally). The levels are never renamed and never reordered:

- **R0 — read-only, automatic.** No side effect. Any agent may do this
  freely within its `allowed_tools`/`allowed_actions`.
- **R1 — low-risk, reversible, automatic within agent permissions.** Runs
  without a human in the loop, as long as it's within the agent's own
  `risk_ceiling` and `allowed_actions`.
- **R2 — controlled, reversible, automatic only when validation, rollback,
  budget, and policy conditions all pass.** This is the "controlled
  autonomy" tier from `constitution/000_mission.md` — the system still acts
  on its own, but only after checking its work.
- **R3 — material business impact, human approval required.** Examples:
  merge to main, deploy to production, customer communications, pricing
  changes, database migrations. No agent's `risk_ceiling` reaches R3; an
  agent may execute an R3 action only once an approval record exists
  (`EventType.APPROVAL_RESOLVED`).
- **R4 — high-risk / irreversible, approval + independent reviewer +
  rollback plan required.** Examples: deleting customer data, payment
  method changes, contracts, money transfers, auth/ownership changes,
  firewall/DNS/root access. Requires everything R3 requires, plus a
  distinct Independent Reviewer sign-off (`constitution/agents/independent_reviewer.md`)
  and a documented rollback plan before execution.
- **R5 — prohibited, always rejected.** Self-granting permissions,
  disabling audit logs, bypassing approval, storing or exposing raw
  passwords, autonomous loans/investments/ownership transfer/hiring/firing/
  legal signatures/destructive security activity/unbounded spend. R5 is not
  a ceiling any agent can be granted (`packages/agent-specs/schema.json`'s
  `risk_ceiling` enum stops at `R4` on purpose) — it is a fixed set of
  actions the permission engine refuses in code regardless of actor, policy
  version, or approval:
  `packages/contracts/risk_levels.py`'s `HARD_PROHIBITED_ACTIONS` —
  `self.grant_permission`, `policy.disable_audit_log`,
  `policy.bypass_approval`, `credential.expose_raw_secret`,
  `finance.transfer_money`, `finance.take_loan`, `finance.make_investment`,
  `org.transfer_ownership`, `org.change_authentication_root`,
  `legal.sign_contract`, `hr.hire`, `hr.fire`,
  `security.destructive_exploitation`. See
  `constitution/010_operating_principles.md` rule 11.

`risk_levels.py` also fixes: `DEFAULT_AUTO_APPROVE_CEILING = R2` (the system
default boundary between "the company just does it" and "the company asks
first"), `APPROVAL_REQUIRED_FROM = R3`, `REVIEWER_REQUIRED_FROM = R4`,
`ALWAYS_REJECTED = R5`, plus `RISK_ORDER`/`risk_index`/`at_least()` helpers
used anywhere code needs to compare two risk levels rather than re-deriving
an ordering.

## What the permission engine evaluates

For every proposed action, `packages/permission-engine/` evaluates a fixed
set of inputs — no decision is made on a subset of these:

- **actor** — which agent (or human) is proposing the action
- **organization** — which tenant/organization this happens under
  (`Event.organization_id`)
- **action** — the specific action identifier (e.g.
  `finance.create_purchase_request`)
- **resource** — what the action targets
- **tool** — the underlying tool call, cross-checked against
  `packages/tool-registry/` and the actor's `allowed_tools`/`denied_tools`
- **environment** — dev, staging, or production
- **risk** — the R0–R5 classification of this action
- **budget** — remaining budget against the actor's `budget_ceiling`
  (`constitution/080_financial_controls.md`)
- **reversibility** — can this be undone, and how
- **evidence** — what evidence already exists to support the action
  (`constitution/070_quality.md`)
- **reviewers** — who has reviewed this, and whether that satisfies
  `review_requirements`
- **incident state** — whether an open incident freezes this action
  (`constitution/090_incident_response.md`)
- **time window** — whether this falls inside an approved change window
- **cumulative usage** — how much of the actor's concurrency/budget/rate
  ceilings are already consumed

## Every decision is a record

The permission engine's output is never just "yes" or "no" — every decision
is stored with: the **policy version** it was evaluated against, the full
**inputs** listed above, the **result**, and a human-readable
**explanation** of why. This is what lets a human (or the Independent
Reviewer, or an incident retrospective) reconstruct exactly why an action
was allowed or refused, months later, without re-running anything. A
decision record is itself evidence under
`constitution/070_quality.md` and a first-class object under the
"communication only through typed messages attached to... decisions" rule in
`constitution/030_agent_protocol.md`.
