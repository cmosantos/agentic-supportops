from typing import Any

from domain.ai import InvestigationEventType, InvestigationRuntime
from repositories.investigation_repository import InvestigationRepository
from observability.tracing import TraceBoundary


class InvestigationEventRecorder:
    def __init__(
        self,
        repository: InvestigationRepository,
        investigation_id: int,
        runtime: InvestigationRuntime,
    ) -> None:
        self._repository = repository
        self._investigation_id = investigation_id
        self._runtime = runtime
        self._sequence = repository.next_event_sequence(investigation_id)

    def record(self, event_type: InvestigationEventType, **fields: Any) -> None:
        if fields.get("duration_ms") is not None:
            fields["duration_ms"] = max(0.0, fields["duration_ms"])
        fields.setdefault("metadata", {})
        fields["metadata"] = {
            **fields["metadata"],
            **TraceBoundary.current_ids(),
        }
        self._repository.record_event(
            self._investigation_id,
            self._runtime,
            event_type,
            self._sequence,
            **fields,
        )
        self._sequence += 1
