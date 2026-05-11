# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Tests for GenAIMainAgentSpanProcessor."""

import unittest
from unittest.mock import MagicMock, Mock, patch

from microsoft.opentelemetry._constants import (
    GEN_AI_MAIN_AGENT_CONVERSATION_ID_KEY,
    GEN_AI_MAIN_AGENT_ID_KEY,
    GEN_AI_MAIN_AGENT_NAME_KEY,
    GEN_AI_MAIN_AGENT_VERSION_KEY,
)
from microsoft.opentelemetry._genai.main_agent._processor import (
    GenAIMainAgentSpanProcessor,
)
from microsoft.opentelemetry.a365.core.constants import (
    GEN_AI_AGENT_ID_KEY,
    GEN_AI_AGENT_NAME_KEY,
    GEN_AI_AGENT_VERSION_KEY,
    GEN_AI_CONVERSATION_ID_KEY,
    GEN_AI_OPERATION_NAME_KEY,
    INVOKE_AGENT_OPERATION_NAME,
)


def _mock_parent_span(attributes: dict, valid: bool = True) -> Mock:
    parent = Mock()
    parent.attributes = attributes
    span_context = Mock()
    span_context.is_valid = valid
    parent.get_span_context.return_value = span_context
    return parent


def _mock_invalid_parent_span() -> Mock:
    parent = Mock()
    span_context = Mock()
    span_context.is_valid = False
    parent.get_span_context.return_value = span_context
    return parent


class TestGenAIMainAgentSpanProcessorOnStart(unittest.TestCase):
    """on_start propagation from parent span to current span."""

    def setUp(self) -> None:
        self.processor = GenAIMainAgentSpanProcessor()
        self.span = MagicMock()

    def test_no_valid_parent_does_nothing(self):
        with patch(
            "microsoft.opentelemetry._genai.main_agent._processor.trace.get_current_span",
            return_value=_mock_invalid_parent_span(),
        ):
            self.processor.on_start(self.span, parent_context=None)

        self.span.set_attribute.assert_not_called()

    def test_parent_with_no_relevant_attributes_does_nothing(self):
        with patch(
            "microsoft.opentelemetry._genai.main_agent._processor.trace.get_current_span",
            return_value=_mock_parent_span({"unrelated": "value"}),
        ):
            self.processor.on_start(self.span, parent_context=None)

        self.span.set_attribute.assert_not_called()

    def test_parent_with_only_fallbacks_copies_fallbacks(self):
        parent_attrs = {
            GEN_AI_AGENT_NAME_KEY: "main",
            GEN_AI_AGENT_ID_KEY: "id-1",
            GEN_AI_AGENT_VERSION_KEY: "v1",
            GEN_AI_CONVERSATION_ID_KEY: "conv-1",
        }
        with patch(
            "microsoft.opentelemetry._genai.main_agent._processor.trace.get_current_span",
            return_value=_mock_parent_span(parent_attrs),
        ):
            self.processor.on_start(self.span, parent_context=None)

        self.span.set_attribute.assert_any_call(GEN_AI_MAIN_AGENT_NAME_KEY, "main")
        self.span.set_attribute.assert_any_call(GEN_AI_MAIN_AGENT_ID_KEY, "id-1")
        self.span.set_attribute.assert_any_call(GEN_AI_MAIN_AGENT_VERSION_KEY, "v1")
        self.span.set_attribute.assert_any_call(GEN_AI_MAIN_AGENT_CONVERSATION_ID_KEY, "conv-1")
        self.assertEqual(self.span.set_attribute.call_count, 4)

    def test_parent_with_primary_wins_over_fallback(self):
        parent_attrs = {
            GEN_AI_MAIN_AGENT_NAME_KEY: "primary",
            GEN_AI_AGENT_NAME_KEY: "fallback",
        }
        with patch(
            "microsoft.opentelemetry._genai.main_agent._processor.trace.get_current_span",
            return_value=_mock_parent_span(parent_attrs),
        ):
            self.processor.on_start(self.span, parent_context=None)

        self.span.set_attribute.assert_called_once_with(GEN_AI_MAIN_AGENT_NAME_KEY, "primary")

    def test_parent_with_mixed_primary_and_fallback(self):
        parent_attrs = {
            GEN_AI_MAIN_AGENT_NAME_KEY: "primary-name",
            GEN_AI_AGENT_ID_KEY: "fallback-id",
        }
        with patch(
            "microsoft.opentelemetry._genai.main_agent._processor.trace.get_current_span",
            return_value=_mock_parent_span(parent_attrs),
        ):
            self.processor.on_start(self.span, parent_context=None)

        self.span.set_attribute.assert_any_call(GEN_AI_MAIN_AGENT_NAME_KEY, "primary-name")
        self.span.set_attribute.assert_any_call(GEN_AI_MAIN_AGENT_ID_KEY, "fallback-id")
        self.assertEqual(self.span.set_attribute.call_count, 2)


class TestGenAIMainAgentSpanProcessorOnEnd(unittest.TestCase):
    """on_end self-copy when this span itself is the top-level invoke_agent."""

    def setUp(self) -> None:
        self.processor = GenAIMainAgentSpanProcessor()

    def test_skipped_when_not_invoke_agent(self):
        span = MagicMock()
        span.attributes = {GEN_AI_OPERATION_NAME_KEY: "chat"}

        self.processor.on_end(span)

        span.set_attribute.assert_not_called()

    def test_skipped_when_main_agent_attribute_already_present(self):
        span = MagicMock()
        span.attributes = {
            GEN_AI_OPERATION_NAME_KEY: INVOKE_AGENT_OPERATION_NAME,
            GEN_AI_MAIN_AGENT_NAME_KEY: "already-set",
            GEN_AI_AGENT_NAME_KEY: "self",
        }

        self.processor.on_end(span)

        span.set_attribute.assert_not_called()

    def test_copies_self_attributes_when_invoke_agent_and_unenriched(self):
        span = MagicMock()
        span.attributes = {
            GEN_AI_OPERATION_NAME_KEY: INVOKE_AGENT_OPERATION_NAME,
            GEN_AI_AGENT_NAME_KEY: "self-name",
            GEN_AI_AGENT_ID_KEY: "self-id",
            GEN_AI_AGENT_VERSION_KEY: "self-v",
            GEN_AI_CONVERSATION_ID_KEY: "self-conv",
        }

        self.processor.on_end(span)

        span.set_attribute.assert_any_call(GEN_AI_MAIN_AGENT_NAME_KEY, "self-name")
        span.set_attribute.assert_any_call(GEN_AI_MAIN_AGENT_ID_KEY, "self-id")
        span.set_attribute.assert_any_call(GEN_AI_MAIN_AGENT_VERSION_KEY, "self-v")
        span.set_attribute.assert_any_call(GEN_AI_MAIN_AGENT_CONVERSATION_ID_KEY, "self-conv")
        self.assertEqual(span.set_attribute.call_count, 4)

    def test_copies_only_present_attributes(self):
        span = MagicMock()
        span.attributes = {
            GEN_AI_OPERATION_NAME_KEY: INVOKE_AGENT_OPERATION_NAME,
            GEN_AI_AGENT_NAME_KEY: "only-name",
        }

        self.processor.on_end(span)

        span.set_attribute.assert_called_once_with(GEN_AI_MAIN_AGENT_NAME_KEY, "only-name")

    def test_noop_when_span_has_no_set_attribute(self):
        # ReadableSpan-only objects (no ``set_attribute``) must not raise.
        span = MagicMock(spec=["attributes"])
        span.attributes = {
            GEN_AI_OPERATION_NAME_KEY: INVOKE_AGENT_OPERATION_NAME,
            GEN_AI_AGENT_NAME_KEY: "self-name",
        }

        self.processor.on_end(span)  # must not raise

        self.assertFalse(hasattr(span, "set_attribute"))


class TestGenAIMainAgentSpanProcessorLifecycle(unittest.TestCase):
    def test_shutdown_and_force_flush_are_noops(self):
        processor = GenAIMainAgentSpanProcessor()
        processor.shutdown()
        self.assertTrue(processor.force_flush())
