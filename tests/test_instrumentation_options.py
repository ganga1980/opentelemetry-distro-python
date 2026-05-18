# -------------------------------------------------------------------------
# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License. See License.txt in the project root for
# license information.
# --------------------------------------------------------------------------

"""Functional validation tests for instrumentation_options kwargs forwarding.

Verifies that per-library configuration passed via ``instrumentation_options``
in ``use_microsoft_opentelemetry()`` flows through ``_setup_instrumentations``
and arrives at each instrumentor's ``instrument()`` call.  The ``enabled`` key
must be stripped; all other keys must be forwarded verbatim.

Covers:
  - Every library in ``_SUPPORTED_INSTRUMENTED_LIBRARIES`` (parametrized).
  - Upstream OTel instrumentors (web/HTTP/DB) with library-specific kwargs.
  - In-repo GenAI instrumentors (LangChain, Semantic Kernel, Agent Framework).
  - Cross-cutting: multi-lib isolation, disabled-lib short-circuit, end-to-end
    from ``use_microsoft_opentelemetry()`` down.
"""

import unittest
from unittest.mock import MagicMock, patch

from microsoft.opentelemetry._constants import _SUPPORTED_INSTRUMENTED_LIBRARIES
from microsoft.opentelemetry._distro import (
    _get_instrumentation_kwargs,
    _setup_instrumentations,
    use_microsoft_opentelemetry,
)

# ---------------------------------------------------------------------------
# Unit tests for the _get_instrumentation_kwargs helper
# ---------------------------------------------------------------------------


class TestGetInstrumentationKwargs(unittest.TestCase):
    """Tests for _get_instrumentation_kwargs helper."""

    def test_returns_empty_when_no_options(self):
        self.assertEqual(_get_instrumentation_kwargs({}, "requests"), {})

    def test_returns_empty_when_lib_not_in_options(self):
        otel_kwargs = {"instrumentation_options": {"flask": {"enabled": True}}}
        self.assertEqual(_get_instrumentation_kwargs(otel_kwargs, "requests"), {})

    def test_strips_enabled_key(self):
        otel_kwargs = {
            "instrumentation_options": {
                "requests": {"enabled": True, "excluded_urls": "health,ready"},
            }
        }
        result = _get_instrumentation_kwargs(otel_kwargs, "requests")
        self.assertEqual(result, {"excluded_urls": "health,ready"})
        self.assertNotIn("enabled", result)

    def test_returns_all_non_enabled_keys(self):
        otel_kwargs = {
            "instrumentation_options": {
                "langchain": {
                    "enabled": True,
                    "agent_id": "bot-123",
                    "agent_name": "MyBot",
                    "server_address": "api.openai.com",
                },
            }
        }
        result = _get_instrumentation_kwargs(otel_kwargs, "langchain")
        self.assertEqual(
            result,
            {
                "agent_id": "bot-123",
                "agent_name": "MyBot",
                "server_address": "api.openai.com",
            },
        )

    def test_returns_empty_when_only_enabled(self):
        otel_kwargs = {
            "instrumentation_options": {"flask": {"enabled": False}},
        }
        self.assertEqual(_get_instrumentation_kwargs(otel_kwargs, "flask"), {})

    def test_callable_values_pass_through(self):
        def hook(span, result):  # pylint: disable=unused-argument
            pass

        otel_kwargs = {
            "instrumentation_options": {
                "requests": {"request_hook": hook},
            }
        }
        result = _get_instrumentation_kwargs(otel_kwargs, "requests")
        self.assertIs(result["request_hook"], hook)


# ---------------------------------------------------------------------------
# Unit tests for kwargs forwarding through _setup_instrumentations
# ---------------------------------------------------------------------------


class TestInstrumentationKwargsForwarding(unittest.TestCase):
    """Verify _setup_instrumentations forwards per-library kwargs to instrumentor.instrument()."""

    @patch("microsoft.opentelemetry._distro._SUPPORTED_INSTRUMENTED_LIBRARIES", ("test_lib",))
    @patch("microsoft.opentelemetry._distro._is_instrumentation_enabled", return_value=True)
    @patch("microsoft.opentelemetry._distro.get_dist_dependency_conflicts", return_value=None)
    @patch("microsoft.opentelemetry._distro.entry_points")
    def test_per_lib_kwargs_forwarded(self, ep_iter_mock, _dep, _enabled):
        ep_mock = MagicMock()
        ep_mock.name = "test_lib"
        instrumentor_instance = MagicMock()
        ep_mock.load.return_value = lambda: instrumentor_instance
        ep_iter_mock.return_value = [ep_mock]

        otel_kwargs = {
            "instrumentation_options": {
                "test_lib": {
                    "enabled": True,
                    "excluded_urls": "health",
                    "request_hook": "my_hook",
                },
            }
        }
        _setup_instrumentations(otel_kwargs)
        instrumentor_instance.instrument.assert_called_once()
        call_kwargs = instrumentor_instance.instrument.call_args[1]
        self.assertEqual(call_kwargs["excluded_urls"], "health")
        self.assertEqual(call_kwargs["request_hook"], "my_hook")
        self.assertNotIn("enabled", call_kwargs)

    @patch("microsoft.opentelemetry._distro._SUPPORTED_INSTRUMENTED_LIBRARIES", ("test_lib",))
    @patch("microsoft.opentelemetry._distro._is_instrumentation_enabled", return_value=True)
    @patch("microsoft.opentelemetry._distro.get_dist_dependency_conflicts", return_value=None)
    @patch("microsoft.opentelemetry._distro.entry_points")
    def test_shared_kwargs_merged_with_per_lib(self, ep_iter_mock, _dep, _enabled):
        ep_mock = MagicMock()
        ep_mock.name = "test_lib"
        instrumentor_instance = MagicMock()
        ep_mock.load.return_value = lambda: instrumentor_instance
        ep_iter_mock.return_value = [ep_mock]

        otel_kwargs = {
            "instrumentation_options": {
                "test_lib": {"agent_id": "bot-1"},
            }
        }
        _setup_instrumentations(otel_kwargs, enable_sensitive_data=True)
        call_kwargs = instrumentor_instance.instrument.call_args[1]
        self.assertEqual(call_kwargs["agent_id"], "bot-1")
        self.assertTrue(call_kwargs["enable_sensitive_data"])

    @patch("microsoft.opentelemetry._distro._SUPPORTED_INSTRUMENTED_LIBRARIES", ("test_lib",))
    @patch("microsoft.opentelemetry._distro._is_instrumentation_enabled", return_value=True)
    @patch("microsoft.opentelemetry._distro.get_dist_dependency_conflicts", return_value=None)
    @patch("microsoft.opentelemetry._distro.entry_points")
    def test_no_extra_kwargs_when_options_empty(self, ep_iter_mock, _dep, _enabled):
        ep_mock = MagicMock()
        ep_mock.name = "test_lib"
        instrumentor_instance = MagicMock()
        ep_mock.load.return_value = lambda: instrumentor_instance
        ep_iter_mock.return_value = [ep_mock]

        _setup_instrumentations({})
        call_kwargs = instrumentor_instance.instrument.call_args[1]
        self.assertEqual(call_kwargs, {"skip_dep_check": True})

    @patch("microsoft.opentelemetry._distro._SUPPORTED_INSTRUMENTED_LIBRARIES", ("test_lib",))
    @patch("microsoft.opentelemetry._distro._is_instrumentation_enabled", return_value=True)
    @patch("microsoft.opentelemetry._distro.get_dist_dependency_conflicts", return_value=None)
    @patch("microsoft.opentelemetry._distro.entry_points")
    def test_per_lib_kwargs_override_shared_kwargs(self, ep_iter_mock, _dep, _enabled):
        ep_mock = MagicMock()
        ep_mock.name = "test_lib"
        instrumentor_instance = MagicMock()
        ep_mock.load.return_value = lambda: instrumentor_instance
        ep_iter_mock.return_value = [ep_mock]

        otel_kwargs = {
            "instrumentation_options": {
                "test_lib": {"enable_sensitive_data": True},
            }
        }
        _setup_instrumentations(otel_kwargs, enable_sensitive_data=False)
        call_kwargs = instrumentor_instance.instrument.call_args[1]
        self.assertTrue(call_kwargs["enable_sensitive_data"])


# ---------------------------------------------------------------------------
# Functional validation: per-library kwargs forwarding for every supported lib
# ---------------------------------------------------------------------------


class TestInstrumentationOptionsFunctionalValidation(unittest.TestCase):
    """Functional validation: instrumentation_options kwargs flow through for every supported library.

    For each library in _SUPPORTED_INSTRUMENTED_LIBRARIES, we simulate the
    full _setup_instrumentations path and verify that per-library kwargs from
    instrumentation_options are forwarded to the instrumentor's instrument()
    call while the 'enabled' key is stripped.
    """

    def _run_with_options(self, lib_name, lib_options, shared_kwargs=None):
        """Helper: run _setup_instrumentations for a single library and return the kwargs it received."""
        instrumentor_instance = MagicMock()

        ep_mock = MagicMock()
        ep_mock.name = lib_name
        ep_mock.load.return_value = lambda: instrumentor_instance

        with (
            patch("microsoft.opentelemetry._distro._SUPPORTED_INSTRUMENTED_LIBRARIES", (lib_name,)),
            patch("microsoft.opentelemetry._distro._is_instrumentation_enabled", return_value=True),
            patch("microsoft.opentelemetry._distro.get_dist_dependency_conflicts", return_value=None),
            patch("microsoft.opentelemetry._distro.entry_points", return_value=[ep_mock]),
        ):
            otel_kwargs = {"instrumentation_options": {lib_name: lib_options}}
            _setup_instrumentations(otel_kwargs, **(shared_kwargs or {}))

        instrumentor_instance.instrument.assert_called_once()
        return instrumentor_instance.instrument.call_args[1]

    # -- Upstream OTel instrumentors (web/HTTP/DB) --

    def test_django_forwards_kwargs(self):
        call_kwargs = self._run_with_options(
            "django",
            {
                "enabled": True,
                "is_sql_commentor_enabled": True,
                "request_hook": "my_hook",
                "response_hook": "my_response_hook",
            },
        )
        self.assertTrue(call_kwargs["is_sql_commentor_enabled"])
        self.assertEqual(call_kwargs["request_hook"], "my_hook")
        self.assertEqual(call_kwargs["response_hook"], "my_response_hook")
        self.assertNotIn("enabled", call_kwargs)

    def test_fastapi_forwards_kwargs(self):
        call_kwargs = self._run_with_options(
            "fastapi",
            {
                "enabled": True,
                "excluded_urls": "health,ready",
                "server_request_hook": "hook_fn",
            },
        )
        self.assertEqual(call_kwargs["excluded_urls"], "health,ready")
        self.assertEqual(call_kwargs["server_request_hook"], "hook_fn")
        self.assertNotIn("enabled", call_kwargs)

    def test_flask_forwards_kwargs(self):
        call_kwargs = self._run_with_options(
            "flask",
            {
                "enabled": True,
                "excluded_urls": "/ping",
                "request_hook": "hook",
            },
        )
        self.assertEqual(call_kwargs["excluded_urls"], "/ping")
        self.assertNotIn("enabled", call_kwargs)

    def test_httpx_forwards_kwargs(self):
        call_kwargs = self._run_with_options(
            "httpx",
            {
                "request_hook": "req_hook",
                "response_hook": "resp_hook",
            },
        )
        self.assertEqual(call_kwargs["request_hook"], "req_hook")
        self.assertEqual(call_kwargs["response_hook"], "resp_hook")

    def test_psycopg2_forwards_kwargs(self):
        call_kwargs = self._run_with_options(
            "psycopg2",
            {
                "enable_commenter": True,
            },
        )
        self.assertTrue(call_kwargs["enable_commenter"])

    def test_requests_forwards_kwargs(self):
        def hook_fn(span, response):  # pylint: disable=unused-argument
            pass

        call_kwargs = self._run_with_options(
            "requests",
            {
                "excluded_urls": "health",
                "request_hook": hook_fn,
                "response_hook": "resp_hook",
            },
        )
        self.assertEqual(call_kwargs["excluded_urls"], "health")
        self.assertIs(call_kwargs["request_hook"], hook_fn)

    def test_urllib_forwards_kwargs(self):
        call_kwargs = self._run_with_options(
            "urllib",
            {
                "excluded_urls": "/internal",
            },
        )
        self.assertEqual(call_kwargs["excluded_urls"], "/internal")

    def test_urllib3_forwards_kwargs(self):
        call_kwargs = self._run_with_options(
            "urllib3",
            {
                "excluded_urls": "health",
                "request_hook": "hook",
                "response_hook": "hook",
                "url_filter": "filter_fn",
            },
        )
        self.assertEqual(call_kwargs["excluded_urls"], "health")
        self.assertEqual(call_kwargs["url_filter"], "filter_fn")

    # -- Upstream OTel instrumentors (GenAI) --

    def test_openai_forwards_kwargs(self):
        call_kwargs = self._run_with_options(
            "openai",
            {
                "enabled": True,
                "completion_hook": "my_completion_hook",
            },
        )
        self.assertEqual(call_kwargs["completion_hook"], "my_completion_hook")
        self.assertNotIn("enabled", call_kwargs)

    def test_openai_agents_forwards_kwargs(self):
        call_kwargs = self._run_with_options(
            "openai_agents",
            {
                "enabled": True,
                "custom_param": "value",
            },
        )
        self.assertEqual(call_kwargs["custom_param"], "value")
        self.assertNotIn("enabled", call_kwargs)

    # -- In-repo instrumentors --

    def test_langchain_forwards_agent_kwargs(self):
        call_kwargs = self._run_with_options(
            "langchain",
            {
                "enabled": True,
                "agent_id": "bot-123",
                "agent_name": "MyBot",
                "agent_description": "A test bot",
                "agent_version": "1.0",
                "server_address": "api.openai.com",
                "server_port": 443,
                "separate_trace_from_runtime_context": True,
            },
        )
        self.assertEqual(call_kwargs["agent_id"], "bot-123")
        self.assertEqual(call_kwargs["agent_name"], "MyBot")
        self.assertEqual(call_kwargs["agent_description"], "A test bot")
        self.assertEqual(call_kwargs["agent_version"], "1.0")
        self.assertEqual(call_kwargs["server_address"], "api.openai.com")
        self.assertEqual(call_kwargs["server_port"], 443)
        self.assertTrue(call_kwargs["separate_trace_from_runtime_context"])
        self.assertNotIn("enabled", call_kwargs)

    def test_semantic_kernel_forwards_kwargs(self):
        call_kwargs = self._run_with_options(
            "semantic_kernel",
            {
                "enabled": True,
            },
        )
        self.assertNotIn("enabled", call_kwargs)

    def test_agent_framework_forwards_kwargs(self):
        call_kwargs = self._run_with_options(
            "agent_framework",
            {
                "enabled": True,
            },
            shared_kwargs={"enable_sensitive_data": True},
        )
        # enable_sensitive_data comes from shared kwargs, not per-lib
        self.assertTrue(call_kwargs["enable_sensitive_data"])
        self.assertNotIn("enabled", call_kwargs)

    # -- Cross-cutting concerns --

    def test_all_supported_libs_receive_kwargs(self):
        """Every library in _SUPPORTED_INSTRUMENTED_LIBRARIES gets its kwargs forwarded."""
        for lib_name in _SUPPORTED_INSTRUMENTED_LIBRARIES:
            with self.subTest(lib=lib_name):
                sentinel = object()
                call_kwargs = self._run_with_options(
                    lib_name,
                    {
                        "enabled": True,
                        "test_sentinel": sentinel,
                    },
                )
                self.assertIs(
                    call_kwargs["test_sentinel"],
                    sentinel,
                    f"{lib_name}: test_sentinel not forwarded",
                )
                self.assertNotIn(
                    "enabled",
                    call_kwargs,
                    f"{lib_name}: 'enabled' should be stripped",
                )

    def test_multiple_libs_each_get_own_kwargs(self):
        """When instrumentation_options has entries for multiple libs, each gets only its own."""
        lib_a_instance = MagicMock()
        lib_b_instance = MagicMock()

        ep_a = MagicMock()
        ep_a.name = "lib_a"
        ep_a.load.return_value = lambda: lib_a_instance

        ep_b = MagicMock()
        ep_b.name = "lib_b"
        ep_b.load.return_value = lambda: lib_b_instance

        with (
            patch("microsoft.opentelemetry._distro._SUPPORTED_INSTRUMENTED_LIBRARIES", ("lib_a", "lib_b")),
            patch("microsoft.opentelemetry._distro._is_instrumentation_enabled", return_value=True),
            patch("microsoft.opentelemetry._distro.get_dist_dependency_conflicts", return_value=None),
            patch("microsoft.opentelemetry._distro.entry_points", return_value=[ep_a, ep_b]),
        ):
            otel_kwargs = {
                "instrumentation_options": {
                    "lib_a": {"excluded_urls": "/a", "enabled": True},
                    "lib_b": {"request_hook": "b_hook"},
                },
            }
            _setup_instrumentations(otel_kwargs)

        a_kwargs = lib_a_instance.instrument.call_args[1]
        b_kwargs = lib_b_instance.instrument.call_args[1]
        self.assertEqual(a_kwargs["excluded_urls"], "/a")
        self.assertNotIn("request_hook", a_kwargs)
        self.assertEqual(b_kwargs["request_hook"], "b_hook")
        self.assertNotIn("excluded_urls", b_kwargs)

    def test_kwargs_not_forwarded_to_disabled_lib(self):
        """A disabled library should not have instrument() called at all."""
        instrumentor_instance = MagicMock()
        ep_mock = MagicMock()
        ep_mock.name = "requests"
        ep_mock.load.return_value = lambda: instrumentor_instance

        with (
            patch("microsoft.opentelemetry._distro._SUPPORTED_INSTRUMENTED_LIBRARIES", ("requests",)),
            patch("microsoft.opentelemetry._distro.entry_points", return_value=[ep_mock]),
        ):
            otel_kwargs = {
                "instrumentation_options": {
                    "requests": {"enabled": False, "excluded_urls": "health"},
                }
            }
            _setup_instrumentations(otel_kwargs)

        instrumentor_instance.instrument.assert_not_called()

    @patch("microsoft.opentelemetry._distro._setup_instrumentations")
    @patch("microsoft.opentelemetry._distro._setup_logging")
    @patch("microsoft.opentelemetry._distro._setup_metrics")
    @patch("microsoft.opentelemetry._distro._setup_tracing")
    def test_end_to_end_options_reach_setup_instrumentations(self, _trc, _met, _log, setup_inst):
        """instrumentation_options passed to use_microsoft_opentelemetry() arrive in otel_kwargs."""
        opts = {
            "langchain": {"agent_id": "bot-1", "agent_name": "TestBot"},
            "requests": {"excluded_urls": "health", "enabled": True},
        }
        use_microsoft_opentelemetry(instrumentation_options=opts)
        otel_kwargs = setup_inst.call_args[0][0]
        actual_opts = otel_kwargs.get("instrumentation_options", {})
        self.assertEqual(actual_opts["langchain"]["agent_id"], "bot-1")
        self.assertEqual(actual_opts["requests"]["excluded_urls"], "health")
