"""Loads and validates packages/agent-specs/*.yaml at API startup.

Constitution section 8: "Store definitions in versioned YAML or JSON plus
human-readable constitution files. Validate them at startup." A malformed
or schema-violating spec must fail startup loudly, not silently skip - an
agent with an invalid contract must not be able to run.
"""
import json
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator

from contracts.risk_levels import RiskLevel


class AgentSpecError(Exception):
    pass


def _schema_path(agent_specs_dir: Path) -> Path:
    return agent_specs_dir / "schema.json"


def load_agent_specs(agent_specs_dir: str | Path) -> dict[str, dict]:
    directory = Path(agent_specs_dir)
    schema_file = _schema_path(directory)
    if not schema_file.exists():
        raise AgentSpecError(f"Missing agent-spec schema at {schema_file}")

    schema = json.loads(schema_file.read_text())
    validator = Draft202012Validator(schema)

    specs: dict[str, dict] = {}
    errors: list[str] = []

    for yaml_file in sorted(directory.glob("*.yaml")):
        try:
            raw = yaml.safe_load(yaml_file.read_text())
        except yaml.YAMLError as exc:
            errors.append(f"{yaml_file.name}: invalid YAML - {exc}")
            continue

        validation_errors = sorted(validator.iter_errors(raw), key=lambda e: e.path)
        if validation_errors:
            for err in validation_errors:
                errors.append(f"{yaml_file.name}: {err.message} (at {'/'.join(str(p) for p in err.path)})")
            continue

        if raw["id"] != yaml_file.stem:
            errors.append(f"{yaml_file.name}: id '{raw['id']}' must match filename stem '{yaml_file.stem}'")
            continue

        try:
            RiskLevel(raw["risk_ceiling"])
        except ValueError:
            errors.append(f"{yaml_file.name}: risk_ceiling '{raw['risk_ceiling']}' is not a valid RiskLevel")
            continue

        if raw["risk_ceiling"] == RiskLevel.R5.value:
            errors.append(f"{yaml_file.name}: risk_ceiling must never be R5 (R5 is always-prohibited, not a ceiling)")
            continue

        specs[raw["id"]] = raw

    if errors:
        raise AgentSpecError(
            f"{len(errors)} agent-spec validation error(s):\n" + "\n".join(f"  - {e}" for e in errors)
        )

    return specs
