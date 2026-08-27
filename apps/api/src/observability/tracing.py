from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from typing import Any

from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import (
    ConsoleSpanExporter,
    SimpleSpanProcessor,
    SpanExporter,
)


SAFE_RESOURCE_KEYS = {
    "device_id",
    "host_id",
    "application_id",
    "mailbox_id",
    "user_id",
    "resource_id",
    "reference",
}


class TraceBoundary:
    """Small opt-in OpenTelemetry boundary; domain code never owns exporters."""

    def __init__(
        self,
        *,
        enabled: bool = False,
        service_name: str = "agentic-supportops",
        exporter: str = "none",
        span_exporter: SpanExporter | None = None,
    ) -> None:
        self.enabled = enabled
        self.exporter = exporter
        self._provider: TracerProvider | None = None
        if not enabled:
            self._tracer = trace.NoOpTracerProvider().get_tracer(service_name)
            return

        provider = TracerProvider(
            resource=Resource.create({"service.name": service_name})
        )
        selected_exporter = span_exporter
        if selected_exporter is None and exporter == "console":
            selected_exporter = ConsoleSpanExporter()
        if selected_exporter is not None:
            provider.add_span_processor(SimpleSpanProcessor(selected_exporter))
        elif exporter != "none":
            raise ValueError("OTEL_EXPORTER must be 'none' or 'console'")
        self._provider = provider
        self._tracer = provider.get_tracer("agentic-supportops")

    @contextmanager
    def span(
        self,
        name: str,
        attributes: Mapping[str, Any] | None = None,
    ) -> Iterator[trace.Span]:
        with self._tracer.start_as_current_span(
            name,
            attributes=dict(attributes or {}),
            record_exception=False,
            set_status_on_exception=False,
        ) as span:
            try:
                yield span
            except Exception as error:
                span.set_attribute("error.type", type(error).__name__)
                span.set_status(Status(StatusCode.ERROR))
                raise

    def shutdown(self) -> None:
        if self._provider is not None:
            self._provider.shutdown()

    @staticmethod
    def current_ids() -> dict[str, str]:
        context = trace.get_current_span().get_span_context()
        if not context.is_valid:
            return {}
        return {
            "trace_id": format(context.trace_id, "032x"),
            "span_id": format(context.span_id, "016x"),
        }

    @staticmethod
    def safe_resource_attributes(arguments: Mapping[str, Any]) -> dict[str, str]:
        return {
            f"supportops.resource.{key}": str(value)[:100]
            for key, value in arguments.items()
            if key in SAFE_RESOURCE_KEYS and isinstance(value, str)
        }


def build_trace_boundary(settings) -> TraceBoundary:
    return TraceBoundary(
        enabled=settings.otel_enabled,
        service_name=settings.otel_service_name,
        exporter=settings.otel_exporter,
    )
