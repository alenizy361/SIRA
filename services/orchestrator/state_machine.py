"""Deterministic, observable state machine (constitution section 6).

Pure functions only - no DB/IO here. Callers (apps/api routers, the
claude-worker poll loop) persist the returned state and append a
run_events / audit_logs row themselves, so every transition stays
attributable per constitution rule #12.
"""
from enum import Enum


class InvalidTransition(Exception):
    pass


class GoalState(str, Enum):
    GOAL_CAPTURED = "goal_captured"
    GOAL_CLARIFIED = "goal_clarified"
    DISCOVERY = "discovery"
    PLAN_DRAFTED = "plan_drafted"
    PLAN_REVIEWED = "plan_reviewed"
    RISK_CLASSIFIED = "risk_classified"
    APPROVAL_PENDING = "approval_pending"
    READY = "ready"
    ASSIGNED = "assigned"
    RUNNING = "running"
    VALIDATING = "validating"
    REVIEWING = "reviewing"
    PREVIEW_READY = "preview_ready"
    MEASURING = "measuring"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class TaskState(str, Enum):
    READY = "ready"
    ASSIGNED = "assigned"
    RUNNING = "running"
    VALIDATING = "validating"
    REVIEWING = "reviewing"
    COMPLETED = "completed"
    RETRY_WAIT = "retry_wait"
    BLOCKED = "blocked"
    HUMAN_INPUT_REQUIRED = "human_input_required"
    FAILED = "failed"
    INCIDENT_OPENED = "incident_opened"
    CANCELLED = "cancelled"


# Linear happy path plus the documented approval branch (skippable) and the
# three failure branches (retry / blocked / failed) from constitution section 6.
_GOAL_TRANSITIONS: dict[GoalState, set[GoalState]] = {
    GoalState.GOAL_CAPTURED: {GoalState.GOAL_CLARIFIED, GoalState.CANCELLED},
    GoalState.GOAL_CLARIFIED: {GoalState.DISCOVERY, GoalState.CANCELLED},
    GoalState.DISCOVERY: {GoalState.PLAN_DRAFTED, GoalState.CANCELLED},
    GoalState.PLAN_DRAFTED: {GoalState.PLAN_REVIEWED, GoalState.CANCELLED},
    GoalState.PLAN_REVIEWED: {GoalState.RISK_CLASSIFIED, GoalState.CANCELLED},
    GoalState.RISK_CLASSIFIED: {GoalState.APPROVAL_PENDING, GoalState.READY, GoalState.CANCELLED},
    GoalState.APPROVAL_PENDING: {GoalState.READY, GoalState.CANCELLED},
    GoalState.READY: {GoalState.ASSIGNED, GoalState.CANCELLED},
    GoalState.ASSIGNED: {GoalState.RUNNING, GoalState.CANCELLED},
    GoalState.RUNNING: {GoalState.VALIDATING, GoalState.CANCELLED},
    GoalState.VALIDATING: {GoalState.REVIEWING, GoalState.CANCELLED},
    GoalState.REVIEWING: {GoalState.PREVIEW_READY, GoalState.MEASURING, GoalState.CANCELLED},
    GoalState.PREVIEW_READY: {GoalState.MEASURING, GoalState.CANCELLED},
    GoalState.MEASURING: {GoalState.COMPLETED, GoalState.CANCELLED},
    GoalState.COMPLETED: set(),
    GoalState.CANCELLED: set(),
}

_TASK_TRANSITIONS: dict[TaskState, set[TaskState]] = {
    TaskState.READY: {TaskState.ASSIGNED, TaskState.CANCELLED},
    # ASSIGNED -> RETRY_WAIT: a task can fail (or its worker die) after being
    # leased/assigned but before RUNNING commits - it must be recoverable, not
    # a permanent trap.
    TaskState.ASSIGNED: {TaskState.RUNNING, TaskState.RETRY_WAIT, TaskState.CANCELLED},
    TaskState.RUNNING: {
        TaskState.VALIDATING,
        TaskState.RETRY_WAIT,
        TaskState.BLOCKED,
        TaskState.FAILED,
        TaskState.CANCELLED,
    },
    TaskState.VALIDATING: {TaskState.REVIEWING, TaskState.RETRY_WAIT, TaskState.FAILED},
    TaskState.REVIEWING: {TaskState.COMPLETED, TaskState.RETRY_WAIT, TaskState.FAILED},
    TaskState.RETRY_WAIT: {TaskState.READY, TaskState.FAILED, TaskState.CANCELLED},
    TaskState.BLOCKED: {TaskState.HUMAN_INPUT_REQUIRED, TaskState.CANCELLED},
    TaskState.HUMAN_INPUT_REQUIRED: {TaskState.READY, TaskState.CANCELLED, TaskState.FAILED},
    TaskState.FAILED: {TaskState.INCIDENT_OPENED},
    TaskState.INCIDENT_OPENED: set(),
    TaskState.COMPLETED: set(),
    TaskState.CANCELLED: set(),
}


def transition_goal(current: GoalState, target: GoalState) -> GoalState:
    allowed = _GOAL_TRANSITIONS.get(current, set())
    if target not in allowed:
        raise InvalidTransition(f"Goal cannot move from {current.value} to {target.value}")
    return target


def transition_task(current: TaskState, target: TaskState) -> TaskState:
    allowed = _TASK_TRANSITIONS.get(current, set())
    if target not in allowed:
        raise InvalidTransition(f"Task cannot move from {current.value} to {target.value}")
    return target
