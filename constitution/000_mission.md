# 000 — Mission

## What Rabit AI Company OS is

Rabit AI Company OS is a living command center for an autonomous company: a
system of record, orchestration engine, and control surface for a roster of
23 defined AI agents (`constitution/agents/`) that plan, build, ship, market,
support, and account for a real product business, under a permission model
that scales autonomy to risk (`constitution/040_permissions.md`).

It is not a chatbot, a demo, or a workflow diagram. Every goal a human enters
becomes a real, traceable chain of plans, tasks, tool calls, evidence, and
decisions (`constitution/020_architecture.md`), and every agent in the roster
either does its real job or is visibly disabled — never simulated
(`constitution/010_operating_principles.md`, rule 2).

## The command center

The primary surface is a premium, mobile-first command center: a phone-sized
dashboard that is the default way a human owner watches and steers the
company, with a full desktop view for deeper work. It shows the company's
current state as one legible signal — not a wall of logs — backed by a
real-time event stream (`packages/contracts/events.py`), not a static status
page. The `CoreState` enum (`offline, connecting, idle, listening,
understanding, planning, delegating, coding, testing, reviewing,
waiting_for_approval, deploying, monitoring, learning, speaking, warning,
incident, paused`) is the single source of truth for "what is the company
doing right now," rendered consistently across every surface.

## Arabic-first, fully bilingual

Arabic is the first-class default: layout, typography, and voice interaction
are designed RTL-first, not retrofitted onto an English-first build. Full
English support with correct LTR rendering is a hard requirement, on equal
footing, not a secondary locale. A user must be able to switch locales
mid-session, instantly, without losing context, a re-render glitch, or a
mixed-direction layout bug. Any feature shipped in one language and not the
other is incomplete, per `constitution/010_operating_principles.md` rule 1
(no placeholder business logic — a missing translation is a placeholder).

## The autonomy gradient

The command center's core promise is legible trust, delivered through one
gradient, applied consistently everywhere in the product:

- **Autonomous** for low-risk actions (R0–R1) — the company keeps moving
  without asking.
- **Controlled** for medium-risk actions (R2) — the company acts, but only
  after validation, rollback plans, budget checks, and policy conditions
  pass.
- **Human-approved** for high-risk actions (R3 and above) — the company
  proposes, a human decides, an independent reviewer signs off where R4
  requires it.

See `constitution/040_permissions.md` for the full R0–R5 model. This gradient
is not a UI convention layered on top of otherwise-uniform automation — it is
enforced by the permission engine (`packages/permission-engine/`) on every
tool call, for every agent, with no exceptions and no bypass
(`constitution/010_operating_principles.md`, rule 11).

## Event-driven, not polling

Rabit AI Company OS reacts to things happening — a goal created, a webhook
received, a schedule firing, a budget threshold crossed
(`EventType.BUDGET_THRESHOLD_REACHED`) — it does not sit in a loop polling
external systems or its own database for changes. The orchestrator
(`services/orchestrator`) advances the state machine in response to events
published on the shared bus, and the dashboard renders those same events over
a websocket, live (`constitution/020_architecture.md`). This is an
architectural constraint, not a style preference: continuous polling wastes
budget, hides failures behind stale reads, and cannot cheaply express
"nothing has changed, everything is fine" the way an event-driven system can.

## Money and time, by default

Unless an organization's policy says otherwise, the operating currency is SAR
and the operating timezone is Asia/Riyadh
(`constitution/010_operating_principles.md` rule 17;
`constitution/080_financial_controls.md`). Every budget ceiling, every KPI
window, every timestamp shown to a human defaults to these unless the
organization is explicitly configured for another market.

## First tenant, generic product

The first real workload this system runs is Rabit's own product portfolio —
starting with `cv.rabit.sa`. The 23-agent roster, the state machine, and the
permission model are written generically: "the product," "the customer," "the
codebase." Nothing in the constitution, the agent contracts
(`constitution/agents/`), or the orchestrator hard-codes cv.rabit.sa
specifics. cv.rabit.sa is the proving ground that the system operates a real
product end to end — build, ship, market, support, account for it — it is
not the shape of the system. A second, unrelated product, or a second
organization entirely, must be able to onboard with new configuration and new
agent-spec instances, never a code change.
