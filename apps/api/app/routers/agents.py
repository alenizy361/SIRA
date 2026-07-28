from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session as DbSession

from app.auth.dependencies import get_current_user
from app.db import get_db
from app.models.agents import AgentDefinition, AgentInstance
from app.models.identity import User

router = APIRouter(prefix="/agents", tags=["agents"])


@router.get("")
def list_agents(user: User = Depends(get_current_user), db: DbSession = Depends(get_db)):
    definitions = {d.agent_key: d for d in db.query(AgentDefinition).all()}
    instances = db.query(AgentInstance).filter(AgentInstance.organization_id == user.organization_id).all()
    instances_by_def = {i.agent_definition_id: i for i in instances}

    out = []
    for key, definition in sorted(definitions.items()):
        instance = instances_by_def.get(definition.id)
        out.append(
            {
                "agent_key": key,
                "display_name_en": definition.display_name_en,
                "display_name_ar": definition.display_name_ar,
                "mission": definition.mission,
                "risk_ceiling": definition.risk_ceiling,
                "enabled": instance.enabled if instance else definition.spec_json.get("enabled", False),
                "disabled_reason": (instance.disabled_reason if instance else definition.spec_json.get("disabled_reason")),
                "current_state": instance.current_state if instance else "not_activated",
            }
        )
    return out
