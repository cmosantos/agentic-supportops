import json
from hashlib import sha256

from db.models import IncidentRecord
from domain.investigation import GoalProfile, InvestigationGoal


_GOAL_FOCUS_BY_CATEGORY = {
    "identity": "account, access, license, mailbox, or mailbox permissions",
    "messaging": "mailbox state, quota, permissions, or message delivery",
    "endpoint": "device state, local resource pressure, or a device service",
    "network": "network configuration, gateway and external connectivity, or DNS",
    "infrastructure": (
        "application health, host state, resource metrics, alerts, or service health"
    ),
}

_GOAL_PROFILE_QUESTIONS: dict[GoalProfile, str] = {
    GoalProfile.ROOT_CAUSE: "Determine the most likely incident cause",
    GoalProfile.ACCOUNT_LOCK_STATE: (
        "Determine whether the affected user account is locked"
    ),
    GoalProfile.APPLICATION_AVAILABILITY: (
        "Determine whether the affected application is available"
    ),
    GoalProfile.EVIDENCE_SUFFICIENCY: (
        "Determine whether the persisted diagnostic evidence is sufficient, "
        "consistent, and relevant to support a finding and recommendation"
    ),
}


def build_investigation_goal(
    incident: IncidentRecord | None = None,
    profile: GoalProfile = GoalProfile.ROOT_CAUSE,
) -> InvestigationGoal:
    """Build the application-owned question and boundaries for one run."""
    if not isinstance(profile, GoalProfile):
        raise TypeError("profile must be a GoalProfile")
    question = _GOAL_PROFILE_QUESTIONS[profile]
    if profile is GoalProfile.ROOT_CAUSE:
        focus = _GOAL_FOCUS_BY_CATEGORY.get(
            incident.category.casefold() if incident is not None else ""
        )
        if focus is not None:
            question = f"Determine whether the incident is caused by {focus}"
    return InvestigationGoal(
        objective=(
            f"{question} using persisted diagnostic evidence. "
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


def investigation_goal_fingerprint(goal: InvestigationGoal) -> str:
    """Return a safe correlation key for the exact persisted goal snapshot."""
    canonical = json.dumps(
        goal.model_dump(mode="json"),
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    return sha256(canonical.encode("utf-8")).hexdigest()[:16]


def investigation_goal_trace_attributes(
    goal: InvestigationGoal,
) -> dict[str, str | bool]:
    return {
        "supportops.investigation.goal_driven": True,
        "supportops.investigation.goal_fingerprint": (
            investigation_goal_fingerprint(goal)
        ),
        "supportops.investigation.human_action_required": goal.human_action_required,
    }


def build_investigation_input(
    incident: IncidentRecord,
    goal: InvestigationGoal | None = None,
) -> str:
    """Describe what is required; runtime governance remains authoritative."""
    goal = goal or build_investigation_goal()
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
