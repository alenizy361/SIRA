# 060 — Memory

Rabit AI Company OS has five distinct memory layers. They are not
interchangeable, and confusing one for another (treating a working-memory
scratch note as durable semantic fact, say) is itself a defect.

## The five layers

1. **Working memory** — per-run scratch state, tied to a single task/run,
   ephemeral. It exists to let an agent hold intermediate state during
   execution and is cleared once the run reaches a terminal phase
   (`constitution/020_architecture.md`). Nothing depends on working memory
   surviving a restart.
2. **Episodic memory** — what happened: the linked timeline of goals,
   plans, tasks, reviews, incidents, and decisions, in the order they
   occurred. This is the audit trail — it is what the Memory and Knowledge
   Librarian Agent (`constitution/agents/memory_librarian.md`) curates and
   what `constitution/010_operating_principles.md` rule 10's
   attributability requirement is stored in.
3. **Semantic memory** — durable facts about the business: what
   cv.rabit.sa's supported export formats are, what a customer segment's
   typical behavior looks like, what an architecture decision settled. This
   is what agents read to avoid re-deriving the same fact from scratch
   every time.
4. **Policy memory** — versioned rules: permission policy, budget ceilings,
   agent-spec versions. Every permission-engine decision cites the exact
   `policy_version` it was evaluated against (`constitution/040_permissions.md`);
   policy memory is what that version number resolves to.
5. **Artifact memory** — durable outputs: code diffs, PRs, documents,
   design files, evidence attachments (`constitution/070_quality.md`),
   screenshots, reports. If a completion claim points at evidence, that
   evidence lives here.

## What every memory item must carry

Regardless of layer, a memory write is incomplete without:

- **source** — where this came from (which agent, which run, which
  external document)
- **confidence** — how sure the writer is (a Research Agent's estimate
  carries an explicit uncertainty, `constitution/agents/research.md`)
- **freshness** — when this was last verified, and when it should be
  considered stale
- **owner** — which agent/team is responsible for keeping this current
- **sensitivity** — public, internal, confidential, or secret
  classification, driving access and redaction rules
- **retention class** — how long this is kept, and what happens when it
  expires (archived, purged, escalated for a retention decision)

A memory write missing any of these fields is rejected the same way a task
missing `required_evidence` cannot reach `COMPLETED`
(`constitution/070_quality.md`).

## Rules

- **No hidden chain-of-thought.** Memory stores decision summaries — what
  was decided, on what evidence, with what reasoning stated at a level a
  human can audit — never a raw internal reasoning trace kept as if it were
  the authoritative record (`constitution/010_operating_principles.md` rule
  16). The Memory Librarian explicitly refuses writes that look like a raw
  reasoning dump instead of a decision summary.
- **Contradiction detection.** When two semantic-memory facts conflict
  (e.g. two different "supported export formats" lists), this is
  surfaced, not silently overwritten — flagged for the responsible agent or
  a human to resolve, with both versions preserved in episodic memory.
- **Redaction.** Personal data and secrets are scrubbed according to
  sensitivity classification before being written to any layer beyond
  working memory. A screenshot or support ticket containing an unredacted
  phone number or password is redacted before it becomes semantic or
  artifact memory.
- **User-correctable.** A human can correct or annotate a memory item. A
  correction is itself a new episodic record — linked to what it corrects —
  never a silent overwrite that erases what the system previously believed
  and why.

See `constitution/agents/memory_librarian.md` for the agent responsible for
enforcing these rules day to day, and `constitution/090_incident_response.md`
for the rule that evidence must be preserved, not merely "remembered
somewhere," during an incident.
