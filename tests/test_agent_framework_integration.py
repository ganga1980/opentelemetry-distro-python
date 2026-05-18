# -------------------------------------------------------------------------
# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License. See License.txt in the project root for
# license information.
# --------------------------------------------------------------------------

"""Integration tests for Agent Framework instrumentation.

Validates that the in-repo ``AgentFrameworkInstrumentor`` can be loaded,
activated, and that the span processor and enricher are correctly registered.
"""

import unittest
from unittest.mock import MagicMock, patch

from microsoft.opentelemetry.a365.core.exporters.enriching_span_processor import (
    unregister_span_enricher,
)

from microsoft.opentelemetry._agent_framework._span_processor import AgentFrameworkSpanProcessor
from microsoft.opentelemetry._agent_framework._trace_instrumentor import AgentFrameworkInstrumentor

from microsoft.opentelemetry._constants import _SUPPORTED_INSTRUMENTED_LIBRARIES


class TestAgentFrameworkInstrumentationConfig(unittest.TestCase):
    """Verify agent_framework is registered in the distro's supported library lists."""

    def test_agent_framework_in_supported_libraries(self):
        self.assertIn("agent_framework", _SUPPORTED_INSTRUMENTED_LIBRARIES)


class TestAgentFrameworkInstrumentorLifecycle(unittest.TestCase):
    """Verify the AgentFrameworkInstrumentor can be activated and torn down."""

    def setUp(self):
        unregister_span_enricher()
        AgentFrameworkInstrumentor._instance = None
        AgentFrameworkInstrumentor._is_instrumented_by_opentelemetry = False

    def tearDown(self):
        unregister_span_enricher()
        AgentFrameworkInstrumentor._instance = None
        AgentFrameworkInstrumentor._is_instrumented_by_opentelemetry = False

    def test_instrumentation_dependencies(self):
        inst = AgentFrameworkInstrumentor()
        deps = inst.instrumentation_dependencies()
        dep_str = " ".join(deps)
        self.assertIn("agent-framework", dep_str)

    @patch("microsoft.opentelemetry._agent_framework._trace_instrumentor.get_tracer_provider")
    def test_instrument_adds_span_processor(self, mock_get_provider):
        mock_provider = MagicMock()
        mock_get_provider.return_value = mock_provider

        inst = AgentFrameworkInstrumentor()
        inst._instrument()

        mock_provider.add_span_processor.assert_called_once()
        args, _ = mock_provider.add_span_processor.call_args
        self.assertIsInstance(args[0], AgentFrameworkSpanProcessor)

    @patch("microsoft.opentelemetry.a365.core.exporters.enriching_span_processor.register_span_enricher")
    @patch("microsoft.opentelemetry._agent_framework._trace_instrumentor.get_tracer_provider")
    def test_instrument_registers_enricher(self, mock_get_provider, mock_register):
        mock_get_provider.return_value = MagicMock()

        inst = AgentFrameworkInstrumentor()
        inst._instrument()

        mock_register.assert_called_once()

    @patch("microsoft.opentelemetry.a365.core.exporters.enriching_span_processor.unregister_span_enricher")
    @patch("microsoft.opentelemetry.a365.core.exporters.enriching_span_processor.register_span_enricher")
    @patch("microsoft.opentelemetry._agent_framework._trace_instrumentor.get_tracer_provider")
    def test_uninstrument_clears_enricher(self, mock_get_provider, mock_register, mock_unregister):
        mock_get_provider.return_value = MagicMock()

        inst = AgentFrameworkInstrumentor()
        inst._instrument()
        inst._uninstrument()

        mock_unregister.assert_called_once()

    @patch("microsoft.opentelemetry._agent_framework._trace_instrumentor.get_tracer_provider")
    def test_enable_sensitive_data_kwarg(self, mock_get_provider):
        """enable_sensitive_data kwarg is forwarded to the SDK."""
        mock_provider = MagicMock()
        mock_get_provider.return_value = mock_provider

        mock_af_obs = MagicMock()
        with patch.dict("sys.modules", {"agent_framework.observability": mock_af_obs}):
            inst = AgentFrameworkInstrumentor()
            inst._instrument(enable_sensitive_data=True)
            mock_af_obs.enable_instrumentation.assert_called_once_with(enable_sensitive_data=True)


class TestAgentFrameworkSpanProcessor(unittest.TestCase):
    """Verify the AgentFrameworkSpanProcessor transforms spans correctly."""

    def test_agent_span_gets_enriched(self):
        """A span with agent framework attributes is processed."""
        processor = AgentFrameworkSpanProcessor()
        mock_span = MagicMock()
        mock_span.name = "agent.execute"
        mock_span.attributes = {"gen_ai.operation.name": "execute"}
        # Should not raise
        processor.on_start(mock_span)

    def test_non_agent_span_unchanged(self):
        """A regular span is not modified by the processor."""
        processor = AgentFrameworkSpanProcessor()
        mock_span = MagicMock()
        mock_span.name = "http.request"
        mock_span.attributes = {}
        processor.on_start(mock_span)
        mock_span.update_name.assert_not_called()


if __name__ == "__main__":
    unittest.main()
