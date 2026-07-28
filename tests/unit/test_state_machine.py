import pytest

from orchestrator.state_machine import GoalState, InvalidTransition, TaskState, transition_goal, transition_task


def test_goal_happy_path():
    state = GoalState.GOAL_CAPTURED
    for target in [
        GoalState.GOAL_CLARIFIED,
        GoalState.DISCOVERY,
        GoalState.PLAN_DRAFTED,
        GoalState.PLAN_REVIEWED,
        GoalState.RISK_CLASSIFIED,
        GoalState.READY,
        GoalState.ASSIGNED,
        GoalState.RUNNING,
        GoalState.VALIDATING,
        GoalState.REVIEWING,
        GoalState.MEASURING,
        GoalState.COMPLETED,
    ]:
        state = transition_goal(state, target)
    assert state == GoalState.COMPLETED


def test_goal_approval_branch():
    state = transition_goal(GoalState.RISK_CLASSIFIED, GoalState.APPROVAL_PENDING)
    state = transition_goal(state, GoalState.READY)
    assert state == GoalState.READY


def test_goal_cannot_skip_states():
    with pytest.raises(InvalidTransition):
        transition_goal(GoalState.GOAL_CAPTURED, GoalState.RUNNING)


def test_completed_goal_is_terminal():
    with pytest.raises(InvalidTransition):
        transition_goal(GoalState.COMPLETED, GoalState.RUNNING)


def test_task_retry_wait_path():
    state = transition_task(TaskState.RUNNING, TaskState.RETRY_WAIT)
    state = transition_task(state, TaskState.READY)
    assert state == TaskState.READY


def test_task_blocked_path():
    state = transition_task(TaskState.RUNNING, TaskState.BLOCKED)
    state = transition_task(state, TaskState.HUMAN_INPUT_REQUIRED)
    assert state == TaskState.HUMAN_INPUT_REQUIRED


def test_task_failed_opens_incident():
    state = transition_task(TaskState.RUNNING, TaskState.FAILED)
    state = transition_task(state, TaskState.INCIDENT_OPENED)
    assert state == TaskState.INCIDENT_OPENED


def test_task_cannot_go_backwards_from_completed():
    with pytest.raises(InvalidTransition):
        transition_task(TaskState.COMPLETED, TaskState.RUNNING)
