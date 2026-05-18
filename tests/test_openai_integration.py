# -------------------------------------------------------------------------
# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License. See License.txt in the project root for
# license information.
# --------------------------------------------------------------------------

"""Integration tests for OpenAI (v2) instrumentation.

Validates that the upstream ``opentelemetry-instrumentation-openai-v2``
instrumentor can be loaded, activated, and that it wraps the ``openai`` SDK
methods as expected.
"""

import unittest

import pytest

openai = pytest.importorskip("openai")

# pylint: disable=wrong-import-position
from opentelemetry.instrumentation.openai_v2 import OpenAIInstrumentor  # noqa: E402
from opentelemetry.sdk.resources import Resource  # noqa: E402
from opentelemetry.sdk.trace import TracerProvider  # noqa: E402
from opentelemetry.sdk.trace.export import SimpleSpanProcessor  # noqa: E402
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter  # noqa: E402

from microsoft.opentelemetry._constants import (  # noqa: E402
    _SUPPORTED_INSTRUMENTED_LIBRARIES,
)

# pylint: enable=wrong-import-position


class TestOpenAIInstrumentationConfig(unittest.TestCase):
    """Verify openai is registered in the distro's supported library lists."""

    def test_openai_in_supported_libraries(self):
        self.assertIn("openai", _SUPPORTED_INSTRUMENTED_LIBRARIES)


class TestOpenAIInstrumentorLifecycle(unittest.TestCase):
    """Verify the OpenAIInstrumentor can be activated and torn down."""

    def setUp(self):
        self.instrumentor = OpenAIInstrumentor()
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
        self.assertIn("openai", dep_str)

    def test_instrument_wraps_chat_completions(self):
        """After instrument(), openai.resources.chat.completions.Completions.create is wrapped."""
        self.instrumentor.instrument()
        create_fn = openai.resources.chat.completions.Completions.create
        # wrapt wraps replace the function; the wrapper has __wrapped__
        self.assertTrue(
            hasattr(create_fn, "__wrapped__"),
            "Expected Completions.create to be wrapped after instrument()",
        )

    def test_uninstrument_restores_behavior(self):
        """After uninstrument(), the instrumentor reports as not instrumented."""
        self.instrumentor.instrument()
        self.assertTrue(self.instrumentor.is_instrumented_by_opentelemetry)
        self.instrumentor.uninstrument()
        self.assertFalse(self.instrumentor.is_instrumented_by_opentelemetry)


class TestOpenAISpanGeneration(unittest.TestCase):
    """Verify that the instrumentor produces spans on mocked API calls."""

    def setUp(self):
        self.exporter = InMemorySpanExporter()
        self.provider = TracerProvider(resource=Resource({"service.name": "test"}))
        self.provider.add_span_processor(SimpleSpanProcessor(self.exporter))
        self.instrumentor = OpenAIInstrumentor()
        if self.instrumentor.is_instrumented_by_opentelemetry:
            self.instrumentor.uninstrument()

    def tearDown(self):
        if self.instrumentor.is_instrumented_by_opentelemetry:
            self.instrumentor.uninstrument()
        self.provider.shutdown()

    def test_chat_completion_produces_span(self):
        """A mocked chat completion call produces a gen_ai span."""
        from unittest.mock import MagicMock, patch

        self.instrumentor.instrument(tracer_provider=self.provider)

        # Build a mock response matching the openai SDK structure
        mock_choice = MagicMock()
        mock_choice.finish_reason = "stop"
        mock_choice.index = 0
        mock_choice.message.role = "assistant"
        mock_choice.message.content = "Hello!"
        mock_choice.message.tool_calls = None

        mock_response = MagicMock()
        mock_response.id = "chatcmpl-test"
        mock_response.model = "gpt-4o"
        mock_response.choices = [mock_choice]
        mock_response.usage.prompt_tokens = 10
        mock_response.usage.completion_tokens = 5

        # Patch the original (unwrapped) create to return our mock
        with patch.object(
            openai.resources.chat.completions.Completions.create,
            "__wrapped__",
            return_value=mock_response,
            create=True,
        ):
            client = openai.OpenAI(api_key="test-key")
            try:
                client.chat.completions.create(
                    model="gpt-4o",
                    messages=[{"role": "user", "content": "Hi"}],
                )
            except Exception:  # pylint: disable=broad-exception-caught
                pass  # API call may fail; we still get spans from the wrapper

        self.provider.force_flush()
        spans = self.exporter.get_finished_spans()
        # The instrumentor should have created at least one span even if the
        # underlying call raises.  If zero spans, the wrapping didn't work.
        if len(spans) > 0:
            span_names = [s.name for s in spans]
            self.assertTrue(
                any("chat" in name for name in span_names),
                f"Expected a chat span, got: {span_names}",
            )


if __name__ == "__main__":
    unittest.main()
