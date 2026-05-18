# -------------------------------------------------------------------------
# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License. See License.txt in the project root for
# license information.
# --------------------------------------------------------------------------

"""Integration tests for urllib instrumentation.

Validates that Python's built-in ``urllib.request`` produces correct HTTP
client spans when instrumented and that hook kwargs (``request_hook``,
``response_hook``, ``excluded_urls``) are honoured at runtime.
"""

import threading
import unittest
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer

from opentelemetry.instrumentation.urllib import URLLibInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from microsoft.opentelemetry._constants import _SUPPORTED_INSTRUMENTED_LIBRARIES


class _TestHandler(BaseHTTPRequestHandler):
    """Minimal HTTP handler that returns 200 for any request."""

    def do_GET(self):  # noqa: N802
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, fmt, *args):  # pylint: disable=arguments-differ
        pass


class TestUrllibInstrumentationConfig(unittest.TestCase):
    """Verify urllib is registered in the distro's supported library lists."""

    def test_urllib_in_supported_libraries(self):
        self.assertIn("urllib", _SUPPORTED_INSTRUMENTED_LIBRARIES)


class TestUrllibInstrumentorLifecycle(unittest.TestCase):
    """Verify the upstream URLLibInstrumentor can be activated and torn down."""

    def setUp(self):
        self.instrumentor = URLLibInstrumentor()
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
        # urllib instruments the stdlib — no external deps required
        self.assertIsInstance(deps, (list, tuple))


class TestUrllibSpanGeneration(unittest.TestCase):
    """Verify that urllib.request produces spans and hooks fire."""

    @classmethod
    def setUpClass(cls):
        cls.server = HTTPServer(("127.0.0.1", 0), _TestHandler)
        cls.port = cls.server.server_address[1]
        cls.server_thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.server_thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server_thread.join(timeout=5)
        cls.server.server_close()

    def setUp(self):
        self.exporter = InMemorySpanExporter()
        self.provider = TracerProvider(resource=Resource({"service.name": "test"}))
        self.provider.add_span_processor(SimpleSpanProcessor(self.exporter))
        self.instrumentor = URLLibInstrumentor()
        if self.instrumentor.is_instrumented_by_opentelemetry:
            self.instrumentor.uninstrument()

    def tearDown(self):
        if self.instrumentor.is_instrumented_by_opentelemetry:
            self.instrumentor.uninstrument()
        self.provider.shutdown()

    def test_basic_span_creation(self):
        """A urllib request produces an HTTP client span."""
        self.instrumentor.instrument(tracer_provider=self.provider)

        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/hello") as resp:
            self.assertEqual(resp.status, 200)

        self.provider.force_flush()
        spans = self.exporter.get_finished_spans()
        self.assertGreater(len(spans), 0)
        self.assertIn("GET", spans[0].name)

    def test_no_spans_after_uninstrument(self):
        """After uninstrument(), urllib requests should not produce spans."""
        self.instrumentor.instrument(tracer_provider=self.provider)
        self.instrumentor.uninstrument()

        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/hello") as resp:
            resp.read()

        self.provider.force_flush()
        spans = self.exporter.get_finished_spans()
        self.assertEqual(len(spans), 0)

    def test_request_hook_fires(self):
        """request_hook sets a custom attribute on the span."""
        hook_called = []

        def my_request_hook(span, request):
            hook_called.append(True)
            span.set_attribute("test.hook", "request")

        self.instrumentor.instrument(
            tracer_provider=self.provider,
            request_hook=my_request_hook,
        )

        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/hook") as resp:
            resp.read()

        self.provider.force_flush()
        self.assertTrue(hook_called, "request_hook was never called")
        spans = self.exporter.get_finished_spans()
        self.assertEqual(spans[0].attributes.get("test.hook"), "request")

    def test_response_hook_fires(self):
        """response_hook sets a custom attribute from the response."""
        hook_called = []

        def my_response_hook(span, request, response):
            hook_called.append(True)
            span.set_attribute("test.status", response.status)

        self.instrumentor.instrument(
            tracer_provider=self.provider,
            response_hook=my_response_hook,
        )

        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/hook") as resp:
            resp.read()

        self.provider.force_flush()
        self.assertTrue(hook_called, "response_hook was never called")
        spans = self.exporter.get_finished_spans()
        self.assertEqual(spans[0].attributes.get("test.status"), 200)

    def test_excluded_urls_suppresses_spans(self):
        """excluded_urls prevents span creation for matching paths."""
        self.instrumentor.instrument(
            tracer_provider=self.provider,
            excluded_urls=f"127.0.0.1:{self.port}/health",
        )

        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/health") as resp:
            resp.read()
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/api") as resp:
            resp.read()

        self.provider.force_flush()
        spans = self.exporter.get_finished_spans()
        self.assertEqual(len(spans), 1)
        attrs = spans[0].attributes or {}
        url = str(attrs.get("http.url", attrs.get("url.full", "")))
        self.assertIn("/api", url)


if __name__ == "__main__":
    unittest.main()
