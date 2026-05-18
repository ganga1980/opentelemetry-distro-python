# -------------------------------------------------------------------------
# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License. See License.txt in the project root for
# license information.
# --------------------------------------------------------------------------

"""Integration tests for requests instrumentation.

Validates that the ``requests`` library produces correct HTTP client spans
when instrumented and that hook kwargs (``request_hook``, ``response_hook``,
``excluded_urls``) are honoured at runtime.
"""

import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

requests = pytest.importorskip("requests")

# pylint: disable=wrong-import-position
from opentelemetry.instrumentation.requests import RequestsInstrumentor  # noqa: E402
from opentelemetry.sdk.resources import Resource  # noqa: E402
from opentelemetry.sdk.trace import TracerProvider  # noqa: E402
from opentelemetry.sdk.trace.export import SimpleSpanProcessor  # noqa: E402
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter  # noqa: E402

from microsoft.opentelemetry._constants import (  # noqa: E402
    _SUPPORTED_INSTRUMENTED_LIBRARIES,
)

# pylint: enable=wrong-import-position


class _TestHandler(BaseHTTPRequestHandler):
    """Minimal HTTP handler that returns 200 for any request."""

    def do_GET(self):  # noqa: N802
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, fmt, *args):  # pylint: disable=arguments-differ
        pass


class TestRequestsInstrumentationConfig(unittest.TestCase):
    """Verify requests is registered in the distro's supported library lists."""

    def test_requests_in_supported_libraries(self):
        self.assertIn("requests", _SUPPORTED_INSTRUMENTED_LIBRARIES)


class TestRequestsInstrumentorLifecycle(unittest.TestCase):
    """Verify the upstream RequestsInstrumentor can be activated and torn down."""

    def setUp(self):
        self.instrumentor = RequestsInstrumentor()
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
        self.assertIn("requests", dep_str)


class TestRequestsSpanGeneration(unittest.TestCase):
    """Verify that requests produce spans, hooks fire, and excluded_urls work."""

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
        self.instrumentor = RequestsInstrumentor()
        if self.instrumentor.is_instrumented_by_opentelemetry:
            self.instrumentor.uninstrument()

    def tearDown(self):
        if self.instrumentor.is_instrumented_by_opentelemetry:
            self.instrumentor.uninstrument()
        self.provider.shutdown()

    def test_basic_span_creation(self):
        """A GET request produces an HTTP client span."""
        self.instrumentor.instrument(tracer_provider=self.provider)

        resp = requests.get(f"http://127.0.0.1:{self.port}/hello")
        self.assertEqual(resp.status_code, 200)

        self.provider.force_flush()
        spans = self.exporter.get_finished_spans()
        self.assertGreater(len(spans), 0)
        self.assertIn("GET", spans[0].name)

    def test_no_spans_after_uninstrument(self):
        """After uninstrument(), requests should not produce spans."""
        self.instrumentor.instrument(tracer_provider=self.provider)
        self.instrumentor.uninstrument()

        requests.get(f"http://127.0.0.1:{self.port}/hello")

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

        requests.get(f"http://127.0.0.1:{self.port}/hook")

        self.provider.force_flush()
        self.assertTrue(hook_called, "request_hook was never called")
        spans = self.exporter.get_finished_spans()
        self.assertEqual(spans[0].attributes.get("test.hook"), "request")

    def test_response_hook_fires(self):
        """response_hook sets a custom attribute from the response."""
        hook_called = []

        def my_response_hook(span, request, response):
            hook_called.append(True)
            span.set_attribute("test.status", response.status_code)

        self.instrumentor.instrument(
            tracer_provider=self.provider,
            response_hook=my_response_hook,
        )

        requests.get(f"http://127.0.0.1:{self.port}/hook")

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

        requests.get(f"http://127.0.0.1:{self.port}/health")
        requests.get(f"http://127.0.0.1:{self.port}/api")

        self.provider.force_flush()
        spans = self.exporter.get_finished_spans()
        self.assertEqual(len(spans), 1)
        attrs = spans[0].attributes or {}
        url = str(attrs.get("http.url", attrs.get("url.full", "")))
        self.assertIn("/api", url)


if __name__ == "__main__":
    unittest.main()
