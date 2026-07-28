# 080 — Financial Controls

## Defaults

SAR is the default operating currency and Asia/Riyadh the default timezone
for every organization on this system, unless explicitly configured
otherwise (`constitution/000_mission.md`,
`constitution/010_operating_principles.md` rule 17). Every `budget_ceiling`
in an agent spec is denominated in SAR
(`packages/agent-specs/schema.json`: `budget_ceiling.currency` is a fixed
`"SAR"` const, not a free-text field an agent-spec author can silently
change to another currency).

## Marketing and Growth Agent's default limits

The Marketing and Growth Agent (`constitution/agents/marketing_growth.md`)
is the one agent in the roster with an explicit, spec-mandated autonomous
spend envelope. Its defaults are:

- Maximum autonomous campaign change: **10% of daily budget**
- Maximum autonomous experimental spend: **100 SAR/day**
- Maximum autonomous monthly marketing spend: **2,000 SAR/month**
- **Any new vendor, any recurring subscription, or any spend above these
  limits requires human approval** — this is an R3 action
  (`constitution/040_permissions.md`), regardless of how confident the
  agent is in the campaign's performance.

These are defaults, not hardcoded constants — an organization's policy can
tighten them further, but Marketing's own agent spec cannot loosen them
without a policy change that is itself R3+
(`constitution/010_operating_principles.md` rule 12).

## Finance and Procurement Agent cannot move money

The Finance and Procurement Agent (`constitution/agents/finance_procurement.md`)
tracks budgets, forecasts costs, evaluates purchase requests, and flags
anomalies — it never executes a financial transaction. `finance.transfer_money`,
`finance.take_loan`, and `finance.make_investment` are `HARD_PROHIBITED_ACTIONS`
in `packages/contracts/risk_levels.py` — R5, rejected in code regardless of
who is asking or what policy is configured
(`constitution/040_permissions.md`).

Finance & Procurement also **cannot approve its own request**. It may
create a purchase request; the approval must come from a different actor —
a human, or another agent acting within its own risk ceiling and without a
conflict of interest — the same independence rule that governs QA and the
Independent Reviewer (`constitution/070_quality.md`).

## Budget reservation before external spend

Any agent about to incur an external cost (an ad spend, a paid API call, a
vendor charge) must reserve against its `budget_ceiling.daily_max` /
`monthly_max` **before** the tool call that spends money executes, not
after the fact. The permission engine's evaluation of `budget` and
`cumulative usage` (`constitution/040_permissions.md`) happens at
reservation time — a spend that would exceed the ceiling is refused before
it happens, not caught and rolled back afterward.

## No negative remaining budget

Reservation and spend are atomic with respect to the ceiling: an action
that would drive an agent's remaining budget below zero is rejected outright
by the permission engine, full stop — there is no "slightly over budget"
state. `EventType.BUDGET_THRESHOLD_REACHED`
(`packages/contracts/events.py`) fires at configured warning thresholds
(e.g. 80% of daily/monthly cap) well before the ceiling itself would be hit,
giving the CEO Agent and a human operator advance notice rather than a
surprise refusal mid-campaign.
