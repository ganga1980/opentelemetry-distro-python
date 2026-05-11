# -------------------------------------------------------------------------
# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License. See License.txt in the project root for
# license information.
# --------------------------------------------------------------------------

"""Tests for use_microsoft_opentelemetry() -- OTel provider init + Azure Monitor.

Validates that the microsoft distro wrapper:
  1. Initialises OTel global providers (tracing, metrics, logging).
  2. Optionally delegates Azure Monitor exporter setup.
"""

import os
import unittest
from unittest.mock import MagicMock, patch

from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.metrics import MeterProvider

from microsoft.opentelemetry._constants import (
    _A365_DISABLED_INSTRUMENTATIONS,
    _SUPPORTED_INSTRUMENTED_LIBRARIES,
)
from microsoft.opentelemetry._distro import (
    use_microsoft_opentelemetry,
    _append_a365_components,
    _append_spectra_components,
    _is_instrumentation_enabled,
    _setup_tracing,
    _setup_metrics,
    _setup_logging,
)

TEST_RESOURCE = Resource({"service.name": "test-service"})
TEST_CONNECTION_STRING = "InstrumentationKey=test-key;IngestionEndpoint=https://test.in.ai.azure.com/"


class TestUseMicrosoftOpenTelemetry(unittest.TestCase):
    """Tests for use_microsoft_opentelemetry() orchestration."""

    @patch("microsoft.opentelemetry._distro._setup_logging")
    @patch("microsoft.opentelemetry._distro._setup_metrics")
    @patch("microsoft.opentelemetry._distro._setup_tracing")
    @patch("microsoft.opentelemetry._distro._append_azure_monitor_components", return_value=(None, None, None))
    def test_azure_monitor_disabled_by_default(self, append_mock, tracing_mock, metrics_mock, logging_mock):
        """Azure Monitor is disabled by default; components are not collected."""
        use_microsoft_opentelemetry()
        append_mock.assert_not_called()

    @patch("microsoft.opentelemetry._distro._append_azure_monitor_components", return_value=(None, None, None))
    def test_connection_string_remapped(self, append_mock):
        """azure_monitor_connection_string is remapped to connection_string."""
        use_microsoft_opentelemetry(
            azure_monitor_connection_string=TEST_CONNECTION_STRING,
            enable_azure_monitor=True,
        )
        _, azure_kwargs = append_mock.call_args[0]
        self.assertEqual(azure_kwargs["connection_string"], TEST_CONNECTION_STRING)

    @patch("microsoft.opentelemetry._distro._append_azure_monitor_components", return_value=(None, None, None))
    def test_azure_monitor_kwargs_remapped(self, append_mock):
        """azure_monitor_ prefixed kwargs are remapped to internal names."""
        use_microsoft_opentelemetry(
            enable_azure_monitor=True,
            azure_monitor_connection_string=TEST_CONNECTION_STRING,
            azure_monitor_exporter_credential="test_cred",
            azure_monitor_enable_live_metrics=False,
            azure_monitor_enable_performance_counters=False,
            azure_monitor_exporter_disable_offline_storage=True,
            azure_monitor_exporter_storage_directory="/tmp/test",
            azure_monitor_browser_sdk_loader_config={"enabled": False},
        )
        _, azure_kwargs = append_mock.call_args[0]
        self.assertEqual(azure_kwargs["connection_string"], TEST_CONNECTION_STRING)
        self.assertEqual(azure_kwargs["credential"], "test_cred")
        self.assertEqual(azure_kwargs["enable_live_metrics"], False)
        self.assertEqual(azure_kwargs["enable_performance_counters"], False)
        self.assertEqual(azure_kwargs["disable_offline_storage"], True)
        self.assertEqual(azure_kwargs["storage_directory"], "/tmp/test")
        self.assertEqual(azure_kwargs["browser_sdk_loader_config"], {"enabled": False})

    @patch("microsoft.opentelemetry._distro._append_azure_monitor_components", return_value=(None, None, None))
    def test_general_otel_kwargs_forwarded(self, append_mock):
        """General OTel kwargs are forwarded via otel_kwargs."""
        use_microsoft_opentelemetry(
            enable_azure_monitor=True,
            azure_monitor_connection_string=TEST_CONNECTION_STRING,
            sampling_ratio=0.5,
            logger_name="test",
        )
        otel_kwargs, azure_kwargs = append_mock.call_args[0]
        self.assertEqual(azure_kwargs["connection_string"], TEST_CONNECTION_STRING)
        self.assertEqual(otel_kwargs["sampling_ratio"], 0.5)
        self.assertEqual(otel_kwargs["logger_name"], "test")

    @patch("microsoft.opentelemetry._distro._append_azure_monitor_components")
    @patch("microsoft.opentelemetry._distro._setup_logging")
    @patch("microsoft.opentelemetry._distro._setup_metrics")
    @patch("microsoft.opentelemetry._distro._setup_tracing")
    def test_explicit_disable(self, tracing_mock, metrics_mock, logging_mock, append_mock):
        """Explicitly disabling Azure Monitor still creates providers."""
        use_microsoft_opentelemetry(
            azure_monitor_connection_string=TEST_CONNECTION_STRING,
            enable_azure_monitor=False,
        )
        tracing_mock.assert_called_once()
        metrics_mock.assert_called_once()
        logging_mock.assert_called_once()
        append_mock.assert_not_called()

    @patch("microsoft.opentelemetry._distro._append_azure_monitor_components", return_value=(None, None, None))
    def test_enable_key_not_forwarded(self, append_mock):
        """enable_azure_monitor is consumed, not forwarded."""
        use_microsoft_opentelemetry(
            azure_monitor_connection_string=TEST_CONNECTION_STRING,
            enable_azure_monitor=True,
        )
        otel_kwargs, azure_kwargs = append_mock.call_args[0]
        self.assertNotIn("enable_azure_monitor", otel_kwargs)
        self.assertNotIn("enable_azure_monitor", azure_kwargs)

    def test_microsoft_opentelemetry_version_env_var_set_for_azure_monitor(self):
        from microsoft.opentelemetry._constants import MICROSOFT_OPENTELEMETRY_VERSION_ENV
        from microsoft.opentelemetry._version import VERSION

        captured: dict = {}

        def _capture(_otel_kwargs, _azure_kwargs):
            captured["env_value"] = os.environ.get(MICROSOFT_OPENTELEMETRY_VERSION_ENV)
            return (None, None, None)

        original = os.environ.pop(MICROSOFT_OPENTELEMETRY_VERSION_ENV, None)
        try:
            with patch(
                "microsoft.opentelemetry._distro._append_azure_monitor_components",
                side_effect=_capture,
            ):
                use_microsoft_opentelemetry(
                    enable_azure_monitor=True,
                    azure_monitor_connection_string=TEST_CONNECTION_STRING,
                )
            self.assertEqual(captured.get("env_value"), VERSION)
            self.assertEqual(os.environ.get(MICROSOFT_OPENTELEMETRY_VERSION_ENV), VERSION)
        finally:
            if original is None:
                os.environ.pop(MICROSOFT_OPENTELEMETRY_VERSION_ENV, None)
            else:
                os.environ[MICROSOFT_OPENTELEMETRY_VERSION_ENV] = original

    @patch("microsoft.opentelemetry._distro._setup_logging")
    @patch("microsoft.opentelemetry._distro._setup_metrics")
    @patch("microsoft.opentelemetry._distro._setup_tracing")
    def test_disable_tracing_skips_tracing(self, tracing_mock, metrics_mock, logging_mock):
        """disable_tracing=True skips TracerProvider creation."""
        use_microsoft_opentelemetry(disable_tracing=True)
        tracing_mock.assert_not_called()
        metrics_mock.assert_called_once()
        logging_mock.assert_called_once()

    @patch("microsoft.opentelemetry._distro._setup_logging")
    @patch("microsoft.opentelemetry._distro._setup_metrics")
    @patch("microsoft.opentelemetry._distro._setup_tracing")
    def test_disable_metrics_skips_metrics(self, tracing_mock, metrics_mock, logging_mock):
        """disable_metrics=True skips MeterProvider creation."""
        use_microsoft_opentelemetry(disable_metrics=True)
        tracing_mock.assert_called_once()
        metrics_mock.assert_not_called()
        logging_mock.assert_called_once()

    @patch("microsoft.opentelemetry._distro._setup_logging")
    @patch("microsoft.opentelemetry._distro._setup_metrics")
    @patch("microsoft.opentelemetry._distro._setup_tracing")
    def test_disable_logging_skips_logging(self, tracing_mock, metrics_mock, logging_mock):
        """disable_logging=True skips LoggerProvider creation."""
        use_microsoft_opentelemetry(disable_logging=True)
        tracing_mock.assert_called_once()
        metrics_mock.assert_called_once()
        logging_mock.assert_not_called()


class TestOTelProviderSetup(unittest.TestCase):
    """Tests for core OTel provider initialisation functions."""

    def test_setup_tracing_creates_provider(self):
        """_setup_tracing creates a TracerProvider."""
        tp = _setup_tracing(TEST_RESOURCE, {})
        self.assertIsInstance(tp, TracerProvider)

    def test_setup_tracing_adds_span_processors(self):
        """_setup_tracing adds user-supplied span processors."""
        sp = MagicMock()
        tp = _setup_tracing(TEST_RESOURCE, {"span_processors": [sp]})
        self.assertIsInstance(tp, TracerProvider)
        self.assertIn(sp, tp._active_span_processor._span_processors)

    def test_setup_metrics_creates_provider(self):
        """_setup_metrics creates a MeterProvider."""
        mp = _setup_metrics(TEST_RESOURCE, {})
        self.assertIsInstance(mp, MeterProvider)


class TestAzureMonitorComponentCollection(unittest.TestCase):
    """Tests for _append_azure_monitor_components()."""

    @patch("microsoft.opentelemetry._distro._append_azure_monitor_components", return_value=(None, None, None))
    def test_post_setup_skipped_on_failure(self, append_mock):
        """When component collection returns None configs, post-setup is skipped."""
        use_microsoft_opentelemetry(
            enable_azure_monitor=True,
            azure_monitor_connection_string=TEST_CONNECTION_STRING,
        )
        append_mock.assert_called_once()

    @patch("microsoft.opentelemetry._distro._append_azure_monitor_components", return_value=(None, None, None))
    def test_providers_created_by_azure_monitor(self, append_mock):
        """Providers are created by Azure Monitor _setup_* and registered by distro."""
        use_microsoft_opentelemetry(
            enable_azure_monitor=True,
            azure_monitor_connection_string=TEST_CONNECTION_STRING,
        )
        append_mock.assert_called_once()

    @patch("microsoft.opentelemetry._distro._append_azure_monitor_components", return_value=(None, None, None))
    def test_otel_and_azure_kwargs_forwarded(self, append_mock):
        """Both OTel and Azure Monitor kwargs are forwarded to component collection."""
        use_microsoft_opentelemetry(
            enable_azure_monitor=True,
            azure_monitor_connection_string=TEST_CONNECTION_STRING,
            sampling_ratio=0.5,
            logger_name="test",
        )
        otel_kwargs, azure_kwargs = append_mock.call_args[0]
        self.assertEqual(azure_kwargs["connection_string"], TEST_CONNECTION_STRING)
        self.assertEqual(otel_kwargs["sampling_ratio"], 0.5)
        self.assertEqual(otel_kwargs["logger_name"], "test")


class TestEnableKwargsPassthrough(unittest.TestCase):
    """Tests that azure_monitor_ kwargs are remapped and passed through."""

    @patch("microsoft.opentelemetry._distro._append_azure_monitor_components", return_value=(None, None, None))
    def test_enable_live_metrics_passed_through(self, append_mock):
        """azure_monitor_enable_live_metrics is remapped and forwarded."""
        use_microsoft_opentelemetry(
            enable_azure_monitor=True,
            azure_monitor_connection_string=TEST_CONNECTION_STRING,
            azure_monitor_enable_live_metrics=False,
        )
        _, azure_kwargs = append_mock.call_args[0]
        self.assertEqual(azure_kwargs["enable_live_metrics"], False)

    @patch("microsoft.opentelemetry._distro._append_azure_monitor_components", return_value=(None, None, None))
    def test_enable_performance_counters_passed_through(self, append_mock):
        """azure_monitor_enable_performance_counters is remapped and forwarded."""
        use_microsoft_opentelemetry(
            enable_azure_monitor=True,
            azure_monitor_connection_string=TEST_CONNECTION_STRING,
            azure_monitor_enable_performance_counters=False,
        )
        _, azure_kwargs = append_mock.call_args[0]
        self.assertEqual(azure_kwargs["enable_performance_counters"], False)


class TestAllConfigOptions(unittest.TestCase):
    """End-to-end test that every documented configuration option works."""

    @patch("microsoft.opentelemetry._utils.is_otlp_enabled", return_value=False)
    @patch("microsoft.opentelemetry._distro._append_azure_monitor_components", return_value=(None, None, None))
    def test_all_options_end_to_end(self, append_mock, otlp_mock):
        """Every documented kwarg is accepted, remapped if needed, and forwarded."""
        from logging import Formatter

        formatter = Formatter("%(message)s")

        use_microsoft_opentelemetry(
            enable_azure_monitor=True,
            # Azure Monitor options (remapped)
            azure_monitor_connection_string=TEST_CONNECTION_STRING,
            azure_monitor_exporter_credential="test_cred",
            azure_monitor_enable_live_metrics=False,
            azure_monitor_enable_performance_counters=False,
            azure_monitor_exporter_disable_offline_storage=True,
            azure_monitor_exporter_storage_directory="/tmp/test",
            azure_monitor_browser_sdk_loader_config={"enabled": False},
            # General OTel options (passed through unchanged)
            disable_logging=True,
            disable_tracing=True,
            disable_metrics=True,
            resource=TEST_RESOURCE,
            span_processors=["sp1"],
            log_record_processors=["lrp1"],
            metric_readers=["mr1"],
            views=["v1"],
            logger_name="mylogger",
            logging_formatter=formatter,
            instrumentation_options={"flask": {"enabled": False}},
            enable_trace_based_sampling_for_logs=True,
            sampling_ratio=0.25,
        )

        append_mock.assert_called_once()
        otel_kwargs, azure_kwargs = append_mock.call_args[0]

        # Remapped azure_monitor_ kwargs
        self.assertEqual(azure_kwargs["connection_string"], TEST_CONNECTION_STRING)
        self.assertEqual(azure_kwargs["credential"], "test_cred")
        self.assertEqual(azure_kwargs["enable_live_metrics"], False)
        self.assertEqual(azure_kwargs["enable_performance_counters"], False)
        self.assertEqual(azure_kwargs["disable_offline_storage"], True)
        self.assertEqual(azure_kwargs["storage_directory"], "/tmp/test")
        self.assertEqual(azure_kwargs["browser_sdk_loader_config"], {"enabled": False})

        # General OTel kwargs
        self.assertEqual(otel_kwargs["disable_logging"], True)
        self.assertEqual(otel_kwargs["disable_tracing"], True)
        self.assertEqual(otel_kwargs["disable_metrics"], True)
        self.assertEqual(otel_kwargs["resource"], TEST_RESOURCE)
        self.assertEqual(otel_kwargs["span_processors"], ["sp1"])
        self.assertEqual(otel_kwargs["log_record_processors"], ["lrp1"])
        self.assertEqual(otel_kwargs["metric_readers"], ["mr1"])
        self.assertEqual(otel_kwargs["views"], ["v1"])
        self.assertEqual(otel_kwargs["logger_name"], "mylogger")
        self.assertEqual(otel_kwargs["logging_formatter"], formatter)
        self.assertEqual(otel_kwargs["instrumentation_options"], {"flask": {"enabled": False}})
        self.assertEqual(otel_kwargs["enable_trace_based_sampling_for_logs"], True)
        self.assertEqual(otel_kwargs["sampling_ratio"], 0.25)

        # azure_monitor_ prefixed keys should NOT appear in otel_kwargs
        for key in otel_kwargs:
            self.assertFalse(
                key.startswith("azure_monitor_"),
                f"Prefixed key '{key}' should have been remapped",
            )
        self.assertNotIn("enable_azure_monitor", otel_kwargs)


class TestSetupLogging(unittest.TestCase):
    """Tests for _setup_logging()."""

    def test_creates_logger_provider(self):
        """_setup_logging creates a LoggerProvider."""
        lp = _setup_logging(TEST_RESOURCE, {})
        self.assertIsNotNone(lp)

    @patch("opentelemetry._logs.set_logger_provider")
    def test_adds_log_record_processors(self, set_lp_mock):
        """_setup_logging adds user-supplied log record processors."""
        lrp = MagicMock()
        lp = _setup_logging(TEST_RESOURCE, {"log_record_processors": [lrp]})
        self.assertIsNotNone(lp)

    @patch("opentelemetry._logs.set_logger_provider")
    def test_attaches_handler_for_logger_name(self, set_lp_mock):
        """_setup_logging attaches a LoggingHandler when logger_name is specified."""
        import logging

        test_logger_name = "test_distro_logging_handler"
        test_logger = logging.getLogger(test_logger_name)
        original_handlers = list(test_logger.handlers)

        try:
            _setup_logging(TEST_RESOURCE, {"logger_name": test_logger_name})
            # Should have added a handler
            self.assertGreater(len(test_logger.handlers), len(original_handlers))
        finally:
            # Cleanup: remove any handlers we added
            for h in list(test_logger.handlers):
                if h not in original_handlers:
                    test_logger.removeHandler(h)

    @patch("opentelemetry._logs.set_logger_provider")
    def test_attaches_handler_with_formatter(self, set_lp_mock):
        """_setup_logging sets formatter on handler when logging_formatter is provided."""
        import logging

        test_logger_name = "test_distro_logging_formatter"
        test_logger = logging.getLogger(test_logger_name)
        original_handlers = list(test_logger.handlers)
        formatter = logging.Formatter("%(message)s")

        try:
            _setup_logging(
                TEST_RESOURCE,
                {
                    "logger_name": test_logger_name,
                    "logging_formatter": formatter,
                },
            )
            new_handlers = [h for h in test_logger.handlers if h not in original_handlers]
            self.assertTrue(len(new_handlers) > 0)
            self.assertEqual(new_handlers[0].formatter, formatter)
        finally:
            for h in list(test_logger.handlers):
                if h not in original_handlers:
                    test_logger.removeHandler(h)


class TestA365KwargsConfiguration(unittest.TestCase):
    """Tests for A365 kwargs precedence and forwarding in _append_a365_components."""

    @patch("microsoft.opentelemetry._distro._append_a365_components")
    def test_a365_kwargs_forwarded(self, a365_mock):
        """A365 kwargs are parsed and forwarded to _append_a365_components."""

        def token_fn(aid, tid):
            return "token"

        use_microsoft_opentelemetry(
            enable_a365=True,
            a365_token_resolver=token_fn,
            a365_cluster_category="gov",
            a365_use_s2s_endpoint=True,
            a365_suppress_invoke_agent_input=True,
            a365_enable_observability_exporter=False,
            a365_observability_scope_override="api://custom-scope/.default",
            a365_max_queue_size=5000,
            a365_scheduled_delay_ms=2000,
            a365_exporter_timeout_ms=13000,
            a365_max_export_batch_size=534,
        )
        a365_mock.assert_called_once()
        _, kwargs = a365_mock.call_args
        self.assertEqual(kwargs["token_resolver"], token_fn)
        self.assertEqual(kwargs["cluster_category"], "gov")
        self.assertEqual(kwargs["use_s2s_endpoint"], True)
        self.assertEqual(kwargs["suppress_invoke_agent_input"], True)
        self.assertEqual(kwargs["enable_observability_exporter"], False)
        self.assertEqual(kwargs["observability_scope_override"], "api://custom-scope/.default")
        self.assertEqual(kwargs["max_queue_size"], 5000)
        self.assertEqual(kwargs["scheduled_delay_ms"], 2000)
        self.assertEqual(kwargs["exporter_timeout_ms"], 13000)
        self.assertEqual(kwargs["max_export_batch_size"], 534)

    @patch("microsoft.opentelemetry._distro._append_a365_components")
    def test_a365_not_called_when_disabled(self, a365_mock):
        """_append_a365_components is called with enable_a365=False (skips internally)."""
        use_microsoft_opentelemetry()
        a365_mock.assert_called_once()
        args = a365_mock.call_args[0]
        self.assertFalse(args[0])  # enable_a365=False

    @patch("microsoft.opentelemetry._distro._append_a365_components")
    def test_a365_kwargs_not_leaked_to_otel(self, a365_mock):
        """A365 kwargs are consumed and not forwarded to OTel kwargs."""
        use_microsoft_opentelemetry(
            enable_a365=True,
            enable_azure_monitor=False,
        )
        otel_kwargs = a365_mock.call_args[0][1]
        self.assertNotIn("enable_a365", otel_kwargs)

    @patch("microsoft.opentelemetry.a365.core.exporters.utils._create_default_token_resolver")
    def test_tenant_and_agent_ids_not_sourced_from_env(self, default_resolver_mock):

        default_resolver_mock.return_value = lambda aid, tid: "token"

        env = {
            "A365_TENANT_ID": "env-tenant",
            "A365_AGENT_ID": "env-agent",
        }
        with patch.dict("os.environ", env, clear=False):
            otel_kwargs = {"span_processors": []}
            _append_a365_components(True, otel_kwargs, enable_observability_exporter=True)

        processors = otel_kwargs["span_processors"]
        self.assertEqual(len(processors), 2)

        baggage_proc = processors[0]
        self.assertIsNone(baggage_proc._tenant_id)
        self.assertIsNone(baggage_proc._agent_id)

    def test_a365_skipped_when_disabled(self):
        """No processors added when enable_a365=False."""
        otel_kwargs = {"span_processors": []}
        _append_a365_components(False, otel_kwargs)
        self.assertEqual(otel_kwargs["span_processors"], [])

    def test_baggage_processor_registered_when_exporter_disabled(self):
        """A365SpanProcessor is registered even when the exporter is disabled."""
        from microsoft.opentelemetry.a365.core.exporters.span_processor import A365SpanProcessor

        env = {k: v for k, v in os.environ.items() if k != "ENABLE_A365_OBSERVABILITY_EXPORTER"}
        with patch.dict("os.environ", env, clear=True):
            otel_kwargs = {"span_processors": []}
            _append_a365_components(True, otel_kwargs, enable_observability_exporter=False)

        processors = otel_kwargs["span_processors"]
        self.assertEqual(len(processors), 1)
        self.assertIsInstance(processors[0], A365SpanProcessor)

    @patch("microsoft.opentelemetry.a365.core.exporters.utils._create_default_token_resolver")
    def test_exporter_registered_when_a365_enabled_and_env_true(self, default_resolver_mock):
        """When enable_a365=True and ENABLE_A365_OBSERVABILITY_EXPORTER=true, the
        Agent365 exporter span processor is added alongside A365SpanProcessor."""
        default_resolver_mock.return_value = lambda aid, tid: "token"
        from microsoft.opentelemetry.a365.core.exporters.span_processor import A365SpanProcessor

        with patch.dict("os.environ", {"ENABLE_A365_OBSERVABILITY_EXPORTER": "true"}, clear=False):
            otel_kwargs = {"span_processors": []}
            _append_a365_components(True, otel_kwargs)

        processors = otel_kwargs["span_processors"]
        # Two processors: A365SpanProcessor + Agent365 exporter processor
        self.assertEqual(len(processors), 2)
        self.assertIsInstance(processors[0], A365SpanProcessor)

    def test_baggage_processor_registered_when_exporter_env_unset(self):
        """When enable_a365=True and ENABLE_A365_OBSERVABILITY_EXPORTER is unset,
        only the A365SpanProcessor is registered (no exporter processor)."""
        from microsoft.opentelemetry.a365.core.exporters.span_processor import A365SpanProcessor

        env = {k: v for k, v in os.environ.items() if k != "ENABLE_A365_OBSERVABILITY_EXPORTER"}
        with patch.dict("os.environ", env, clear=True):
            otel_kwargs = {"span_processors": []}
            _append_a365_components(True, otel_kwargs)

        processors = otel_kwargs["span_processors"]
        self.assertEqual(len(processors), 1)
        self.assertIsInstance(processors[0], A365SpanProcessor)

    @patch("microsoft.opentelemetry.a365.core.exporters.utils._create_default_token_resolver")
    def test_cluster_category_kwarg_overrides_env(self, default_resolver_mock):
        """a365_cluster_category kwarg takes precedence over A365_CLUSTER_CATEGORY env var."""
        default_resolver_mock.return_value = lambda aid, tid: "token"

        with (
            patch.dict("os.environ", {"A365_CLUSTER_CATEGORY": "mooncake"}, clear=False),
            patch("microsoft.opentelemetry.a365.core.exporters.agent365_exporter._Agent365Exporter") as exporter_mock,
        ):
            otel_kwargs = {"span_processors": []}
            _append_a365_components(True, otel_kwargs, cluster_category="gov", enable_observability_exporter=True)

        _, exporter_kwargs = exporter_mock.call_args
        self.assertEqual(exporter_kwargs["cluster_category"], "gov")

    @patch("microsoft.opentelemetry.a365.core.exporters.utils._create_default_token_resolver")
    def test_cluster_category_falls_back_to_env(self, default_resolver_mock):
        """A365_CLUSTER_CATEGORY env var is used when kwarg not provided."""
        default_resolver_mock.return_value = lambda aid, tid: "token"

        with (
            patch.dict("os.environ", {"A365_CLUSTER_CATEGORY": "gov"}, clear=False),
            patch("microsoft.opentelemetry.a365.core.exporters.agent365_exporter._Agent365Exporter") as exporter_mock,
        ):
            otel_kwargs = {"span_processors": []}
            _append_a365_components(True, otel_kwargs, enable_observability_exporter=True)

        _, exporter_kwargs = exporter_mock.call_args
        self.assertEqual(exporter_kwargs["cluster_category"], "gov")

    @patch("microsoft.opentelemetry.a365.core.exporters.utils._create_default_token_resolver")
    def test_cluster_category_defaults_to_prod(self, default_resolver_mock):
        """cluster_category defaults to 'prod' when neither kwarg nor env var is set."""
        default_resolver_mock.return_value = lambda aid, tid: "token"

        env = {k: v for k, v in os.environ.items() if k != "A365_CLUSTER_CATEGORY"}
        with (
            patch.dict("os.environ", env, clear=True),
            patch("microsoft.opentelemetry.a365.core.exporters.agent365_exporter._Agent365Exporter") as exporter_mock,
        ):
            otel_kwargs = {"span_processors": []}
            _append_a365_components(True, otel_kwargs, enable_observability_exporter=True)

        _, exporter_kwargs = exporter_mock.call_args
        self.assertEqual(exporter_kwargs["cluster_category"], "prod")

    @patch("microsoft.opentelemetry.a365.core.exporters.utils._create_default_token_resolver")
    def test_enable_observability_exporter_kwarg_false_skips_exporter(self, default_resolver_mock):
        """a365_enable_observability_exporter=False with env unset skips the exporter."""
        default_resolver_mock.return_value = lambda aid, tid: "token"
        from microsoft.opentelemetry.a365.core.exporters.span_processor import A365SpanProcessor

        env = {k: v for k, v in os.environ.items() if k != "ENABLE_A365_OBSERVABILITY_EXPORTER"}
        with (
            patch.dict("os.environ", env, clear=True),
            patch("microsoft.opentelemetry.a365.core.exporters.agent365_exporter._Agent365Exporter") as exporter_mock,
        ):
            otel_kwargs = {"span_processors": []}
            _append_a365_components(True, otel_kwargs, enable_observability_exporter=False)

        exporter_mock.assert_not_called()
        # _append_a365_components always registers the baggage A365SpanProcessor
        # even when the exporter is disabled.
        processors = otel_kwargs["span_processors"]
        self.assertEqual(len(processors), 1)
        self.assertIsInstance(processors[0], A365SpanProcessor)

    @patch("microsoft.opentelemetry.a365.core.exporters.utils._create_default_token_resolver")
    def test_enable_observability_exporter_env_true_enables_when_kwarg_false(self, default_resolver_mock):
        """With OR semantics, env var being true enables the exporter even if the kwarg is False."""
        default_resolver_mock.return_value = lambda aid, tid: "token"

        with (
            patch.dict("os.environ", {"ENABLE_A365_OBSERVABILITY_EXPORTER": "true"}, clear=False),
            patch("microsoft.opentelemetry.a365.core.exporters.agent365_exporter._Agent365Exporter") as exporter_mock,
        ):
            otel_kwargs = {"span_processors": []}
            _append_a365_components(True, otel_kwargs, enable_observability_exporter=False)

        exporter_mock.assert_called_once()

    @patch("microsoft.opentelemetry.a365.core.exporters.utils._create_default_token_resolver")
    def test_enable_observability_exporter_falls_back_to_env_false(self, default_resolver_mock):
        """ENABLE_A365_OBSERVABILITY_EXPORTER=false env var disables exporter when kwarg not provided."""
        default_resolver_mock.return_value = lambda aid, tid: "token"

        with (
            patch.dict("os.environ", {"ENABLE_A365_OBSERVABILITY_EXPORTER": "false"}, clear=False),
            patch("microsoft.opentelemetry.a365.core.exporters.agent365_exporter._Agent365Exporter") as exporter_mock,
        ):
            otel_kwargs = {"span_processors": []}
            _append_a365_components(True, otel_kwargs)

        exporter_mock.assert_not_called()

    @patch("microsoft.opentelemetry.a365.core.exporters.utils._create_default_token_resolver")
    def test_enable_observability_exporter_defaults_false(self, default_resolver_mock):
        """Exporter is NOT built when neither kwarg nor env var is set (counterpart semantics)."""
        default_resolver_mock.return_value = lambda aid, tid: "token"

        env = {k: v for k, v in os.environ.items() if k != "ENABLE_A365_OBSERVABILITY_EXPORTER"}
        with (
            patch.dict("os.environ", env, clear=True),
            patch("microsoft.opentelemetry.a365.core.exporters.agent365_exporter._Agent365Exporter") as exporter_mock,
        ):
            otel_kwargs = {"span_processors": []}
            _append_a365_components(True, otel_kwargs)

        exporter_mock.assert_not_called()

    @patch("microsoft.opentelemetry.a365.core.exporters.utils._create_default_token_resolver")
    def test_enable_observability_exporter_kwarg_true_enables_exporter(self, default_resolver_mock):
        """a365_enable_observability_exporter=True alone enables the exporter (env var not required)."""
        default_resolver_mock.return_value = lambda aid, tid: "token"

        env = {k: v for k, v in os.environ.items() if k != "ENABLE_A365_OBSERVABILITY_EXPORTER"}
        with (
            patch.dict("os.environ", env, clear=True),
            patch("microsoft.opentelemetry.a365.core.exporters.agent365_exporter._Agent365Exporter") as exporter_mock,
        ):
            otel_kwargs = {"span_processors": []}
            _append_a365_components(True, otel_kwargs, enable_observability_exporter=True)

        exporter_mock.assert_called_once()

    @patch("microsoft.opentelemetry.a365.core.exporters.utils._create_default_token_resolver")
    def test_observability_scope_override_kwarg_passed_to_resolver(self, default_resolver_mock):
        """a365_observability_scope_override kwarg is forwarded to _create_default_token_resolver."""
        default_resolver_mock.return_value = lambda aid, tid: "token"

        with patch("microsoft.opentelemetry.a365.core.exporters.agent365_exporter._Agent365Exporter"):
            otel_kwargs = {"span_processors": []}
            _append_a365_components(
                True,
                otel_kwargs,
                observability_scope_override="api://custom-scope/.default",
                enable_observability_exporter=True,
            )

        default_resolver_mock.assert_called_once_with(scope_override="api://custom-scope/.default")

    @patch("microsoft.opentelemetry.a365.core.exporters.utils._create_default_token_resolver")
    def test_observability_scope_override_kwarg_overrides_env(self, default_resolver_mock):
        """a365_observability_scope_override kwarg takes precedence over env var."""
        default_resolver_mock.return_value = lambda aid, tid: "token"

        with (
            patch.dict(
                "os.environ",
                {"A365_OBSERVABILITY_SCOPE_OVERRIDE": "api://env-scope/.default"},
                clear=False,
            ),
            patch("microsoft.opentelemetry.a365.core.exporters.agent365_exporter._Agent365Exporter"),
        ):
            otel_kwargs = {"span_processors": []}
            _append_a365_components(
                True,
                otel_kwargs,
                observability_scope_override="api://kwarg-scope/.default",
                enable_observability_exporter=True,
            )

        default_resolver_mock.assert_called_once_with(scope_override="api://kwarg-scope/.default")

    @patch("microsoft.opentelemetry.a365.core.exporters.utils._create_default_token_resolver")
    def test_observability_scope_override_falls_back_to_env(self, default_resolver_mock):
        """A365_OBSERVABILITY_SCOPE_OVERRIDE env var is forwarded when kwarg not provided."""
        default_resolver_mock.return_value = lambda aid, tid: "token"

        with (
            patch.dict(
                "os.environ",
                {"A365_OBSERVABILITY_SCOPE_OVERRIDE": "api://env-scope/.default"},
                clear=False,
            ),
            patch("microsoft.opentelemetry.a365.core.exporters.agent365_exporter._Agent365Exporter"),
        ):
            otel_kwargs = {"span_processors": []}
            _append_a365_components(True, otel_kwargs, enable_observability_exporter=True)

        default_resolver_mock.assert_called_once_with(scope_override="api://env-scope/.default")

    @patch("microsoft.opentelemetry.a365.core.exporters.utils._create_default_token_resolver")
    def test_observability_scope_override_defaults_to_none(self, default_resolver_mock):
        """scope_override is None when neither kwarg nor env var is set."""
        default_resolver_mock.return_value = lambda aid, tid: "token"

        env = {k: v for k, v in os.environ.items() if k != "A365_OBSERVABILITY_SCOPE_OVERRIDE"}
        with (
            patch.dict("os.environ", env, clear=True),
            patch("microsoft.opentelemetry.a365.core.exporters.agent365_exporter._Agent365Exporter"),
        ):
            otel_kwargs = {"span_processors": []}
            _append_a365_components(True, otel_kwargs, enable_observability_exporter=True)

        default_resolver_mock.assert_called_once_with(scope_override=None)


class TestA365BatchProcessorKwargs(unittest.TestCase):
    """Tests for a365_max_queue_size / scheduled_delay_ms / exporter_timeout_ms /
    max_export_batch_size kwargs forwarding to _EnrichingBatchSpanProcessor."""

    def _build(self, **kwargs):
        """Run _append_a365_components with the exporter enabled and return the
        captured kwargs that were passed to _EnrichingBatchSpanProcessor."""
        with (
            patch(
                "microsoft.opentelemetry.a365.core.exporters.utils._create_default_token_resolver",
                return_value=lambda aid, tid: "token",
            ),
            patch("microsoft.opentelemetry.a365.core.exporters.agent365_exporter._Agent365Exporter"),
            patch(
                "microsoft.opentelemetry.a365.core.exporters.enriching_span_processor._EnrichingBatchSpanProcessor"
            ) as proc_mock,
        ):
            otel_kwargs = {"span_processors": []}
            _append_a365_components(
                True,
                otel_kwargs,
                enable_observability_exporter=True,
                **kwargs,
            )
        proc_mock.assert_called_once()
        return proc_mock.call_args.kwargs

    def test_batch_kwargs_omitted_when_user_does_not_pass_them(self):
        """When the user passes none of the batch kwargs, the processor is
        constructed without any of them so BatchSpanProcessor uses its own
        defaults."""
        proc_kwargs = self._build()
        self.assertNotIn("max_queue_size", proc_kwargs)
        self.assertNotIn("schedule_delay_millis", proc_kwargs)
        self.assertNotIn("export_timeout_millis", proc_kwargs)
        self.assertNotIn("max_export_batch_size", proc_kwargs)

    def test_max_queue_size_forwarded(self):
        proc_kwargs = self._build(max_queue_size=4096)
        self.assertEqual(proc_kwargs["max_queue_size"], 4096)
        self.assertNotIn("schedule_delay_millis", proc_kwargs)
        self.assertNotIn("export_timeout_millis", proc_kwargs)
        self.assertNotIn("max_export_batch_size", proc_kwargs)

    def test_scheduled_delay_ms_forwarded_with_renamed_key(self):
        """a365_scheduled_delay_ms maps to BatchSpanProcessor's schedule_delay_millis."""
        proc_kwargs = self._build(scheduled_delay_ms=2000)
        self.assertEqual(proc_kwargs["schedule_delay_millis"], 2000)
        self.assertNotIn("scheduled_delay_ms", proc_kwargs)

    def test_exporter_timeout_ms_forwarded_with_renamed_key(self):
        """a365_exporter_timeout_ms maps to BatchSpanProcessor's export_timeout_millis."""
        proc_kwargs = self._build(exporter_timeout_ms=15000)
        self.assertEqual(proc_kwargs["export_timeout_millis"], 15000)
        self.assertNotIn("exporter_timeout_ms", proc_kwargs)

    def test_max_export_batch_size_forwarded(self):
        proc_kwargs = self._build(max_export_batch_size=256)
        self.assertEqual(proc_kwargs["max_export_batch_size"], 256)

    def test_all_batch_kwargs_forwarded_together(self):
        proc_kwargs = self._build(
            max_queue_size=4096,
            scheduled_delay_ms=2000,
            exporter_timeout_ms=15000,
            max_export_batch_size=256,
        )
        self.assertEqual(proc_kwargs["max_queue_size"], 4096)
        self.assertEqual(proc_kwargs["schedule_delay_millis"], 2000)
        self.assertEqual(proc_kwargs["export_timeout_millis"], 15000)
        self.assertEqual(proc_kwargs["max_export_batch_size"], 256)
        # Always-forwarded enriching kwarg is preserved.
        self.assertIn("suppress_invoke_agent_input", proc_kwargs)

    def test_zero_value_is_forwarded(self):
        """Falsy-but-not-None integer values must still be forwarded (not dropped)."""
        proc_kwargs = self._build(max_queue_size=0, max_export_batch_size=0)
        self.assertEqual(proc_kwargs["max_queue_size"], 0)
        self.assertEqual(proc_kwargs["max_export_batch_size"], 0)


class TestA365Components(unittest.TestCase):
    """Tests for A365 enable_a365 flag and _append_a365_components."""

    @patch("microsoft.opentelemetry._distro._append_azure_monitor_components", return_value=(None, None, None))
    @patch("microsoft.opentelemetry._distro._append_a365_components")
    def test_a365_enabled_passed_through(self, a365_mock, azure_monitor_mock):
        """enable_a365=True is forwarded to _append_a365_components."""
        use_microsoft_opentelemetry(
            enable_a365=True,
        )
        a365_mock.assert_called_once()
        call_args = a365_mock.call_args
        # First arg: enable_a365
        self.assertTrue(call_args[0][0])

    @patch("microsoft.opentelemetry._distro._append_azure_monitor_components", return_value=(None, None, None))
    @patch("microsoft.opentelemetry._distro._append_a365_components")
    def test_a365_disabled_by_default(self, a365_mock, azure_monitor_mock):
        """A365 is disabled by default."""
        use_microsoft_opentelemetry()
        a365_mock.assert_called_once()
        # enable_a365 should be False
        self.assertFalse(a365_mock.call_args[0][0])

    @patch("microsoft.opentelemetry._distro._append_azure_monitor_components", return_value=(None, None, None))
    @patch("microsoft.opentelemetry._distro._append_a365_components")
    def test_a365_flag_not_in_otel_kwargs(self, a365_mock, azure_monitor_mock):
        """enable_a365 should not leak into azure_monitor_kwargs."""
        use_microsoft_opentelemetry(
            enable_azure_monitor=True,
            enable_a365=True,
            azure_monitor_connection_string=TEST_CONNECTION_STRING,
        )
        # Check azure_monitor_mock received connection_string but not enable_a365
        am_call = azure_monitor_mock.call_args
        if am_call:
            merged = {**am_call[0][0], **am_call[0][1]} if am_call[0] else {}
            self.assertNotIn("enable_a365", merged)

    @patch("microsoft.opentelemetry._distro._append_azure_monitor_components", return_value=(None, None, None))
    def test_a365_appends_span_processors(self, azure_monitor_mock):
        """When A365 is enabled, its span processors are appended to otel_kwargs."""
        mock_sp = MagicMock()
        mock_handlers = MagicMock()
        mock_handlers.span_processors = [mock_sp]

        with patch(
            "microsoft.opentelemetry.a365.create_a365_components",
            return_value=mock_handlers,
        ):
            use_microsoft_opentelemetry(enable_a365=True)

        from opentelemetry.trace import get_tracer_provider

        tp = get_tracer_provider()
        self.assertIsNotNone(tp)


class TestSpectraComponents(unittest.TestCase):
    """Tests for Spectra sidecar _append_spectra_components."""

    def test_spectra_skipped_when_disabled(self):
        """No processors added when enable_spectra=False."""
        otel_kwargs = {"span_processors": []}
        _append_spectra_components(False, otel_kwargs)
        self.assertEqual(otel_kwargs["span_processors"], [])

    def test_spectra_skipped_when_tracing_disabled(self):
        """No processors added when disable_tracing=True."""
        otel_kwargs = {"span_processors": [], "disable_tracing": True}
        _append_spectra_components(True, otel_kwargs)
        self.assertEqual(otel_kwargs["span_processors"], [])

    @patch(
        "microsoft.opentelemetry._distro.os.environ",
        {"SPECTRA_PROTOCOL": "grpc"},
    )
    def test_grpc_fallback_to_http_when_grpc_unavailable(self):
        """Falls back to HTTP when gRPC package is not installed."""
        mock_http_exporter = MagicMock()

        otel_kwargs = {}
        with patch.dict(
            "sys.modules",
            {
                "opentelemetry.exporter.otlp.proto.grpc": None,
                "opentelemetry.exporter.otlp.proto.grpc.trace_exporter": None,
            },
        ):
            with patch(
                "opentelemetry.exporter.otlp.proto.http.trace_exporter.OTLPSpanExporter",
                return_value=mock_http_exporter,
            ):
                _append_spectra_components(True, otel_kwargs, protocol="grpc")

        processors = otel_kwargs.get("span_processors", [])
        self.assertEqual(len(processors), 1)

    @patch(
        "opentelemetry.exporter.otlp.proto.http.trace_exporter.OTLPSpanExporter",
    )
    def test_http_protocol_uses_http_exporter(self, mock_exporter_cls):
        """protocol='http' uses HTTP exporter directly."""
        mock_exporter_cls.return_value = MagicMock()
        otel_kwargs = {}
        _append_spectra_components(True, otel_kwargs, protocol="http")
        mock_exporter_cls.assert_called_once()
        call_kwargs = mock_exporter_cls.call_args
        self.assertIn("localhost:4318", str(call_kwargs))
        self.assertEqual(len(otel_kwargs.get("span_processors", [])), 1)

    @patch(
        "opentelemetry.exporter.otlp.proto.http.trace_exporter.OTLPSpanExporter",
    )
    def test_http_custom_endpoint(self, mock_exporter_cls):
        """Custom endpoint is forwarded to the exporter."""
        mock_exporter_cls.return_value = MagicMock()
        _append_spectra_components(True, {}, protocol="http", endpoint="http://spectra.local:9999")
        call_kwargs = mock_exporter_cls.call_args
        self.assertEqual(call_kwargs[1]["endpoint"], "http://spectra.local:9999")

    @patch(
        "opentelemetry.exporter.otlp.proto.http.trace_exporter.OTLPSpanExporter",
    )
    def test_env_var_endpoint_used_as_fallback(self, mock_exporter_cls):
        """SPECTRA_ENDPOINT env var is used when no kwarg provided."""
        mock_exporter_cls.return_value = MagicMock()
        with patch.dict("os.environ", {"SPECTRA_ENDPOINT": "http://env-spectra:4318"}):
            _append_spectra_components(True, {}, protocol="http")
        call_kwargs = mock_exporter_cls.call_args
        self.assertEqual(call_kwargs[1]["endpoint"], "http://env-spectra:4318")

    @patch(
        "opentelemetry.exporter.otlp.proto.http.trace_exporter.OTLPSpanExporter",
    )
    def test_env_var_protocol(self, mock_exporter_cls):
        """SPECTRA_PROTOCOL env var selects protocol when kwarg not provided."""
        mock_exporter_cls.return_value = MagicMock()
        with patch.dict("os.environ", {"SPECTRA_PROTOCOL": "http"}):
            _append_spectra_components(True, {})
        mock_exporter_cls.assert_called_once()

    def test_no_exporter_packages_skips_gracefully(self):
        """When neither gRPC nor HTTP packages are installed, no crash."""
        otel_kwargs = {}
        with patch.dict(
            "sys.modules",
            {
                "opentelemetry.exporter.otlp.proto.grpc": None,
                "opentelemetry.exporter.otlp.proto.grpc.trace_exporter": None,
                "opentelemetry.exporter.otlp.proto.http": None,
                "opentelemetry.exporter.otlp.proto.http.trace_exporter": None,
            },
        ):
            _append_spectra_components(True, otel_kwargs, protocol="grpc")
        self.assertEqual(otel_kwargs.get("span_processors", []), [])

    @patch("microsoft.opentelemetry._distro._append_spectra_components")
    def test_spectra_kwargs_forwarded_from_entry_point(self, spectra_mock):
        """Spectra kwargs are parsed and forwarded from use_microsoft_opentelemetry."""
        use_microsoft_opentelemetry(
            enable_spectra=True,
            spectra_endpoint="http://my-sidecar:4317",
            spectra_protocol="grpc",
            spectra_insecure=False,
        )
        spectra_mock.assert_called_once()
        _, kwargs = spectra_mock.call_args
        self.assertEqual(kwargs["endpoint"], "http://my-sidecar:4317")
        self.assertEqual(kwargs["protocol"], "grpc")
        self.assertEqual(kwargs["insecure"], False)

    @patch("microsoft.opentelemetry._distro._append_spectra_components")
    def test_spectra_disabled_by_default(self, spectra_mock):
        """Spectra is disabled by default."""
        use_microsoft_opentelemetry()
        spectra_mock.assert_called_once()
        self.assertFalse(spectra_mock.call_args[0][0])

    @patch("microsoft.opentelemetry._distro._append_spectra_components")
    def test_spectra_kwargs_not_leaked_to_otel(self, spectra_mock):
        """Spectra kwargs are consumed and not forwarded to OTel kwargs."""
        use_microsoft_opentelemetry(
            enable_spectra=True,
            spectra_endpoint="http://localhost:4317",
        )
        otel_kwargs = spectra_mock.call_args[0][1]
        self.assertNotIn("enable_spectra", otel_kwargs)
        self.assertNotIn("spectra_endpoint", otel_kwargs)
        self.assertNotIn("spectra_protocol", otel_kwargs)
        self.assertNotIn("spectra_insecure", otel_kwargs)


class TestA365DisablesWebInstrumentations(unittest.TestCase):
    """When enable_a365=True, web/DB instrumentations are off by default."""

    _WEB_DB_LIBS = _A365_DISABLED_INSTRUMENTATIONS
    _GENAI_LIBS = tuple(lib for lib in _SUPPORTED_INSTRUMENTED_LIBRARIES if lib not in _A365_DISABLED_INSTRUMENTATIONS)

    @patch("microsoft.opentelemetry._distro._setup_instrumentations")
    @patch("microsoft.opentelemetry._distro._setup_logging")
    @patch("microsoft.opentelemetry._distro._setup_metrics")
    @patch("microsoft.opentelemetry._distro._setup_tracing")
    def test_web_db_disabled_when_a365_enabled(self, _trc, _met, _log, setup_inst):
        """Web/DB libs are disabled by default when A365 is on."""
        use_microsoft_opentelemetry(enable_a365=True)
        otel_kwargs = setup_inst.call_args[0][0]
        for lib in self._WEB_DB_LIBS:
            self.assertFalse(
                _is_instrumentation_enabled(otel_kwargs, lib),
                f"{lib} should be disabled when enable_a365=True",
            )

    @patch("microsoft.opentelemetry._distro._setup_instrumentations")
    @patch("microsoft.opentelemetry._distro._setup_logging")
    @patch("microsoft.opentelemetry._distro._setup_metrics")
    @patch("microsoft.opentelemetry._distro._setup_tracing")
    def test_genai_still_enabled_when_a365_enabled(self, _trc, _met, _log, setup_inst):
        """GenAI libs remain enabled when A365 is on."""
        use_microsoft_opentelemetry(enable_a365=True)
        otel_kwargs = setup_inst.call_args[0][0]
        for lib in self._GENAI_LIBS:
            self.assertTrue(
                _is_instrumentation_enabled(otel_kwargs, lib),
                f"{lib} should still be enabled when enable_a365=True",
            )

    @patch("microsoft.opentelemetry._distro._setup_instrumentations")
    @patch("microsoft.opentelemetry._distro._setup_logging")
    @patch("microsoft.opentelemetry._distro._setup_metrics")
    @patch("microsoft.opentelemetry._distro._setup_tracing")
    def test_user_can_override_disabled_lib(self, _trc, _met, _log, setup_inst):
        """User can explicitly re-enable a web lib even with A365 on."""
        use_microsoft_opentelemetry(
            enable_a365=True,
            instrumentation_options={"django": {"enabled": True}},
        )
        otel_kwargs = setup_inst.call_args[0][0]
        self.assertTrue(
            _is_instrumentation_enabled(otel_kwargs, "django"),
            "django should be enabled when user explicitly overrides",
        )
        # Other web libs should still be disabled
        self.assertFalse(
            _is_instrumentation_enabled(otel_kwargs, "flask"),
            "flask should still be disabled",
        )

    @patch("microsoft.opentelemetry._distro._setup_instrumentations")
    @patch("microsoft.opentelemetry._distro._setup_logging")
    @patch("microsoft.opentelemetry._distro._setup_metrics")
    @patch("microsoft.opentelemetry._distro._setup_tracing")
    def test_no_effect_when_a365_disabled(self, _trc, _met, _log, setup_inst):
        """Without enable_a365, all instrumentations remain enabled by default."""
        use_microsoft_opentelemetry()
        otel_kwargs = setup_inst.call_args[0][0]
        for lib in self._WEB_DB_LIBS:
            self.assertTrue(
                _is_instrumentation_enabled(otel_kwargs, lib),
                f"{lib} should be enabled when a365 is off",
            )

    @patch("microsoft.opentelemetry._distro._append_azure_monitor_components", return_value=(None, None, None))
    @patch("microsoft.opentelemetry._distro._setup_instrumentations")
    @patch("microsoft.opentelemetry._distro._setup_logging")
    @patch("microsoft.opentelemetry._distro._setup_metrics")
    @patch("microsoft.opentelemetry._distro._setup_tracing")
    def test_a365_defaults_skipped_when_azure_monitor_also_enabled(self, _trc, _met, _log, setup_inst, _az):
        """When both enable_a365 and enable_azure_monitor are True, the
        non-A365 (original) defaults are preserved so web/HTTP libs stay on."""
        use_microsoft_opentelemetry(
            enable_a365=True,
            enable_azure_monitor=True,
            azure_monitor_connection_string=TEST_CONNECTION_STRING,
        )
        otel_kwargs = setup_inst.call_args[0][0]
        for lib in self._WEB_DB_LIBS:
            self.assertTrue(
                _is_instrumentation_enabled(otel_kwargs, lib),
                f"{lib} should remain enabled when both a365 and azure_monitor are on",
            )


class TestGenAIMainAgentProcessorRegistration(unittest.TestCase):
    """The GenAI main-agent processors must register only when Azure Monitor is enabled."""

    def _captured_kwargs(self, append_mock):
        otel_kwargs, _ = append_mock.call_args[0]
        return otel_kwargs

    @patch("microsoft.opentelemetry._distro._append_azure_monitor_components", return_value=(None, None, None))
    def test_processors_registered_when_azure_monitor_enabled(self, append_mock):
        from microsoft.opentelemetry._genai.main_agent import (
            GenAIMainAgentLogRecordProcessor,
            GenAIMainAgentSpanProcessor,
        )

        use_microsoft_opentelemetry(
            azure_monitor_connection_string=TEST_CONNECTION_STRING,
            enable_azure_monitor=True,
        )

        otel_kwargs = self._captured_kwargs(append_mock)
        span_processors = otel_kwargs.get("span_processors") or []
        log_processors = otel_kwargs.get("log_record_processors") or []
        self.assertTrue(span_processors and isinstance(span_processors[0], GenAIMainAgentSpanProcessor))
        self.assertTrue(log_processors and isinstance(log_processors[0], GenAIMainAgentLogRecordProcessor))

    @patch("microsoft.opentelemetry._distro._setup_logging")
    @patch("microsoft.opentelemetry._distro._setup_metrics")
    @patch("microsoft.opentelemetry._distro._setup_tracing")
    def test_processors_not_registered_when_azure_monitor_disabled(
        self, tracing_mock, metrics_mock, logging_mock
    ):
        from microsoft.opentelemetry._genai.main_agent import (
            GenAIMainAgentLogRecordProcessor,
            GenAIMainAgentSpanProcessor,
        )

        use_microsoft_opentelemetry()

        otel_kwargs = tracing_mock.call_args[0][1]
        span_processors = otel_kwargs.get("span_processors") or []
        log_processors = otel_kwargs.get("log_record_processors") or []
        self.assertFalse(any(isinstance(p, GenAIMainAgentSpanProcessor) for p in span_processors))
        self.assertFalse(any(isinstance(p, GenAIMainAgentLogRecordProcessor) for p in log_processors))

    @patch("microsoft.opentelemetry._distro._append_a365_components")
    @patch("microsoft.opentelemetry._distro._setup_logging")
    @patch("microsoft.opentelemetry._distro._setup_metrics")
    @patch("microsoft.opentelemetry._distro._setup_tracing")
    def test_processors_not_registered_for_a365_only(
        self, tracing_mock, metrics_mock, logging_mock, a365_mock
    ):
        from microsoft.opentelemetry._genai.main_agent import (
            GenAIMainAgentLogRecordProcessor,
            GenAIMainAgentSpanProcessor,
        )

        use_microsoft_opentelemetry(enable_a365=True)

        otel_kwargs = tracing_mock.call_args[0][1]
        span_processors = otel_kwargs.get("span_processors") or []
        log_processors = otel_kwargs.get("log_record_processors") or []
        self.assertFalse(any(isinstance(p, GenAIMainAgentSpanProcessor) for p in span_processors))
        self.assertFalse(any(isinstance(p, GenAIMainAgentLogRecordProcessor) for p in log_processors))

    @patch("microsoft.opentelemetry._distro._append_azure_monitor_components", return_value=(None, None, None))
    def test_processors_skipped_when_signals_disabled(self, append_mock):
        from microsoft.opentelemetry._genai.main_agent import (
            GenAIMainAgentLogRecordProcessor,
            GenAIMainAgentSpanProcessor,
        )

        use_microsoft_opentelemetry(
            azure_monitor_connection_string=TEST_CONNECTION_STRING,
            enable_azure_monitor=True,
            disable_tracing=True,
            disable_logging=True,
        )

        otel_kwargs = self._captured_kwargs(append_mock)
        span_processors = otel_kwargs.get("span_processors") or []
        log_processors = otel_kwargs.get("log_record_processors") or []
        self.assertFalse(any(isinstance(p, GenAIMainAgentSpanProcessor) for p in span_processors))
        self.assertFalse(any(isinstance(p, GenAIMainAgentLogRecordProcessor) for p in log_processors))


if __name__ == "__main__":
    unittest.main()
