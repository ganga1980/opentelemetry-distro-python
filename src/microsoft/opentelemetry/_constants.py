# -------------------------------------------------------------------------
# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License. See License.txt in the project root for
# license information.
# --------------------------------------------------------------------------

"""Shared constants for the Microsoft OpenTelemetry Distro.

Defines configuration argument names used by :func:`use_microsoft_opentelemetry`,
the mapping of public kwarg names to Azure Monitor internal keys, and the list
of supported auto-instrumented libraries.
"""

# --- Generic OTel Provider Constants ---

DISABLE_LOGGING_ARG = "disable_logging"
DISABLE_METRICS_ARG = "disable_metrics"
DISABLE_TRACING_ARG = "disable_tracing"
LOGGER_NAME_ARG = "logger_name"
LOGGING_FORMATTER_ARG = "logging_formatter"
LOG_RECORD_PROCESSORS_ARG = "log_record_processors"
METRIC_READERS_ARG = "metric_readers"
RESOURCE_ARG = "resource"
SPAN_PROCESSORS_ARG = "span_processors"
VIEWS_ARG = "views"
INSTRUMENTATION_OPTIONS_ARG = "instrumentation_options"
ENABLE_LIVE_METRICS_ARG = "enable_live_metrics"

# --- Instrumentation Constants ---

_SUPPORTED_INSTRUMENTED_LIBRARIES = (
    "django",
    "fastapi",
    "flask",
    "httpx",
    "psycopg2",
    "requests",
    "urllib",
    "urllib3",
    # GENAI
    "langchain",
    "openai",
    "openai_agents",
    "semantic_kernel",
    "agent_framework",
)

# Libraries disabled by default when A365 is enabled (agent workloads
# typically don't need web-framework / HTTP-client instrumentation).
_A365_DISABLED_INSTRUMENTATIONS = (
    "django",
    "fastapi",
    "flask",
    "httpx",
    "psycopg2",
    "requests",
    "urllib",
    "urllib3",
    "azure_sdk",
)

# --- Console Exporter Constants ---

ENABLE_CONSOLE_ARG = "enable_console"

# --- Microsoft Distro Constants ---

ENABLE_AZURE_MONITOR_ARG = "enable_azure_monitor"

# --- OTLP Environment Variable Constants ---

_OTEL_EXPORTER_OTLP_ENDPOINT = "OTEL_EXPORTER_OTLP_ENDPOINT"
_OTEL_EXPORTER_OTLP_TRACES_ENDPOINT = "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT"
_OTEL_EXPORTER_OTLP_METRICS_ENDPOINT = "OTEL_EXPORTER_OTLP_METRICS_ENDPOINT"
_OTEL_EXPORTER_OTLP_LOGS_ENDPOINT = "OTEL_EXPORTER_OTLP_LOGS_ENDPOINT"

# Mapping from azure_monitor_ prefixed public kwargs to internal
# configure_azure_monitor() kwargs.
_AZURE_MONITOR_KWARG_MAP = {
    "azure_monitor_connection_string": "connection_string",
    "azure_monitor_exporter_credential": "credential",
    "azure_monitor_enable_live_metrics": "enable_live_metrics",
    "azure_monitor_enable_performance_counters": "enable_performance_counters",
    "azure_monitor_exporter_disable_offline_storage": "disable_offline_storage",
    "azure_monitor_exporter_storage_directory": "storage_directory",
    "azure_monitor_browser_sdk_loader_config": "browser_sdk_loader_config",
}

# --- Spectra Sidecar Constants ---

ENABLE_SENSITIVE_DATA_ARG = "enable_sensitive_data"

ENABLE_SPECTRA_ARG = "enable_spectra"
SPECTRA_ENDPOINT_ARG = "spectra_endpoint"
SPECTRA_PROTOCOL_ARG = "spectra_protocol"
SPECTRA_INSECURE_ARG = "spectra_insecure"
_SPECTRA_ENDPOINT_ENV = "SPECTRA_ENDPOINT"
_SPECTRA_PROTOCOL_ENV = "SPECTRA_PROTOCOL"
_SPECTRA_DEFAULT_GRPC_ENDPOINT = "http://localhost:4317"
_SPECTRA_DEFAULT_HTTP_ENDPOINT = "http://localhost:4318"

# --- Agent365 Constants ---

ENABLE_A365_ARG = "enable_a365"
A365_TOKEN_RESOLVER_ARG = "a365_token_resolver"
A365_CLUSTER_CATEGORY_ARG = "a365_cluster_category"
A365_USE_S2S_ENDPOINT_ARG = "a365_use_s2s_endpoint"
A365_SUPPRESS_INVOKE_AGENT_INPUT_ARG = "a365_suppress_invoke_agent_input"
A365_ENABLE_OBSERVABILITY_EXPORTER_ARG = "a365_enable_observability_exporter"
A365_OBSERVABILITY_SCOPE_OVERRIDE_ARG = "a365_observability_scope_override"
A365_MAX_QUEUE_SIZE_ARG = "a365_max_queue_size"
A365_SCHEDULED_DELAY_MS_ARG = "a365_scheduled_delay_ms"
A365_EXPORTER_TIMEOUT_MS_ARG = "a365_exporter_timeout_ms"
A365_MAX_EXPORT_BATCH_SIZE_ARG = "a365_max_export_batch_size"

# --- GenAI Main Agent Constants ---

# Target attribute keys written by the GenAI main-agent processors so that
# downstream telemetry (spans + logs) is attributed to the user-facing
# ("main") agent rather than internal sub-agents in a multi-agent system.
GEN_AI_MAIN_AGENT_NAME_KEY = "microsoft.gen_ai.main_agent.name"
GEN_AI_MAIN_AGENT_ID_KEY = "microsoft.gen_ai.main_agent.id"
GEN_AI_MAIN_AGENT_VERSION_KEY = "microsoft.gen_ai.main_agent.version"
GEN_AI_MAIN_AGENT_CONVERSATION_ID_KEY = "microsoft.gen_ai.main_agent.conversation_id"
GEN_AI_MAIN_AGENT_ATTRIBUTE_PREFIX = "microsoft.gen_ai.main_agent."

# --- Version propagation for distro to exporter ---
MICROSOFT_OPENTELEMETRY_VERSION_ENV = "MICROSOFT_OPENTELEMETRY_VERSION"
