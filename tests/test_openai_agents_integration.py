# -------------------------------------------------------------------------
# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License. See License.txt in the project root for
# license information.
# --------------------------------------------------------------------------

"""Integration tests for OpenAI Agents instrumentation.

Validates that the upstream ``opentelemetry-instrumentation-openai-agents``
instrumentor can be loaded and activated, and that the A365 variant also
works correctly.
"""

import unittest

import pytest

agents = pytest.importorskip("agents")

# pylint: disable=wrong-import-position
from opentelemetry.instrumentation.openai_agents import OpenAIAgentsInstrumentor  # noqa: E402

from microsoft.opentelemetry._constants import (  # noqa: E402
    _SUPPORTED_INSTRUMENTED_LIBRARIES,
)

# pylint: enable=wrong-import-position


class TestOpenAIAgentsInstrumentationConfig(unittest.TestCase):
    """Verify openai_agents is registered in the distro's supported library lists."""

    def test_openai_agents_in_supported_libraries(self):
        self.assertIn("openai_agents", _SUPPORTED_INSTRUMENTED_LIBRARIES)


class TestOpenAIAgentsInstrumentorLifecycle(unittest.TestCase):
    """Verify the upstream OpenAIAgentsInstrumentor can be activated and torn down."""

    def setUp(self):
        self.instrumentor = OpenAIAgentsInstrumentor()
        if self.instrumentor.is_instrumented_by_opentelemetry:
            self.instrumentor.uninstrument()

    def tearDown(self):
        if self.instrumentor.is_instrumented_by_opentelemetry:
            self.instrumentor.uninstrument()

    def test_instrument_and_uninstrument(self):
        self.instrumentor.instrument()
        self.assertTrue(self.instrumentor.is_instrumented_by_opentelemetry)
        self.instrumentor.uninstrument()
        self.assertFalse(self.instrumentor.is_instrumented_by_opentelemetry)

    def test_instrumentation_dependencies(self):
        deps = self.instrumentor.instrumentation_dependencies()
        dep_str = " ".join(deps)
        self.assertIn("openai-agents", dep_str)


class TestOpenAIAgentsProcessorRegistration(unittest.TestCase):
    """Verify the instrumentor registers its processor into the agents SDK."""

    def setUp(self):
        self.instrumentor = OpenAIAgentsInstrumentor()
        if self.instrumentor.is_instrumented_by_opentelemetry:
            self.instrumentor.uninstrument()

    def tearDown(self):
        if self.instrumentor.is_instrumented_by_opentelemetry:
            self.instrumentor.uninstrument()

    def test_processor_registered_after_instrument(self):
        """After instrument(), a processor is registered in the agents SDK trace provider."""
        self.instrumentor.instrument()

        # The agents SDK exposes its trace provider
        provider = agents.tracing.get_trace_provider()
        # The DefaultTraceProvider stores processors in _multi_processor
        processors = getattr(provider, "_multi_processor", None)
        if processors is not None:
            proc_list = getattr(processors, "_processors", [])
            self.assertGreater(
                len(proc_list),
                0,
                "Expected at least one processor after instrument()",
            )
        else:
            # Fallback: just verify the instrumentor reports as instrumented
            self.assertTrue(self.instrumentor.is_instrumented_by_opentelemetry)

    def test_processor_removed_after_uninstrument(self):
        """After uninstrument(), the instrumentor reports as not instrumented."""
        self.instrumentor.instrument()
        self.assertTrue(self.instrumentor.is_instrumented_by_opentelemetry)
        self.instrumentor.uninstrument()
        self.assertFalse(self.instrumentor.is_instrumented_by_opentelemetry)


class TestA365OpenAIAgentsInstrumentor(unittest.TestCase):
    """Verify the in-repo A365 variant of the OpenAI Agents instrumentor."""

    def setUp(self):
        from microsoft.opentelemetry._genai._openai_agents._trace_instrumentor import (
            A365OpenAIAgentsInstrumentor,
        )

        self.instrumentor_cls = A365OpenAIAgentsInstrumentor
        A365OpenAIAgentsInstrumentor._processor = None
        A365OpenAIAgentsInstrumentor._is_instrumented_by_opentelemetry = False

    def tearDown(self):
        self.instrumentor_cls._processor = None
        self.instrumentor_cls._is_instrumented_by_opentelemetry = False

    def test_instrumentation_dependencies(self):
        inst = self.instrumentor_cls()
        deps = inst.instrumentation_dependencies()
        self.assertIn("openai-agents >= 0.0.7", deps)

    def test_instrument_creates_processor(self):
        """_instrument() creates and registers a trace processor."""
        from unittest.mock import MagicMock, patch

        with patch("microsoft.opentelemetry._genai._openai_agents._trace_instrumentor.trace_api") as mock_trace_api:
            mock_trace_api.get_tracer.return_value = MagicMock()
            inst = self.instrumentor_cls()

            with patch.dict("sys.modules", {"agents.tracing": agents.tracing}):
                with patch(
                    "microsoft.opentelemetry._genai._openai_agents._trace_instrumentor.OpenAIAgentsTraceProcessor"
                ) as MockProc:
                    MockProc.return_value = MagicMock()
                    inst._instrument()
                    MockProc.assert_called_once()


if __name__ == "__main__":
    unittest.main()
