# 090 — Incident Response

## The Incident Commander Agent

The Incident Commander Agent (`constitution/agents/incident_commander.md`)
coordinates response to outages, security events, corrupted deployments,
runaway spend, and other urgent incidents (`docs/SPEC_SOURCE.md` section
9.23). It is the one agent every other agent's escalation conditions can
route to directly:

- **May:** pause autonomous execution, stop workers, initiate a documented
  rollback, isolate affected integrations, summon relevant agents.
- **May not:** destroy evidence, suppress audit logs, declare an incident
  resolved without verification.

Every other agent's `escalation_conditions`
(`constitution/030_agent_protocol.md`) that mentions "open an incident" or
"escalate to Incident Commander" terminates here.

## Emergency stop behavior

When an incident opens (`EventType.INCIDENT_OPENED`,
`packages/contracts/events.py`), the following happens, in this order, and
none of it requires waiting on a human to be online:

1. **Stop new task assignment.** The orchestrator refuses new
   `TASK_ASSIGNED` transitions for the affected scope (an agent, an
   integration, or the whole organization, depending on incident severity)
   — no new autonomous work starts into a system already known to be
   unwell.
2. **Cancel safe-to-cancel runs.** Only runs whose `stop_conditions`
   (`constitution/030_agent_protocol.md`) mark them cancellable are
   cancelled — a run mid-write to an external system that would leave
   inconsistent state is left to finish or fail on its own terms, not
   torn out.
3. **Revoke worker leases.** Redis-held leases for `services/claude-worker`,
   `services/browser-worker`, and `services/reviewer-worker`
   (`constitution/020_architecture.md`) are released so no orphaned process
   keeps acting after the incident has opened.
4. **Pause external mutation tools.** `packages/tool-registry/` flips any
   tool with an external side effect to denied for the duration of the
   incident; read-only tools stay available so diagnosis and investigation
   can continue without also continuing to change the world.
5. **Preserve evidence.** Episodic memory, the event log, and artifact
   memory (`constitution/060_memory.md`) become append-only for the
   incident's scope. Every event produced during the incident carries the
   incident's `correlation_id`, so the full timeline is reconstructible
   afterward. This is what makes "may not destroy evidence" and "may not
   suppress audit logs" enforceable rather than aspirational.

## Incident state and the orchestrator

`INCIDENT_OPENED` is a first-class phase in the state machine
(`constitution/020_architecture.md`), not a side annotation on top of it.
Any goal or task subtree touched by the incident is moved into this phase
and frozen — no further autonomous transitions (`DEPLOYING`, `MONITORING`,
`COMPLETED`, or even routine `RETRY_WAIT`) happen until the Incident
Commander files `EventType.INCIDENT_RESOLVED` with verification evidence
attached. Only then do affected tasks resume: back to their prior phase, into
`RETRY_WAIT` if the underlying issue needs a fresh attempt, or to
`FAILED`/`CANCELLED` if the Incident Commander's documented disposition says
so.

Declaring `INCIDENT_RESOLVED` on the say-so of the agent whose action
caused the incident is not permitted — verification must come from a
distinct source (Security, Independent Reviewer, or a human), mirroring the
independence requirement in `constitution/070_quality.md`. An incident
touching production or customer data is never closed without human
sign-off, even after technical verification passes — the Incident Commander
proposes resolution, it does not unilaterally close the loop on anything
that reached R3/R4 territory on the way in
(`constitution/040_permissions.md`).
