# -------------------------------------------------------------------------
# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License. See License.txt in the project root for
# license information.
# --------------------------------------------------------------------------

"""Tests for httpx instrumentation configuration and direct instrumentor behavior.

Validates that httpx is listed in the distro's supported configuration and
that requests made with httpx produce spans when HTTPXClientInstrumentor is
enabled directly in the tests.
"""

import threading
import unittest
from http.server import HTTPServer, BaseHTTPRequestHandler

import pytest

httpx = pytest.importorskip("httpx")

# pylint: disable=wrong-import-position
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor  # noqa: E402
from opentelemetry.sdk.trace import TracerProvider  # noqa: E402
from opentelemetry.sdk.trace.export import SimpleSpanProcessor  # noqa: E402
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter  # noqa: E402
from opentelemetry.sdk.resources import Resource  # noqa: E402

from microsoft.opentelemetry._constants import (  # noqa: E402
    _A365_DISABLED_INSTRUMENTATIONS,
    _SUPPORTED_INSTRUMENTED_LIBRARIES,
)

# pylint: enable=wrong-import-position


class _TestHandler(BaseHTTPRequestHandler):
    """Minimal HTTP handler that returns 200 for any GET request."""

    def do_GET(self):  # noqa: N802
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, fmt, *args):  # pylint: disable=arguments-differ
        pass  # suppress stderr output during tests


class TestHttpxInstrumentationConfig(unittest.TestCase):
    """Verify httpx is registered in the distro's supported library lists."""

    def test_httpx_in_supported_libraries(self):
        self.assertIn("httpx", _SUPPORTED_INSTRUMENTED_LIBRARIES)

    def test_httpx_in_a365_disabled_list(self):
        self.assertIn("httpx", _A365_DISABLED_INSTRUMENTATIONS)


class TestHttpxInstrumentorLifecycle(unittest.TestCase):
    """Verify the upstream HTTPXClientInstrumentor can be activated and torn down."""

    def setUp(self):
        self.instrumentor = HTTPXClientInstrumentor()
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
        self.assertIn("httpx", dep_str)


class TestHttpxSpanGeneration(unittest.TestCase):
    """Verify that httpx requests produce spans when instrumented."""

    @classmethod
    def setUpClass(cls):
        cls.server = HTTPServer(("127.0.0.1", 0), _TestHandler)
        cls.port = cls.server.server_address[1]
        cls.server_thread = threading.Thread(target=cls.server.serve_forever)
        cls.server_thread.daemon = True
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

        self.instrumentor = HTTPXClientInstrumentor()
        if self.instrumentor.is_instrumented_by_opentelemetry:
            self.instrumentor.uninstrument()
        self.instrumentor.instrument(tracer_provider=self.provider)

    def tearDown(self):
        if self.instrumentor.is_instrumented_by_opentelemetry:
            self.instrumentor.uninstrument()
        self.provider.shutdown()

    def test_sync_request_creates_span(self):
        """A synchronous httpx request should produce an HTTP span."""
        client = httpx.Client()
        try:
            response = client.get(f"http://127.0.0.1:{self.port}/status")
            self.assertEqual(response.status_code, 200)
        finally:
            client.close()

        self.provider.force_flush()
        spans = self.exporter.get_finished_spans()
        self.assertGreater(len(spans), 0, "Expected at least one span from httpx request")

        span = spans[0]
        self.assertIn("GET", span.name)

    def test_no_spans_after_uninstrument(self):
        """After uninstrument(), httpx requests should not produce spans."""
        self.instrumentor.uninstrument()

        client = httpx.Client()
        try:
            client.get(f"http://127.0.0.1:{self.port}/status")
        finally:
            client.close()

        self.provider.force_flush()
        spans = self.exporter.get_finished_spans()
        self.assertEqual(len(spans), 0, "Expected no spans after uninstrument()")


class TestHttpxHookForwarding(unittest.TestCase):
    """Verify request_hook / response_hook kwargs are honoured at runtime."""

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
        self.instrumentor = HTTPXClientInstrumentor()
        if self.instrumentor.is_instrumented_by_opentelemetry:
            self.instrumentor.uninstrument()

    def tearDown(self):
        if self.instrumentor.is_instrumented_by_opentelemetry:
            self.instrumentor.uninstrument()
        self.provider.shutdown()

    def test_request_hook_fires(self):
        """request_hook receives the span and can set custom attributes."""
        hook_called = []

        def my_request_hook(span, request_info):
            hook_called.append(True)
            span.set_attribute("test.hook", "request")

        self.instrumentor.instrument(
            tracer_provider=self.provider,
            request_hook=my_request_hook,
        )

        with httpx.Client() as client:
            client.get(f"http://127.0.0.1:{self.port}/status")

        self.provider.force_flush()
        self.assertTrue(hook_called, "request_hook was never called")
        spans = self.exporter.get_finished_spans()
        self.assertEqual(spans[0].attributes.get("test.hook"), "request")

    def test_response_hook_fires(self):
        """response_hook receives span + response and can set custom attributes."""
        hook_called = []

        def my_response_hook(span, request_info, response_info):
            hook_called.append(True)
            span.set_attribute("test.status", response_info.status_code)

        self.instrumentor.instrument(
            tracer_provider=self.provider,
            response_hook=my_response_hook,
        )

        with httpx.Client() as client:
            client.get(f"http://127.0.0.1:{self.port}/status")

        self.provider.force_flush()
        self.assertTrue(hook_called, "response_hook was never called")
        spans = self.exporter.get_finished_spans()
        self.assertEqual(spans[0].attributes.get("test.status"), 200)


if __name__ == "__main__":
    unittest.main()
