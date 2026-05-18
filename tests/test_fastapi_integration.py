# -------------------------------------------------------------------------
# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License. See License.txt in the project root for
# license information.
# --------------------------------------------------------------------------

"""Integration tests for FastAPI instrumentation.

Validates that FastAPI produces correct server spans when instrumented and
that hook kwargs (``server_request_hook``, ``client_response_hook``,
``excluded_urls``) are honoured at runtime.
"""

import unittest

import pytest

fastapi = pytest.importorskip("fastapi")
starlette_testclient = pytest.importorskip("starlette.testclient")

# pylint: disable=wrong-import-position
from fastapi import FastAPI  # noqa: E402
from starlette.testclient import TestClient  # noqa: E402

from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor  # noqa: E402
from opentelemetry.sdk.resources import Resource  # noqa: E402
from opentelemetry.sdk.trace import TracerProvider  # noqa: E402
from opentelemetry.sdk.trace.export import SimpleSpanProcessor  # noqa: E402
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter  # noqa: E402

from microsoft.opentelemetry._constants import (  # noqa: E402
    _SUPPORTED_INSTRUMENTED_LIBRARIES,
)

# pylint: enable=wrong-import-position


def _make_fastapi_app():
    app = FastAPI()

    @app.get("/hello")
    async def hello():
        return {"message": "ok"}

    @app.get("/health")
    async def health():
        return {"status": "healthy"}

    return app


class TestFastAPIInstrumentationConfig(unittest.TestCase):
    """Verify fastapi is registered in the distro's supported library lists."""

    def test_fastapi_in_supported_libraries(self):
        self.assertIn("fastapi", _SUPPORTED_INSTRUMENTED_LIBRARIES)


class TestFastAPIInstrumentorLifecycle(unittest.TestCase):
    """Verify the upstream FastAPIInstrumentor can be activated and torn down."""

    def setUp(self):
        self.instrumentor = FastAPIInstrumentor()
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
        self.assertIn("fastapi", dep_str)


class TestFastAPISpanGeneration(unittest.TestCase):
    """Verify that FastAPI requests produce spans and hooks fire."""

    def setUp(self):
        self.exporter = InMemorySpanExporter()
        self.provider = TracerProvider(resource=Resource({"service.name": "test"}))
        self.provider.add_span_processor(SimpleSpanProcessor(self.exporter))
        self.instrumentor = FastAPIInstrumentor()
        if self.instrumentor.is_instrumented_by_opentelemetry:
            self.instrumentor.uninstrument()

    def tearDown(self):
        if self.instrumentor.is_instrumented_by_opentelemetry:
            self.instrumentor.uninstrument()
        self.provider.shutdown()

    def test_basic_span_creation(self):
        """A FastAPI request produces a server span."""
        app = _make_fastapi_app()
        FastAPIInstrumentor.instrument_app(app, tracer_provider=self.provider)

        with TestClient(app) as client:
            resp = client.get("/hello")
        self.assertEqual(resp.status_code, 200)

        self.provider.force_flush()
        spans = self.exporter.get_finished_spans()
        self.assertGreater(len(spans), 0)
        # FastAPI generates server + send/receive spans; find the server span
        server_spans = [s for s in spans if "GET /hello" in s.name]
        self.assertGreater(len(server_spans), 0)
        FastAPIInstrumentor.uninstrument_app(app)

    def test_server_request_hook_fires(self):
        """server_request_hook receives the span and ASGI scope."""
        hook_called = []

        def my_server_request_hook(span, scope):
            hook_called.append(True)
            span.set_attribute("test.hook", "server-request")

        app = _make_fastapi_app()
        FastAPIInstrumentor.instrument_app(
            app,
            tracer_provider=self.provider,
            server_request_hook=my_server_request_hook,
        )

        with TestClient(app) as client:
            client.get("/hello")

        self.provider.force_flush()
        self.assertTrue(hook_called, "server_request_hook was never called")
        spans = self.exporter.get_finished_spans()
        server_spans = [s for s in spans if s.attributes and s.attributes.get("test.hook") == "server-request"]
        self.assertGreater(len(server_spans), 0)
        FastAPIInstrumentor.uninstrument_app(app)

    def test_excluded_urls_suppresses_spans(self):
        """excluded_urls prevents span creation for matching paths."""
        app = _make_fastapi_app()
        FastAPIInstrumentor.instrument_app(
            app,
            tracer_provider=self.provider,
            excluded_urls="/health",
        )

        with TestClient(app) as client:
            client.get("/health")
            client.get("/hello")

        self.provider.force_flush()
        spans = self.exporter.get_finished_spans()
        span_names = [s.name for s in spans]
        self.assertTrue(
            any("/hello" in name for name in span_names),
            f"Expected /hello span, got: {span_names}",
        )
        self.assertFalse(
            any("/health" in name for name in span_names),
            f"Did not expect /health span, got: {span_names}",
        )
        FastAPIInstrumentor.uninstrument_app(app)


if __name__ == "__main__":
    unittest.main()
