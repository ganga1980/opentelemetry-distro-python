# -------------------------------------------------------------------------
# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License. See License.txt in the project root for
# license information.
# --------------------------------------------------------------------------

"""Integration tests for urllib3 instrumentation.

Validates that ``urllib3`` produces correct HTTP client spans when instrumented
and that hook kwargs (``request_hook``, ``response_hook``) are honoured at
runtime.
"""

import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

import urllib3
from opentelemetry.instrumentation.urllib3 import URLLib3Instrumentor
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


class TestUrllib3InstrumentationConfig(unittest.TestCase):
    """Verify urllib3 is registered in the distro's supported library lists."""

    def test_urllib3_in_supported_libraries(self):
        self.assertIn("urllib3", _SUPPORTED_INSTRUMENTED_LIBRARIES)


class TestUrllib3InstrumentorLifecycle(unittest.TestCase):
    """Verify the upstream URLLib3Instrumentor can be activated and torn down."""

    def setUp(self):
        self.instrumentor = URLLib3Instrumentor()
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
        self.assertIn("urllib3", dep_str)


class TestUrllib3SpanGeneration(unittest.TestCase):
    """Verify that urllib3 requests produce spans and hooks fire."""

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
        self.instrumentor = URLLib3Instrumentor()
        if self.instrumentor.is_instrumented_by_opentelemetry:
            self.instrumentor.uninstrument()

    def tearDown(self):
        if self.instrumentor.is_instrumented_by_opentelemetry:
            self.instrumentor.uninstrument()
        self.provider.shutdown()

    def test_basic_span_creation(self):
        """A urllib3 request produces an HTTP client span."""
        self.instrumentor.instrument(tracer_provider=self.provider)

        http = urllib3.PoolManager()
        resp = http.request("GET", f"http://127.0.0.1:{self.port}/hello")
        self.assertEqual(resp.status, 200)

        self.provider.force_flush()
        spans = self.exporter.get_finished_spans()
        self.assertGreater(len(spans), 0)
        self.assertIn("GET", spans[0].name)

    def test_no_spans_after_uninstrument(self):
        """After uninstrument(), urllib3 requests should not produce spans."""
        self.instrumentor.instrument(tracer_provider=self.provider)
        self.instrumentor.uninstrument()

        http = urllib3.PoolManager()
        http.request("GET", f"http://127.0.0.1:{self.port}/hello")

        self.provider.force_flush()
        spans = self.exporter.get_finished_spans()
        self.assertEqual(len(spans), 0)

    def test_request_hook_fires(self):
        """request_hook sets a custom attribute on the span."""
        hook_called = []

        def my_request_hook(span, pool, request_info):
            hook_called.append(True)
            span.set_attribute("test.hook", "request")

        self.instrumentor.instrument(
            tracer_provider=self.provider,
            request_hook=my_request_hook,
        )

        http = urllib3.PoolManager()
        http.request("GET", f"http://127.0.0.1:{self.port}/hook")

        self.provider.force_flush()
        self.assertTrue(hook_called, "request_hook was never called")
        spans = self.exporter.get_finished_spans()
        self.assertEqual(spans[0].attributes.get("test.hook"), "request")

    def test_response_hook_fires(self):
        """response_hook sets a custom attribute from the response."""
        hook_called = []

        def my_response_hook(span, pool, response):
            hook_called.append(True)
            span.set_attribute("test.status", response.status)

        self.instrumentor.instrument(
            tracer_provider=self.provider,
            response_hook=my_response_hook,
        )

        http = urllib3.PoolManager()
        http.request("GET", f"http://127.0.0.1:{self.port}/hook")

        self.provider.force_flush()
        self.assertTrue(hook_called, "response_hook was never called")
        spans = self.exporter.get_finished_spans()
        self.assertEqual(spans[0].attributes.get("test.status"), 200)


if __name__ == "__main__":
    unittest.main()
