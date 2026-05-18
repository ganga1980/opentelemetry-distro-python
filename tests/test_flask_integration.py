# -------------------------------------------------------------------------
# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License. See License.txt in the project root for
# license information.
# --------------------------------------------------------------------------

"""Integration tests for Flask instrumentation.

Validates that Flask produces correct server spans when instrumented and
that hook kwargs (``request_hook``, ``response_hook``, ``excluded_urls``)
are honoured at runtime.
"""

import unittest

import pytest

flask = pytest.importorskip("flask")

# pylint: disable=wrong-import-position
from opentelemetry.instrumentation.flask import FlaskInstrumentor  # noqa: E402
from opentelemetry.sdk.resources import Resource  # noqa: E402
from opentelemetry.sdk.trace import TracerProvider  # noqa: E402
from opentelemetry.sdk.trace.export import SimpleSpanProcessor  # noqa: E402
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter  # noqa: E402

from microsoft.opentelemetry._constants import (  # noqa: E402
    _SUPPORTED_INSTRUMENTED_LIBRARIES,
)

# pylint: enable=wrong-import-position


class TestFlaskInstrumentationConfig(unittest.TestCase):
    """Verify flask is registered in the distro's supported library lists."""

    def test_flask_in_supported_libraries(self):
        self.assertIn("flask", _SUPPORTED_INSTRUMENTED_LIBRARIES)


class TestFlaskInstrumentorLifecycle(unittest.TestCase):
    """Verify the upstream FlaskInstrumentor can be activated and torn down."""

    def setUp(self):
        self.instrumentor = FlaskInstrumentor()
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
        self.assertIn("flask", dep_str.lower())


def _make_flask_app():
    app = flask.Flask(__name__)

    @app.route("/hello")
    def hello():
        return "ok"

    @app.route("/health")
    def health():
        return "healthy"

    return app


class TestFlaskSpanGeneration(unittest.TestCase):
    """Verify that Flask requests produce spans, hooks fire, and excluded_urls work."""

    def setUp(self):
        self.exporter = InMemorySpanExporter()
        self.provider = TracerProvider(resource=Resource({"service.name": "test"}))
        self.provider.add_span_processor(SimpleSpanProcessor(self.exporter))
        self.instrumentor = FlaskInstrumentor()
        if self.instrumentor.is_instrumented_by_opentelemetry:
            self.instrumentor.uninstrument()

    def tearDown(self):
        if self.instrumentor.is_instrumented_by_opentelemetry:
            self.instrumentor.uninstrument()
        self.provider.shutdown()

    def test_basic_span_creation(self):
        """A Flask request produces a server span."""
        self.instrumentor.instrument(tracer_provider=self.provider)
        app = _make_flask_app()

        with app.test_client() as client:
            resp = client.get("/hello")
        self.assertEqual(resp.status_code, 200)

        self.provider.force_flush()
        spans = self.exporter.get_finished_spans()
        self.assertGreater(len(spans), 0)
        self.assertIn("GET /hello", spans[0].name)

    def test_no_spans_after_uninstrument(self):
        """After uninstrument(), Flask requests should not produce spans."""
        self.instrumentor.instrument(tracer_provider=self.provider)
        self.instrumentor.uninstrument()
        app = _make_flask_app()

        with app.test_client() as client:
            client.get("/hello")

        self.provider.force_flush()
        spans = self.exporter.get_finished_spans()
        self.assertEqual(len(spans), 0)

    def test_request_hook_fires(self):
        """request_hook receives the span and environ."""
        hook_called = []

        def my_request_hook(span, environ):
            hook_called.append(True)
            span.set_attribute("test.hook", "flask-request")

        self.instrumentor.instrument(
            tracer_provider=self.provider,
            request_hook=my_request_hook,
        )
        app = _make_flask_app()

        with app.test_client() as client:
            client.get("/hello")

        self.provider.force_flush()
        self.assertTrue(hook_called, "request_hook was never called")
        spans = self.exporter.get_finished_spans()
        self.assertEqual(spans[0].attributes.get("test.hook"), "flask-request")

    def test_response_hook_fires(self):
        """response_hook receives the span, status and headers."""
        hook_called = []

        def my_response_hook(span, status, response_headers):
            hook_called.append(True)
            span.set_attribute("test.status_line", status)

        self.instrumentor.instrument(
            tracer_provider=self.provider,
            response_hook=my_response_hook,
        )
        app = _make_flask_app()

        with app.test_client() as client:
            client.get("/hello")

        self.provider.force_flush()
        self.assertTrue(hook_called, "response_hook was never called")
        spans = self.exporter.get_finished_spans()
        status_line = spans[0].attributes.get("test.status_line")
        self.assertIn("200", status_line)

    def test_excluded_urls_suppresses_spans(self):
        """excluded_urls prevents span creation for matching paths."""
        self.instrumentor.instrument(
            tracer_provider=self.provider,
            excluded_urls="/health",
        )
        app = _make_flask_app()

        with app.test_client() as client:
            client.get("/health")
            client.get("/hello")

        self.provider.force_flush()
        spans = self.exporter.get_finished_spans()
        self.assertEqual(len(spans), 1)
        self.assertIn("/hello", spans[0].name)


if __name__ == "__main__":
    unittest.main()
