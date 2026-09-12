from collections.abc import Sequence

from domain.investigation import (
    InvestigationGoal,
    InvestigationPlan,
    InvestigationPlanStep,
)
from services.playbooks import PlaybookStep
from services.tool_registry import InvestigationToolRegistry


ResolvedPlaybook = Sequence[tuple[PlaybookStep, dict[str, str]]]


def build_model_guided_investigation_plan(
    goal: InvestigationGoal,
) -> InvestigationPlan:
    """Build the controlled high-level path shared by model-guided runtimes."""
    return InvestigationPlan(
        steps=[
            InvestigationPlanStep(
                sequence=1,
                intended_action="Collect incident-scoped diagnostic evidence.",
                purpose=f"Establish factual observations needed to pursue: {goal.objective}",
            ),
            InvestigationPlanStep(
                sequence=2,
                intended_action="Evaluate the collected evidence against the investigation goal.",
                purpose=(
                    "Identify sufficient, missing, or conflicting evidence before "
                    "forming a finding."
                ),
            ),
            InvestigationPlanStep(
                sequence=3,
                intended_action="Produce a grounded finding and bounded recommendations.",
                purpose=(
                    "Separate observed facts from assessment and remain within the "
                    "application-owned goal constraints."
                ),
            ),
        ]
    )


def build_deterministic_investigation_plan(
    goal: InvestigationGoal,
    resolved_playbook: ResolvedPlaybook,
    tools: InvestigationToolRegistry,
) -> InvestigationPlan:
    """Describe the resolved playbook without becoming an execution authority."""
    return InvestigationPlan(
        steps=[
            InvestigationPlanStep(
                sequence=sequence,
                intended_action=(
                    f"Run the read-only {step.tool} check with "
                    f"{_format_arguments(arguments)}."
                ),
                purpose=(
                    f"{tools.description(step.tool)} "
                    f"This observation supports the objective: {goal.objective}"
                ),
            )
            for sequence, (step, arguments) in enumerate(resolved_playbook, start=1)
        ]
    )


def _format_arguments(arguments: dict[str, str]) -> str:
    return ", ".join(
        f"{name}={value}" for name, value in sorted(arguments.items())
    )
