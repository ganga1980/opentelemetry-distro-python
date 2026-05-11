# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Span and log-record processors that propagate
``microsoft.gen_ai.main_agent.*`` attributes from the top-level
(user-facing) GenAI agent so that all downstream telemetry is attributed
to the main agent rather than internal sub-agents in a multi-agent system.
"""

from microsoft.opentelemetry._constants import (
    GEN_AI_MAIN_AGENT_ATTRIBUTE_PREFIX,
    GEN_AI_MAIN_AGENT_CONVERSATION_ID_KEY,
    GEN_AI_MAIN_AGENT_ID_KEY,
    GEN_AI_MAIN_AGENT_NAME_KEY,
    GEN_AI_MAIN_AGENT_VERSION_KEY,
)
from microsoft.opentelemetry.a365.core.constants import (
    GEN_AI_AGENT_ID_KEY,
    GEN_AI_AGENT_NAME_KEY,
    GEN_AI_AGENT_VERSION_KEY,
    GEN_AI_CONVERSATION_ID_KEY,
    GEN_AI_OPERATION_NAME_KEY,
    INVOKE_AGENT_OPERATION_NAME,
)
from opentelemetry import context as context_api
from opentelemetry import trace
from opentelemetry.sdk._logs import LogRecordProcessor, ReadWriteLogRecord
from opentelemetry.sdk.trace import ReadableSpan, Span
from opentelemetry.sdk.trace.export import SpanProcessor

# Each row: (target attribute on current span,
#            primary source attribute on parent span,
#            fallback source attribute on parent span)
_PROPAGATION_TABLE: tuple[tuple[str, str, str], ...] = (
    (GEN_AI_MAIN_AGENT_NAME_KEY, GEN_AI_MAIN_AGENT_NAME_KEY, GEN_AI_AGENT_NAME_KEY),
    (GEN_AI_MAIN_AGENT_ID_KEY, GEN_AI_MAIN_AGENT_ID_KEY, GEN_AI_AGENT_ID_KEY),
    (GEN_AI_MAIN_AGENT_VERSION_KEY, GEN_AI_MAIN_AGENT_VERSION_KEY, GEN_AI_AGENT_VERSION_KEY),
    (
        GEN_AI_MAIN_AGENT_CONVERSATION_ID_KEY,
        GEN_AI_MAIN_AGENT_CONVERSATION_ID_KEY,
        GEN_AI_CONVERSATION_ID_KEY,
    ),
)

# Used at on_end to copy the current span's own gen_ai.* attributes onto the
# microsoft.gen_ai.main_agent.* attributes when the span is the top-level
# invoke_agent span and no main_agent.* attribute has been propagated yet.
_SELF_COPY_TABLE: tuple[tuple[str, str], ...] = tuple(
    (target, fallback) for target, _primary, fallback in _PROPAGATION_TABLE
)


class GenAIMainAgentSpanProcessor(SpanProcessor):
    """Propagates ``microsoft.gen_ai.main_agent.*`` attributes onto spans.

    On ``on_start``: copies main-agent attributes from the parent span (or
    falls back to the parent's ``gen_ai.agent.*`` / ``gen_ai.conversation.id``
    attributes) onto the new span.

    On ``on_end``: when the span is itself an ``invoke_agent`` operation and
    has not already been enriched, copies its own ``gen_ai.agent.*`` /
    ``gen_ai.conversation.id`` attributes onto ``microsoft.gen_ai.main_agent.*``
    so the top-level agent identifies itself as the main agent.
    """

    def on_start(self, span: Span, parent_context: context_api.Context | None = None) -> None:
        parent = trace.get_current_span(parent_context)
        if not parent.get_span_context().is_valid:
            return

        parent_attributes = getattr(parent, "attributes", None) or {}
        for target, primary, fallback in _PROPAGATION_TABLE:
            value = parent_attributes.get(primary)
            if value is None:
                value = parent_attributes.get(fallback)
            if value is not None:
                span.set_attribute(target, value)

    def on_end(self, span: ReadableSpan) -> None:
        attributes = span.attributes or {}
        if attributes.get(GEN_AI_OPERATION_NAME_KEY) != INVOKE_AGENT_OPERATION_NAME:
            return

        for key in attributes:
            if key.startswith(GEN_AI_MAIN_AGENT_ATTRIBUTE_PREFIX):
                return

        if not hasattr(span, "set_attribute"):
            return
        for target, source in _SELF_COPY_TABLE:
            value = attributes.get(source)
            if value is not None:
                span.set_attribute(target, value)  # type: ignore[attr-defined]

    def shutdown(self) -> None:
        pass

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return True


class GenAIMainAgentLogRecordProcessor(LogRecordProcessor):
    """Copies any ``microsoft.gen_ai.main_agent.*`` attributes from the
    current span onto every emitted log record.
    """

    def on_emit(self, log_record: ReadWriteLogRecord) -> None:
        span = trace.get_current_span()
        if not span.get_span_context().is_valid:
            return

        span_attributes = getattr(span, "attributes", None) or {}
        main_agent_attributes = {
            key: value for key, value in span_attributes.items() if key.startswith(GEN_AI_MAIN_AGENT_ATTRIBUTE_PREFIX)
        }
        if not main_agent_attributes:
            return

        if log_record.log_record.attributes is None:
            log_record.log_record.attributes = {}
        for key, value in main_agent_attributes.items():
            log_record.log_record.attributes[key] = value  # type: ignore[index]

    def shutdown(self) -> None:
        pass

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return True
