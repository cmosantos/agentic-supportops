import json

from db.models import IncidentRecord
from domain.investigation import InvestigationGoal


def build_investigation_input(incident: IncidentRecord) -> str:
    """Describe what is required; runtime governance remains authoritative."""
    goal = InvestigationGoal(
        objective=(
            "Determine the most likely incident cause using persisted diagnostic evidence. "
            "Explicitly report insufficient or conflicting evidence. Recommend next steps "
            "and, only when justified, propose one bounded remediation for human approval."
        ),
        success_criteria=[
            "Ground the diagnosis in persisted tool evidence from this investigation.",
            "Explicitly report insufficient or conflicting evidence and missing information.",
            "Preserve supplied resource identifiers exactly.",
            "Distinguish observed facts from assessment in recommendations.",
            "Do not treat any remediation as executed during investigation.",
        ],
        constraints=[
            "The incident title is a symptom label, not proof or diagnostic evidence.",
            "Use only read-only investigation tools; mutations are outside investigation.",
            "Never invent identifiers or infrastructure facts; respect supplied resource scope.",
            "Respect application-enforced tool-call, repeated-call, and turn limits.",
            "Proposed actions require human review and approval before controlled execution.",
        ],
        human_action_required=True,
    )
    return json.dumps({
        "goal": goal.model_dump(mode="json"),
        "incident": {
            "catalog_id": incident.catalog_id,
            "title": incident.title,
            "description": incident.description,
            "category": incident.category,
            "priority": incident.priority.value,
            "affected_resource_type": incident.affected_resource_type,
            "affected_resource_id": incident.affected_resource_id,
            "investigation_context": incident.investigation_context,
        },
    })
