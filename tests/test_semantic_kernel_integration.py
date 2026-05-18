# -------------------------------------------------------------------------
# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License. See License.txt in the project root for
# license information.
# --------------------------------------------------------------------------

"""Integration tests for Semantic Kernel instrumentation.

Validates that the in-repo ``SemanticKernelInstrumentor`` can be loaded,
activated, and that the span processor correctly transforms SK spans.
Full integration tests are skipped when the ``semantic-kernel`` package
is not installed.
"""

import unittest
from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("semantic_kernel")

# pylint: disable=wrong-import-position
from microsoft.opentelemetry._semantic_kernel._span_processor import SemanticKernelSpanProcessor  # noqa: E402
from microsoft.opentelemetry._semantic_kernel._trace_instrumentor import SemanticKernelInstrumentor  # noqa: E402

from microsoft.opentelemetry._constants import (  # noqa: E402
    _SUPPORTED_INSTRUMENTED_LIBRARIES,
)

# pylint: enable=wrong-import-position


class TestSemanticKernelInstrumentationConfig(unittest.TestCase):
    """Verify semantic_kernel is registered in the distro's supported library lists."""

    def test_semantic_kernel_in_supported_libraries(self):
        self.assertIn("semantic_kernel", _SUPPORTED_INSTRUMENTED_LIBRARIES)


class TestSemanticKernelInstrumentorLifecycle(unittest.TestCase):
    """Verify the SemanticKernelInstrumentor can be activated and torn down."""

    def setUp(self):
        from microsoft.opentelemetry.a365.core.exporters.enriching_span_processor import (
            unregister_span_enricher,
        )

        unregister_span_enricher()

    def tearDown(self):
        from microsoft.opentelemetry.a365.core.exporters.enriching_span_processor import (
            unregister_span_enricher,
        )

        unregister_span_enricher()

    def test_instrumentation_dependencies(self):
        inst = SemanticKernelInstrumentor()
        deps = inst.instrumentation_dependencies()
        dep_str = " ".join(deps)
        self.assertIn("semantic-kernel", dep_str)

    @patch("microsoft.opentelemetry._semantic_kernel._trace_instrumentor.get_tracer_provider")
    def test_instrument_adds_span_processor(self, mock_get_provider):
        mock_provider = MagicMock()
        mock_get_provider.return_value = mock_provider

        inst = SemanticKernelInstrumentor()
        inst._instrument()

        mock_provider.add_span_processor.assert_called_once()
        args, _ = mock_provider.add_span_processor.call_args
        self.assertIsInstance(args[0], SemanticKernelSpanProcessor)

    @patch("microsoft.opentelemetry._semantic_kernel._trace_instrumentor.get_tracer_provider")
    def test_uninstrument_clears_state(self, mock_get_provider):
        mock_get_provider.return_value = MagicMock()

        inst = SemanticKernelInstrumentor()
        inst._instrument()
        self.assertIsNotNone(inst._processor)
        inst._uninstrument()
        # Enricher ownership should be released after uninstrument
        self.assertFalse(inst._owns_enricher)


class TestSemanticKernelSpanProcessor(unittest.TestCase):
    """Verify the SemanticKernelSpanProcessor transforms spans correctly."""

    def test_chat_span_gets_renamed(self):
        """A span named 'chat.completions gpt-4o' gets renamed."""
        processor = SemanticKernelSpanProcessor()
        mock_span = MagicMock()
        mock_span.name = "chat.completions gpt-4o"
        mock_span.attributes = {"gen_ai.operation.name": "chat"}
        processor.on_start(mock_span)
        mock_span.update_name.assert_called()

    def test_non_genai_span_unchanged(self):
        """A regular span is not modified by the processor."""
        processor = SemanticKernelSpanProcessor()
        mock_span = MagicMock()
        mock_span.name = "http.request"
        mock_span.attributes = {}
        processor.on_start(mock_span)
        mock_span.update_name.assert_not_called()


if __name__ == "__main__":
    unittest.main()
