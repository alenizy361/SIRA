"""The structured contract passed to every Claude Code invocation.

Constitution section 7.5: "Pass a structured task contract containing
mission, context, constraints, allowed tools, prohibited actions,
acceptance criteria, output schema, timeout, and stop conditions."
"""
from dataclasses import dataclass, field


@dataclass
class TaskContract:
    task_id: str
    mission: str
    context: str
    constraints: list[str]
    allowed_tools: list[str]
    prohibited_actions: list[str]
    acceptance_criteria: list[str]
    output_schema: dict | None
    timeout_seconds: int = 1800
    max_turns: int | None = None
    stop_conditions: list[str] = field(default_factory=list)
    workspace_repo: str | None = None
    branch_name: str | None = None
    risk_level: str = "R1"
    model: str | None = None
    effort: str | None = None
    max_budget_usd: float | None = None
    # True for a pure-conversation turn (e.g. casual chat) that should never
    # touch a tool, regardless of risk_level - distinct from allowed_tools
    # being empty, which _effective_allowed_tools treats as "use the safe
    # default for this risk tier", not "use none at all".
    no_tools: bool = False
