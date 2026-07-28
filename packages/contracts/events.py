"""Realtime event contract (section 15 of the constitution).

Every event pushed over the dashboard WebSocket must be an instance of Event,
serialized via `.to_wire()`. This is the one place new event types are registered -
do not hand-construct event dicts elsewhere.
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional
from uuid import uuid4

from pydantic import BaseModel, Field


class EventType(str, Enum):
    SYSTEM_STATUS_CHANGED = "system.status.changed"
    AGENT_STATUS_CHANGED = "agent.status.changed"
    GOAL_CREATED = "goal.created"
    GOAL_COMPLETED = "goal.completed"
    PLAN_CREATED = "plan.created"
    TASK_CREATED = "task.created"
    TASK_ASSIGNED = "task.assigned"
    TASK_STARTED = "task.started"
    TASK_PROGRESS = "task.progress"
    TASK_BLOCKED = "task.blocked"
    TASK_COMPLETED = "task.completed"
    TASK_CANCELLED = "task.cancelled"
    TASK_MESSAGE = "task.message"
    RUN_OUTPUT_DELTA = "run.output.delta"
    RUN_TOOL_STARTED = "run.tool.started"
    RUN_TOOL_COMPLETED = "run.tool.completed"
    REVIEW_REQUESTED = "review.requested"
    REVIEW_COMPLETED = "review.completed"
    APPROVAL_REQUESTED = "approval.requested"
    APPROVAL_RESOLVED = "approval.resolved"
    INCIDENT_OPENED = "incident.opened"
    INCIDENT_RESOLVED = "incident.resolved"
    INTEGRATION_STATUS_CHANGED = "integration.status.changed"
    BUDGET_THRESHOLD_REACHED = "budget.threshold.reached"
    VOICE_LISTENING_STARTED = "voice.listening.started"
    VOICE_TRANSCRIPT_UPDATED = "voice.transcript.updated"
    CORE_STATE_CHANGED = "core.state.changed"


class CoreState(str, Enum):
    OFFLINE = "offline"
    CONNECTING = "connecting"
    IDLE = "idle"
    LISTENING = "listening"
    UNDERSTANDING = "understanding"
    PLANNING = "planning"
    DELEGATING = "delegating"
    CODING = "coding"
    TESTING = "testing"
    REVIEWING = "reviewing"
    WAITING_FOR_APPROVAL = "waiting_for_approval"
    DEPLOYING = "deploying"
    MONITORING = "monitoring"
    LEARNING = "learning"
    SPEAKING = "speaking"
    WARNING = "warning"
    INCIDENT = "incident"
    PAUSED = "paused"


class EntityRef(BaseModel):
    type: str
    id: str


class Event(BaseModel):
    event_id: str = Field(default_factory=lambda: str(uuid4()))
    sequence: int
    type: EventType
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    organization_id: str
    correlation_id: str
    actor: str
    entities: list[EntityRef] = Field(default_factory=list)
    payload: dict[str, Any] = Field(default_factory=dict)

    def to_wire(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "sequence": self.sequence,
            "type": self.type.value,
            "timestamp": self.timestamp.isoformat(),
            "organization_id": self.organization_id,
            "correlation_id": self.correlation_id,
            "actor": self.actor,
            "entities": [e.model_dump() for e in self.entities],
            "payload": self.payload,
        }


# Fields that must never appear in an event payload - enforced by a runtime assertion
# in the event bus publisher (services/observability/event_bus.py).
FORBIDDEN_PAYLOAD_KEYS = {
    "password", "secret", "api_key", "token", "credential",
    "chain_of_thought", "raw_reasoning", "private_key",
}
