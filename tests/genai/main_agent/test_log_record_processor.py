# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Tests for GenAIMainAgentLogRecordProcessor."""

import unittest
from unittest.mock import MagicMock, Mock, patch

from microsoft.opentelemetry._constants import (
    GEN_AI_MAIN_AGENT_ID_KEY,
    GEN_AI_MAIN_AGENT_NAME_KEY,
)
from microsoft.opentelemetry._genai.main_agent._processor import (
    GenAIMainAgentLogRecordProcessor,
)


def _mock_span(attributes: dict, valid: bool = True) -> Mock:
    span = Mock()
    span.attributes = attributes
    span_context = Mock()
    span_context.is_valid = valid
    span.get_span_context.return_value = span_context
    return span


def _mock_log_record(attributes):
    log_data = MagicMock()
    log_data.log_record.attributes = attributes
    return log_data


class TestGenAIMainAgentLogRecordProcessorOnEmit(unittest.TestCase):
    def setUp(self) -> None:
        self.processor = GenAIMainAgentLogRecordProcessor()

    def test_no_current_span_does_nothing(self):
        log_data = _mock_log_record({"existing": "value"})

        with patch(
            "microsoft.opentelemetry._genai.main_agent._processor.trace.get_current_span",
            return_value=_mock_span({}, valid=False),
        ):
            self.processor.on_emit(log_data)

        self.assertEqual(log_data.log_record.attributes, {"existing": "value"})

    def test_span_without_main_agent_attrs_does_nothing(self):
        log_data = _mock_log_record({"existing": "value"})

        with patch(
            "microsoft.opentelemetry._genai.main_agent._processor.trace.get_current_span",
            return_value=_mock_span({"gen_ai.agent.name": "x"}),
        ):
            self.processor.on_emit(log_data)

        self.assertEqual(log_data.log_record.attributes, {"existing": "value"})

    def test_copies_main_agent_attrs_to_log_record(self):
        log_data = _mock_log_record({"existing": "value"})
        span_attrs = {
            GEN_AI_MAIN_AGENT_NAME_KEY: "main",
            GEN_AI_MAIN_AGENT_ID_KEY: "id-1",
            "unrelated": "skip-me",
        }

        with patch(
            "microsoft.opentelemetry._genai.main_agent._processor.trace.get_current_span",
            return_value=_mock_span(span_attrs),
        ):
            self.processor.on_emit(log_data)

        self.assertEqual(log_data.log_record.attributes[GEN_AI_MAIN_AGENT_NAME_KEY], "main")
        self.assertEqual(log_data.log_record.attributes[GEN_AI_MAIN_AGENT_ID_KEY], "id-1")
        self.assertEqual(log_data.log_record.attributes["existing"], "value")
        self.assertNotIn("unrelated", log_data.log_record.attributes)

    def test_initializes_attributes_dict_when_none(self):
        log_data = _mock_log_record(None)
        span_attrs = {GEN_AI_MAIN_AGENT_NAME_KEY: "main"}

        with patch(
            "microsoft.opentelemetry._genai.main_agent._processor.trace.get_current_span",
            return_value=_mock_span(span_attrs),
        ):
            self.processor.on_emit(log_data)

        self.assertEqual(log_data.log_record.attributes, {GEN_AI_MAIN_AGENT_NAME_KEY: "main"})


class TestGenAIMainAgentLogRecordProcessorLifecycle(unittest.TestCase):
    def test_shutdown_and_force_flush_are_noops(self):
        processor = GenAIMainAgentLogRecordProcessor()
        processor.shutdown()
        self.assertTrue(processor.force_flush())
