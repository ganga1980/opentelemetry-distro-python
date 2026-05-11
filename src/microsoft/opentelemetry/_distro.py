# -------------------------------------------------------------------------
# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License. See License.txt in the project root for
# license information.
# --------------------------------------------------------------------------
import os
from functools import cached_property
from logging import getLogger, Formatter
from typing import Any, Dict, List, Optional

from opentelemetry.metrics import set_meter_provider
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import MetricReader
from opentelemetry.sdk.metrics.view import View
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk._logs import LoggerProvider
from opentelemetry._logs import set_logger_provider
from opentelemetry.instrumentation.logging.handler import LoggingHandler
from opentelemetry.trace import set_tracer_provider
from opentelemetry.util._importlib_metadata import (
    EntryPoint,
    distributions,
    entry_points,
)

from microsoft.opentelemetry._constants import (
    DISABLE_LOGGING_ARG,
    DISABLE_METRICS_ARG,
    DISABLE_TRACING_ARG,
    ENABLE_A365_ARG,
    ENABLE_SENSITIVE_DATA_ARG,
    ENABLE_SPECTRA_ARG,
    SPECTRA_ENDPOINT_ARG,
    SPECTRA_PROTOCOL_ARG,
    SPECTRA_INSECURE_ARG,
    A365_TOKEN_RESOLVER_ARG,
    A365_CLUSTER_CATEGORY_ARG,
    A365_USE_S2S_ENDPOINT_ARG,
    A365_SUPPRESS_INVOKE_AGENT_INPUT_ARG,
    A365_ENABLE_OBSERVABILITY_EXPORTER_ARG,
    A365_OBSERVABILITY_SCOPE_OVERRIDE_ARG,
    A365_MAX_QUEUE_SIZE_ARG,
    A365_SCHEDULED_DELAY_MS_ARG,
    A365_EXPORTER_TIMEOUT_MS_ARG,
    A365_MAX_EXPORT_BATCH_SIZE_ARG,
    ENABLE_AZURE_MONITOR_ARG,
    ENABLE_CONSOLE_ARG,
    INSTRUMENTATION_OPTIONS_ARG,
    LOGGER_NAME_ARG,
    LOGGING_FORMATTER_ARG,
    LOG_RECORD_PROCESSORS_ARG,
    METRIC_READERS_ARG,
    RESOURCE_ARG,
    SPAN_PROCESSORS_ARG,
    VIEWS_ARG,
    _A365_DISABLED_INSTRUMENTATIONS,
    _AZURE_MONITOR_KWARG_MAP,
    _SUPPORTED_INSTRUMENTED_LIBRARIES,
    _SPECTRA_DEFAULT_GRPC_ENDPOINT,
    _SPECTRA_DEFAULT_HTTP_ENDPOINT,
    _SPECTRA_ENDPOINT_ENV,
    _SPECTRA_PROTOCOL_ENV,
    MICROSOFT_OPENTELEMETRY_VERSION_ENV,
)
from microsoft.opentelemetry._genai.main_agent import (
    GenAIMainAgentLogRecordProcessor,
    GenAIMainAgentSpanProcessor,
)
from microsoft.opentelemetry._instrumentation import get_dist_dependency_conflicts
from microsoft.opentelemetry._otlp import is_otlp_enabled
from microsoft.opentelemetry._sdkstats._state import (
    SdkStatsFeature,
    get_sdkstats_feature_flags,
    get_sdkstats_instrumentation_flags,
    is_sdkstats_enabled,
    set_sdkstats_feature,
    set_sdkstats_instrumentation_by_name,
)
from microsoft.opentelemetry._utils import (
    _append_azure_monitor_components,
    _append_console_components,
    _append_otlp_components,
)
from microsoft.opentelemetry._version import VERSION

import azure.monitor.opentelemetry.exporter._utils as _exporter_utils
from azure.monitor.opentelemetry.exporter.statsbeat._statsbeat_metrics import (
    _StatsbeatMetrics,
)

_logger = getLogger(__name__)


def use_microsoft_opentelemetry(**kwargs: object) -> None:  # pylint: disable=too-many-statements
    """Configure OpenTelemetry with optional Azure Monitor support.

    This function sets up the OpenTelemetry global providers
    (TracerProvider, MeterProvider, LoggerProvider) and optionally
    configures Azure Monitor as an exporter.  Azure Monitor is off
    by default: enable it via ``enable_azure_monitor=True`` and
    provide a connection string.

    :keyword bool enable_azure_monitor:
        Enable Azure Monitor export.
        Defaults to False. Set to True to enable Azure Monitor setup.
    :keyword str azure_monitor_connection_string:
        Connection string for Application Insights resource.
    :keyword azure_monitor_exporter_credential:
        Azure AD token credential for authentication.
    :keyword bool azure_monitor_enable_live_metrics:
        Enable live metrics. Defaults to True.
    :keyword bool azure_monitor_enable_performance_counters:
        Enable performance counters. Defaults to True.
    :keyword bool azure_monitor_exporter_disable_offline_storage:
        Disable offline retry storage. Defaults to False.
    :keyword str azure_monitor_exporter_storage_directory:
        Custom directory for offline telemetry storage.
    :keyword dict azure_monitor_browser_sdk_loader_config:
        Browser SDK loader configuration.
    :keyword bool disable_logging:
        Disable the logging pipeline. Defaults to False.
    :keyword bool disable_tracing:
        Disable the tracing pipeline. Defaults to False.
    :keyword bool disable_metrics:
        Disable the metrics pipeline. Defaults to False.
    :keyword resource: OpenTelemetry Resource.
    :keyword list span_processors: Additional span processors.
    :keyword list log_record_processors:
        Additional log record processors.
    :keyword list metric_readers: Additional metric readers.
    :keyword list views: Metric views.
    :keyword str logger_name: Logger name for log collection.
    :keyword logging_formatter: Formatter for collected logs.
    :keyword dict instrumentation_options:
        Per-library instrumentation enable/disable options.
    :keyword bool enable_trace_based_sampling_for_logs:
        Enable trace-based sampling for logs.
    :keyword bool enable_a365:
        Enable Agent365 trace export. Defaults to False.
    :keyword a365_token_resolver:
        Optional callable ``(agent_id: str, tenant_id: str) -> str | None``
        used to authenticate with the Agent365 endpoint.  When omitted,
        ``DefaultAzureCredential`` is used.
    :keyword str a365_cluster_category:
        Cluster category for endpoint discovery. Also read from ``A365_CLUSTER_CATEGORY``
        env var. Defaults to ``"prod"``.
    :keyword bool a365_use_s2s_endpoint:
        Use the S2S endpoint. Also read from ``A365_USE_S2S_ENDPOINT`` env var.
        Defaults to False.
    :keyword bool a365_suppress_invoke_agent_input:
        Strip input messages from InvokeAgent spans before export. Also read from
        ``A365_SUPPRESS_INVOKE_AGENT_INPUT`` env var. Defaults to False.
    :keyword bool a365_enable_observability_exporter:
        Enable the A365 HTTP observability exporter. Also read from
        ``ENABLE_A365_OBSERVABILITY_EXPORTER`` env var. Defaults to False.
        Has no effect unless ``enable_a365=True``.
    :keyword str a365_observability_scope_override:
        Override the authentication scope used when acquiring tokens for the
        A365 observability service. Equivalent to setting the
        ``A365_OBSERVABILITY_SCOPE_OVERRIDE`` environment variable. When provided,
        this kwarg overrides the env var.
    :keyword int a365_max_queue_size:
        Maximum queue size for the A365 batch span processor. Defaults to 2048
        when omitted (BatchSpanProcessor default).
    :keyword int a365_scheduled_delay_ms:
        Delay between A365 export batches in milliseconds. Defaults to 5000
        when omitted (BatchSpanProcessor default).
    :keyword int a365_exporter_timeout_ms:
        Timeout for a single A365 export operation in milliseconds. Defaults to
        30000 when omitted (BatchSpanProcessor default).
    :keyword int a365_max_export_batch_size:
        Maximum batch size for a single A365 export operation. Defaults to 512
        when omitted (BatchSpanProcessor default).
    :keyword bool enable_console:
        Enable console exporter for traces, metrics, and logs (development
        only).  Mirrors ``ExportTarget.Console`` from the .NET distro.
        Auto-enables when no other exporter is active (Azure Monitor off,
        OTLP off, A365 off).  Defaults to False.
    :keyword bool enable_spectra:
        Enable Spectra Collector sidecar export via OTLP. Defaults to False.
        Requires ``opentelemetry-exporter-otlp-proto-grpc`` for gRPC protocol;
        falls back to HTTP if gRPC is unavailable. Logs a warning and skips
        if neither exporter package is installed.
    :keyword str spectra_endpoint:
        Spectra sidecar OTLP endpoint. Also read from ``SPECTRA_ENDPOINT`` env var.
        Defaults to ``http://localhost:4317`` for gRPC or ``http://localhost:4318``
        for HTTP.
    :keyword str spectra_protocol:
        OTLP protocol for Spectra — ``"grpc"`` or ``"http"``. Also read from
        ``SPECTRA_PROTOCOL`` env var. Defaults to ``"grpc"``.
    :keyword bool spectra_insecure:
        Use insecure (no TLS) connection. Defaults to True (localhost sidecar).
    :keyword bool enable_sensitive_data:
        Enable sensitive data recording (prompts, tool arguments, results) for
        the Agent Framework SDK instrumentation. Defaults to False.
    :rtype: None
    """

    os.environ[MICROSOFT_OPENTELEMETRY_VERSION_ENV] = VERSION
    enable_azure_monitor: bool = bool(kwargs.pop(ENABLE_AZURE_MONITOR_ARG, False))
    enable_console: bool = bool(kwargs.pop(ENABLE_CONSOLE_ARG, False))
    enable_a365: bool = bool(kwargs.pop(ENABLE_A365_ARG, False))
    a365_token_resolver = kwargs.pop(A365_TOKEN_RESOLVER_ARG, None)
    a365_cluster_category = kwargs.pop(A365_CLUSTER_CATEGORY_ARG, None)
    a365_use_s2s_endpoint = kwargs.pop(A365_USE_S2S_ENDPOINT_ARG, None)
    a365_suppress_invoke_agent_input = kwargs.pop(A365_SUPPRESS_INVOKE_AGENT_INPUT_ARG, None)
    a365_enable_observability_exporter = kwargs.pop(A365_ENABLE_OBSERVABILITY_EXPORTER_ARG, None)
    a365_observability_scope_override = kwargs.pop(A365_OBSERVABILITY_SCOPE_OVERRIDE_ARG, None)
    a365_max_queue_size = kwargs.pop(A365_MAX_QUEUE_SIZE_ARG, None)
    a365_scheduled_delay_ms = kwargs.pop(A365_SCHEDULED_DELAY_MS_ARG, None)
    a365_exporter_timeout_ms = kwargs.pop(A365_EXPORTER_TIMEOUT_MS_ARG, None)
    a365_max_export_batch_size = kwargs.pop(A365_MAX_EXPORT_BATCH_SIZE_ARG, None)

    enable_spectra: bool = bool(kwargs.pop(ENABLE_SPECTRA_ARG, False))
    spectra_endpoint = kwargs.pop(SPECTRA_ENDPOINT_ARG, None)
    spectra_protocol = kwargs.pop(SPECTRA_PROTOCOL_ARG, None)
    spectra_insecure = kwargs.pop(SPECTRA_INSECURE_ARG, None)

    enable_sensitive_data: bool = bool(kwargs.pop(ENABLE_SENSITIVE_DATA_ARG, False))

    # Separate Azure Monitor kwargs from generic OTel kwargs
    otel_kwargs: Dict[str, Any] = {k: v for k, v in kwargs.items() if k not in _AZURE_MONITOR_KWARG_MAP}
    azure_monitor_kwargs: Dict[str, Any] = {
        _AZURE_MONITOR_KWARG_MAP[k]: v for k, v in kwargs.items() if k in _AZURE_MONITOR_KWARG_MAP
    }  # pylint: disable=line-too-long

    # When A365 is enabled (and Azure Monitor is NOT enabled), disable
    # web-framework / HTTP-client instrumentations by default.  The user can
    # still override by explicitly setting
    # ``instrumentation_options={"django": {"enabled": True}}``.
    if enable_a365 and not enable_azure_monitor:
        inst_opts = otel_kwargs.get(INSTRUMENTATION_OPTIONS_ARG) or {}
        if not isinstance(inst_opts, dict):
            _logger.error(
                "%s must be a dict, got %s; ignoring user value and using defaults.",
                INSTRUMENTATION_OPTIONS_ARG,
                type(inst_opts).__name__,
            )
            inst_opts = {}
        for lib in _A365_DISABLED_INSTRUMENTATIONS:
            inst_opts.setdefault(lib, {}).setdefault("enabled", False)
        otel_kwargs[INSTRUMENTATION_OPTIONS_ARG] = inst_opts

    # ---- SDKStats: record distro feature flag ----
    set_sdkstats_feature(SdkStatsFeature.DISTRO)

    # ---- GenAI main-agent attribute propagation ----
    # Prepended to the processor lists so on_start/on_emit run BEFORE any
    # Batch* export processor appended below; this enriches once per
    # span/log and is then visible to the Azure Monitor exporter. 
    if enable_azure_monitor:
        if not otel_kwargs.get(DISABLE_TRACING_ARG, False):
            otel_kwargs[SPAN_PROCESSORS_ARG] = [
                GenAIMainAgentSpanProcessor(),
                *list(otel_kwargs.get(SPAN_PROCESSORS_ARG) or []),
            ]
        if not otel_kwargs.get(DISABLE_LOGGING_ARG, False):
            otel_kwargs[LOG_RECORD_PROCESSORS_ARG] = [
                GenAIMainAgentLogRecordProcessor(),
                *list(otel_kwargs.get(LOG_RECORD_PROCESSORS_ARG) or []),
            ]

    # ---- OTLP exporters (append to user-supplied processors/readers) ----
    _append_otlp_components(otel_kwargs)
    if is_otlp_enabled():
        set_sdkstats_feature(SdkStatsFeature.OTLP_EXPORT)

    # ---- Spectra sidecar exporter (OTLP gRPC/HTTP to localhost) ----
    _append_spectra_components(
        enable_spectra,
        otel_kwargs,
        endpoint=spectra_endpoint,
        protocol=spectra_protocol,
        insecure=spectra_insecure,
    )

    # ---- A365 exporters (append span processors — traces only) ----
    _append_a365_components(
        enable_a365,
        otel_kwargs,
        token_resolver=a365_token_resolver,
        cluster_category=a365_cluster_category,
        use_s2s_endpoint=a365_use_s2s_endpoint,
        suppress_invoke_agent_input=a365_suppress_invoke_agent_input,
        enable_observability_exporter=a365_enable_observability_exporter,
        observability_scope_override=a365_observability_scope_override,
        max_queue_size=a365_max_queue_size,
        scheduled_delay_ms=a365_scheduled_delay_ms,
        exporter_timeout_ms=a365_exporter_timeout_ms,
        max_export_batch_size=a365_max_export_batch_size,
    )

    # ---- Console exporters (dev-only, mirrors ExportTarget.Console) ----
    # Auto-enable when no other exporter destination is active.
    if not enable_console and not enable_azure_monitor and not enable_a365 and not is_otlp_enabled():
        enable_console = True
    _append_console_components(otel_kwargs, enable_console)
    if enable_console:
        set_sdkstats_feature(SdkStatsFeature.CONSOLE_EXPORT)

    # ---- Build and register providers ----
    tracer_provider: Optional[TracerProvider] = None
    meter_provider: Optional[MeterProvider] = None
    logger_provider: Optional[LoggerProvider] = None

    if enable_azure_monitor:
        tracer_provider, meter_provider, logger_provider = _append_azure_monitor_components(
            otel_kwargs, azure_monitor_kwargs
        )

    # If Azure Monitor was disabled or failed to create a provider for a
    # signal, fall back to creating a plain provider from otel_kwargs so
    # that OTLP (and any user-supplied processors) still work.
    resource = otel_kwargs.get(RESOURCE_ARG) or Resource.create()
    disable_tracing = otel_kwargs.get(DISABLE_TRACING_ARG, False)
    disable_logging = otel_kwargs.get(DISABLE_LOGGING_ARG, False)
    disable_metrics = otel_kwargs.get(DISABLE_METRICS_ARG, False)

    # When Azure Monitor is enabled, its _setup_* functions already create
    # providers that include OTLP + user-supplied components, so the checks
    # below are no-ops. These only run when Azure Monitor is disabled or
    # its setup failed, to create bare providers and register them.
    if tracer_provider is None and not disable_tracing:
        tracer_provider = _setup_tracing(resource, otel_kwargs)
    if meter_provider is None and not disable_metrics:
        meter_provider = _setup_metrics(resource, otel_kwargs)
    if logger_provider is None and not disable_logging:
        logger_provider = _setup_logging(resource, otel_kwargs)

    # Register the created providers as the OTel global singletons
    if tracer_provider is not None:
        set_tracer_provider(tracer_provider)
    if meter_provider is not None:
        set_meter_provider(meter_provider)
    if logger_provider is not None:
        set_logger_provider(logger_provider)

    # ---- Instrumentations (always, after providers are set) ----
    _setup_instrumentations(otel_kwargs, **{ENABLE_SENSITIVE_DATA_ARG: enable_sensitive_data})

    # ---- SDKStats manager (after providers, before returning) ----
    _initialize_sdkstats(enable_azure_monitor)

    if enable_azure_monitor:
        _logger.info("Azure Monitor enabled.")


def _env_bool(name: str, default: bool = False) -> bool:
    """Read a boolean from an environment variable."""
    val = os.environ.get(name, "").strip().lower()
    if not val:
        return default
    return val in ("true", "1", "yes", "on")


def _initialize_sdkstats(enable_azure_monitor: bool) -> None:
    """Set up SDKStats — always sends to the Application Insights statsbeat endpoint.

    When Azure Monitor is active the exporter package's own StatsbeatManager
    handles everything and we bridge our distro-level feature/instrumentation
    bits into its state so it reports the full picture.  For A365-only,
    OTLP-only, or Console-only customers this function creates a standalone
    pipeline using ``AzureMonitorMetricExporter(is_sdkstats=True)`` pointed
    at the well-known statsbeat ingestion endpoint.  The customer's telemetry
    pipeline is not affected.
    """
    if not is_sdkstats_enabled():
        return

    if enable_azure_monitor:
        # The exporter package runs its own statsbeat.  Bridge our
        # distro-level feature bits (A365_EXPORT, OTLP_EXPORT, etc.)
        # and instrumentation bits into the exporter's state so they
        # appear in the same observation.  Our bit values (128+) do
        # not collide with the exporter's (1–64).
        _bridge_sdkstats_to_azure_monitor()
        return

    from microsoft.opentelemetry._sdkstats._manager import SdkStatsManager

    manager = SdkStatsManager()
    manager.initialize()


def _bridge_sdkstats_to_azure_monitor() -> None:
    """OR distro feature/instrumentation bits into the exporter's statsbeat."""
    # Feature bits — OR our flags into the class-level dict that the
    # exporter's _get_feature_metric callback reads each cycle.
    feature_flags = get_sdkstats_feature_flags()
    if feature_flags:
        current = _StatsbeatMetrics._FEATURE_ATTRIBUTES.get("feature") or 0
        _StatsbeatMetrics._FEATURE_ATTRIBUTES["feature"] = current | feature_flags

    # Instrumentation bits — OR directly into the exporter's module-
    # level bitmask (thread-safe via their lock).
    instrumentation_flags = get_sdkstats_instrumentation_flags()
    if instrumentation_flags:
        with _exporter_utils._INSTRUMENTATIONS_BIT_MASK_LOCK:
            _exporter_utils._INSTRUMENTATIONS_BIT_MASK |= instrumentation_flags


def _append_a365_components(
    enable_a365: bool,
    otel_kwargs: Dict[str, Any],
    token_resolver: Any = None,
    cluster_category: Any = None,
    use_s2s_endpoint: Any = None,
    suppress_invoke_agent_input: Any = None,
    enable_observability_exporter: Any = None,
    observability_scope_override: Any = None,
    max_queue_size: Any = None,
    scheduled_delay_ms: Any = None,
    exporter_timeout_ms: Any = None,
    max_export_batch_size: Any = None,
) -> None:
    """Build and append Agent365 span processors to ``otel_kwargs``.

    A365 only produces span processors (traces).  They are added to the
    same list that the distro uses when creating the TracerProvider, so
    the distro registers a single provider for all exporters.

    Kwargs take precedence over environment variables.
    """
    if not enable_a365:
        return

    disable_tracing = otel_kwargs.get(DISABLE_TRACING_ARG, False)
    if disable_tracing:
        return

    # Tell scope classes that telemetry is enabled without env vars.
    # The standalone SDK gates on ENABLE_OBSERVABILITY / ENABLE_A365_OBSERVABILITY
    # env vars, but when the distro is told enable_a365=True that's sufficient.
    from microsoft.opentelemetry.a365.core.opentelemetry_scope import OpenTelemetryScope

    OpenTelemetryScope._enabled_by_distro = True

    from microsoft.opentelemetry.a365.constants import (
        A365_CLUSTER_CATEGORY_ENV,
        A365_OBSERVABILITY_SCOPE_OVERRIDE_ENV,
        A365_USE_S2S_ENDPOINT_ENV,
        A365_SUPPRESS_INVOKE_AGENT_INPUT_ENV,
        ENABLE_A365_OBSERVABILITY_EXPORTER,
    )
    from microsoft.opentelemetry.a365.core.exporters.agent365_exporter import _Agent365Exporter
    from microsoft.opentelemetry.a365.core.exporters.enriching_span_processor import (
        _EnrichingBatchSpanProcessor,
    )
    from microsoft.opentelemetry.a365.core.exporters.span_processor import A365SpanProcessor
    from microsoft.opentelemetry.a365.core.exporters.utils import (
        _create_default_token_resolver,
    )

    try:
        # Baggage-to-span attribute propagation (gen_ai.agent.id,
        # microsoft.tenant.id, user.name, etc.).  Always registered
        # when enable_a365=True so enriched attributes appear on spans
        # regardless of whether the A365 exporter is active.
        baggage_processor = A365SpanProcessor()

        otel_kwargs[SPAN_PROCESSORS_ARG] = list(otel_kwargs.get(SPAN_PROCESSORS_ARG) or [])
        otel_kwargs[SPAN_PROCESSORS_ARG].append(baggage_processor)

        set_sdkstats_feature(SdkStatsFeature.A365_EXPORT)

        # Resolve configuration: kwargs > env vars > defaults
        resolved_cluster_category = cluster_category or os.environ.get(A365_CLUSTER_CATEGORY_ENV, "prod")
        resolved_use_s2s = use_s2s_endpoint if use_s2s_endpoint is not None else _env_bool(A365_USE_S2S_ENDPOINT_ENV)
        resolved_suppress_input = (
            suppress_invoke_agent_input
            if suppress_invoke_agent_input is not None
            else _env_bool(A365_SUPPRESS_INVOKE_AGENT_INPUT_ENV)
        )
        resolved_enable_exporter = bool(enable_observability_exporter) or _env_bool(
            ENABLE_A365_OBSERVABILITY_EXPORTER, default=False
        )
        resolved_scope_override = (
            observability_scope_override
            if observability_scope_override is not None
            else os.environ.get(A365_OBSERVABILITY_SCOPE_OVERRIDE_ENV)
        )

        if not resolved_enable_exporter:
            _logger.info(
                "A365 observability exporter not enabled (set ``a365_enable_observability_exporter=True`` "
                "or ``ENABLE_A365_OBSERVABILITY_EXPORTER=true``); skipping."
            )
            return

        resolved_token_resolver = token_resolver or _create_default_token_resolver(
            scope_override=resolved_scope_override
        )

        exporter = _Agent365Exporter(
            token_resolver=resolved_token_resolver,
            cluster_category=resolved_cluster_category,
            use_s2s_endpoint=resolved_use_s2s,
        )

        # Enriching batch processor wrapping the exporter.
        # Only forward batch parameters when the user explicitly supplied
        # them so that BatchSpanProcessor uses its own defaults otherwise.
        batch_kwargs: Dict[str, Any] = {}
        if max_queue_size is not None:
            batch_kwargs["max_queue_size"] = max_queue_size
        if scheduled_delay_ms is not None:
            batch_kwargs["schedule_delay_millis"] = scheduled_delay_ms
        if exporter_timeout_ms is not None:
            batch_kwargs["export_timeout_millis"] = exporter_timeout_ms
        if max_export_batch_size is not None:
            batch_kwargs["max_export_batch_size"] = max_export_batch_size

        batch_processor = _EnrichingBatchSpanProcessor(
            exporter,
            suppress_invoke_agent_input=resolved_suppress_input,
            **batch_kwargs,
        )

        otel_kwargs[SPAN_PROCESSORS_ARG].append(batch_processor)

    except Exception:  # pylint: disable=broad-exception-caught
        _logger.exception("Failed to create A365 components.")


# ---------------------------------------------------------------------------
# Spectra Sidecar (OTLP gRPC / HTTP) support
# ---------------------------------------------------------------------------


def _append_spectra_components(
    enable_spectra: bool,
    otel_kwargs: Dict[str, Any],
    endpoint: Any = None,
    protocol: Any = None,
    insecure: Any = None,
) -> None:
    """Append a Spectra sidecar OTLP span processor to ``otel_kwargs``.

    The Spectra Collector runs as a Kubernetes sidecar accepting OTLP
    on localhost.  gRPC (port 4317) is preferred; falls back to HTTP
    (port 4318) if ``opentelemetry-exporter-otlp-proto-grpc`` is not
    installed.  Logs a warning and skips entirely when neither exporter
    package is available.
    """
    if not enable_spectra:
        return

    set_sdkstats_feature(SdkStatsFeature.SPECTRA_EXPORT)

    if otel_kwargs.get(DISABLE_TRACING_ARG, False):
        return

    raw_protocol = protocol or os.environ.get(_SPECTRA_PROTOCOL_ENV, "grpc")
    resolved_protocol = str(raw_protocol).strip().lower()
    resolved_insecure = insecure if insecure is not None else True
    resolved_endpoint = endpoint or os.environ.get(_SPECTRA_ENDPOINT_ENV)

    if resolved_protocol not in {"grpc", "http"}:
        _logger.error(
            "Invalid Spectra protocol %r (normalized: %r). Supported values are 'grpc' and 'http'. "
            "Spectra sidecar export is disabled.",
            raw_protocol,
            resolved_protocol,
        )
        return

    exporter = None

    if resolved_protocol == "grpc":
        try:
            from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter as GrpcSpanExporter

            grpc_endpoint = resolved_endpoint or _SPECTRA_DEFAULT_GRPC_ENDPOINT
            exporter = GrpcSpanExporter(endpoint=grpc_endpoint, insecure=resolved_insecure)
            _logger.info("Spectra sidecar exporter using gRPC at %s", grpc_endpoint)
        except ImportError:
            _logger.warning(
                "opentelemetry-exporter-otlp-proto-grpc is not installed. "
                "Falling back to HTTP protocol for Spectra sidecar."
            )
            resolved_protocol = "http"

    if resolved_protocol == "http":
        try:
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter as HttpSpanExporter

            http_endpoint = resolved_endpoint or _SPECTRA_DEFAULT_HTTP_ENDPOINT
            exporter = HttpSpanExporter(endpoint=http_endpoint)
            _logger.info("Spectra sidecar exporter using HTTP at %s", http_endpoint)
        except ImportError:
            _logger.warning(
                "No OTLP exporter package is installed. "
                "Spectra sidecar export is disabled. "
                "Install opentelemetry-exporter-otlp-proto-grpc or "
                "opentelemetry-exporter-otlp-proto-http."
            )
            return

    if exporter is None:
        return

    try:
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        processor = BatchSpanProcessor(exporter)
        otel_kwargs[SPAN_PROCESSORS_ARG] = list(otel_kwargs.get(SPAN_PROCESSORS_ARG) or [])
        otel_kwargs[SPAN_PROCESSORS_ARG].append(processor)
    except Exception:  # pylint: disable=broad-exception-caught
        _logger.exception("Failed to create Spectra sidecar span processor.")


# ---------------------------------------------------------------------------
# Core OTel provider setup
# ---------------------------------------------------------------------------


def _setup_tracing(
    resource: Resource,
    otel_kwargs: Dict[str, Any],
) -> TracerProvider:
    """Create a TracerProvider with user-supplied span processors."""
    tracer_provider = TracerProvider(resource=resource)
    for sp in otel_kwargs.get(SPAN_PROCESSORS_ARG) or []:
        tracer_provider.add_span_processor(sp)
    return tracer_provider


def _setup_metrics(
    resource: Resource,
    otel_kwargs: Dict[str, Any],
) -> MeterProvider:
    """Create a MeterProvider with user-supplied readers/views."""
    readers: List[MetricReader] = list(otel_kwargs.get(METRIC_READERS_ARG) or [])
    views: List[View] = list(otel_kwargs.get(VIEWS_ARG) or [])
    meter_provider = MeterProvider(
        metric_readers=readers,
        resource=resource,
        views=views,
    )
    return meter_provider


def _setup_logging(
    resource: Resource,
    otel_kwargs: Dict[str, Any],
) -> LoggerProvider | None:
    """Create a LoggerProvider with user-supplied processors."""
    logger_provider = LoggerProvider(resource=resource)
    for lrp in otel_kwargs.get(LOG_RECORD_PROCESSORS_ARG) or []:
        logger_provider.add_log_record_processor(lrp)

    logger_name: str = otel_kwargs.get(LOGGER_NAME_ARG, "")
    logging_formatter: Optional[Formatter] = otel_kwargs.get(LOGGING_FORMATTER_ARG)
    if logger_name:
        logger = getLogger(logger_name)
        if not any(isinstance(h, LoggingHandler) for h in logger.handlers):
            handler = LoggingHandler(logger_provider=logger_provider)
            if logging_formatter:
                handler.setFormatter(logging_formatter)
            logger.addHandler(handler)

    return logger_provider


# ---------------------------------------------------------------------------
# Instrumentations
# ---------------------------------------------------------------------------


class _EntryPointDistFinder:
    @cached_property
    def _mapping(self) -> Dict[str, Any]:
        return {self._key_for(ep): dist for dist in distributions() for ep in dist.entry_points}

    def dist_for(self, entry_point: EntryPoint) -> Any:
        dist = getattr(entry_point, "dist", None)
        if dist:
            return dist
        return self._mapping.get(self._key_for(entry_point))

    @staticmethod
    def _key_for(entry_point: EntryPoint) -> str:
        return f"{entry_point.group}:{entry_point.name}:{entry_point.value}"


def _is_instrumentation_enabled(otel_kwargs: Dict[str, Any], lib_name: str) -> bool:
    """Check if a library instrumentation is enabled via instrumentation_options."""
    options = otel_kwargs.get(INSTRUMENTATION_OPTIONS_ARG)
    if not options or lib_name not in options:
        # Default: enabled for supported libraries
        return True
    lib_options = options[lib_name]
    if "enabled" not in lib_options:
        return True
    return lib_options["enabled"] is True


def _setup_instrumentations(otel_kwargs: Dict[str, Any], **kwargs: Any) -> None:
    """Discover and activate OTel instrumentations for supported libraries."""
    entry_point_finder = _EntryPointDistFinder()
    for entry_point in entry_points(group="opentelemetry_instrumentor"):
        lib_name = entry_point.name
        if lib_name not in _SUPPORTED_INSTRUMENTED_LIBRARIES:
            continue
        if not _is_instrumentation_enabled(otel_kwargs, lib_name):
            _logger.debug("Instrumentation skipped for library %s", lib_name)
            continue
        try:
            entry_point_dist = entry_point_finder.dist_for(entry_point)  # type: ignore
            conflict = get_dist_dependency_conflicts(entry_point_dist)  # type: ignore
            if conflict:
                _logger.debug(
                    "Skipping instrumentation %s: %s",
                    entry_point.name,
                    conflict,
                )
                continue
            instrumentor: Any = entry_point.load()
            instrumentor().instrument(skip_dep_check=True, **kwargs)
            set_sdkstats_instrumentation_by_name(lib_name)
        except Exception as ex:  # pylint: disable=broad-except
            _logger.warning(
                "Exception occurred when instrumenting: %s.",
                lib_name,
                exc_info=ex,
            )
