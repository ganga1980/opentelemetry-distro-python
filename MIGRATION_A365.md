---
description: "Use when migrating code from the standalone Agent365 Python SDK packages (microsoft-agents-a365-*) to the Microsoft OpenTelemetry Distro (microsoft-opentelemetry). Covers pip dependency changes, import path rewrites, configure() removal, exporter options, and environment variable mappings."
applyTo: "**/*.py"
---

# Agent365 Python SDK → Microsoft OpenTelemetry Distro Migration

> **Official docs:** [Microsoft OpenTelemetry on Microsoft Learn](https://learn.microsoft.com/en-us/microsoft-agent-365/developer/microsoft-opentelemetry?tabs=python)

Users are migrating from the standalone A365 observability PyPI packages under
`microsoft-agents-a365-observability-*` to a single distro package: `microsoft-opentelemetry`.

This migration covers the **observability** packages, along with the related **hosting** and **runtime** packages.
Other A365 packages, such as notifications and tooling, are not part of this distro.

After migrating, see [A365_DOCUMENTATION.md](A365_DOCUMENTATION.md) for the full distro usage guide (configuration, auto-instrumentation, token resolver, baggage, scope classes, troubleshooting).

The distro bundles:
- **`a365/core`** — Scope classes, span enrichment, A365 exporter, baggage middleware
- **`a365/hosting`** — Hosting middleware (baggage, output logging, invoke-agent scope helpers).
- **`a365/runtime`** — Standalone utilities (Power Platform API discovery, JWT token introspection, environment detection).

## Step 1 — Replace pip Dependencies

Remove the standalone A365 observability packages and install the distro:

```
# ❌ Remove old packages
pip uninstall -y microsoft-agents-a365-observability-core
pip uninstall -y microsoft-agents-a365-observability-hosting
pip uninstall -y microsoft-agents-a365-runtime
pip uninstall -y microsoft-agents-a365-observability-extensions-langchain
pip uninstall -y microsoft-agents-a365-observability-extensions-openai
pip uninstall -y microsoft-agents-a365-observability-extensions-semantic-kernel
pip uninstall -y microsoft-agents-a365-observability-extensions-agent-framework

# ✅ Install the new single package
pip install microsoft-opentelemetry
```

## Step 2 — Rewrite Import Paths

The old packages used `microsoft_agents_a365.*` namespace.
The new distro uses `microsoft.opentelemetry.*`.

### Core (observability-core)

| Old import path | New import path |
|-----------------|-----------------|
| `microsoft_agents_a365.observability.core` | `microsoft.opentelemetry.a365.core` |
| `from microsoft_agents_a365.observability.core import configure` | `from microsoft.opentelemetry import use_microsoft_opentelemetry` |
| `from microsoft_agents_a365.observability.core import is_configured` | Remove — not needed |
| `from microsoft_agents_a365.observability.core import get_tracer` | `from opentelemetry import trace; trace.get_tracer(...)` |
| `from microsoft_agents_a365.observability.core import get_tracer_provider` | `from opentelemetry import trace; trace.get_tracer_provider()` |
| `from microsoft_agents_a365.observability.core import Agent365ExporterOptions` | Use kwargs or env vars (see below) |
| `from microsoft_agents_a365.observability.core import SpectraExporterOptions` | Use OTLP env vars (see below) |
| `from microsoft_agents_a365.observability.core import SpanProcessor` | Remove — handled internally by distro |
| `from microsoft_agents_a365.observability.core import register_span_enricher` | Remove — handled internally |
| `from microsoft_agents_a365.observability.core import unregister_span_enricher` | Remove — handled internally |
| `from microsoft_agents_a365.observability.core import get_span_enricher` | Remove — handled internally |
| `from microsoft_agents_a365.observability.core import EnrichedReadableSpan` | Remove — handled internally |
| `from microsoft_agents_a365.observability.core import extract_context_from_headers` | Remove — use OTel propagation APIs |
| `from microsoft_agents_a365.observability.core import get_traceparent` | Remove — use OTel propagation APIs |

Scope classes, data models, and enums keep working — just change the package prefix:

```python
# ❌ OLD
from microsoft_agents_a365.observability.core import (
    AgentDetails,
    BaggageBuilder,
    CallerDetails,
    Channel,
    ChatMessage,
    ExecuteToolScope,
    InferenceCallDetails,
    InferenceOperationType,
    InferenceScope,
    InputMessages,
    InvokeAgentScope,
    InvokeAgentScopeDetails,
    MessageRole,
    OutputMessage,
    OutputMessages,
    OutputScope,
    Request,
    Response,
    ServiceEndpoint,
    SpanDetails,
    TextPart,
    ToolCallDetails,
    ToolType,
    UserDetails,
)

# ✅ NEW — same symbols, different package path
from microsoft.opentelemetry.a365.core import (
    AgentDetails,
    BaggageBuilder,
    CallerDetails,
    Channel,
    ChatMessage,
    ExecuteToolScope,
    InferenceCallDetails,
    InferenceOperationType,
    InferenceScope,
    InputMessages,
    InvokeAgentScope,
    InvokeAgentScopeDetails,
    MessageRole,
    OutputMessage,
    OutputMessages,
    OutputScope,
    Request,
    Response,
    ServiceEndpoint,
    SpanDetails,
    TextPart,
    ToolCallDetails,
    ToolType,
    UserDetails,
)
```

### Hosting (microsoft-agents-a365-observability-hosting)

```python
# ❌ OLD
from microsoft_agents_a365.observability.hosting import (
    BaggageMiddleware,
    OutputLoggingMiddleware,
    A365_PARENT_TRACEPARENT_KEY,
    ObservabilityHostingManager,
    ObservabilityHostingOptions,
)
from microsoft_agents_a365.observability.hosting.scope_helpers.populate_baggage import populate as populate_baggage
from microsoft_agents_a365.observability.hosting.scope_helpers.populate_invoke_agent_scope import populate as populate_invoke_agent_scope

# ✅ NEW — same symbols, different package path
from microsoft.opentelemetry.a365.hosting import (
    BaggageMiddleware,
    OutputLoggingMiddleware,
    A365_PARENT_TRACEPARENT_KEY,
    ObservabilityHostingManager,
    ObservabilityHostingOptions,
)
from microsoft.opentelemetry.a365.hosting.scope_helpers.populate_baggage import populate as populate_baggage
from microsoft.opentelemetry.a365.hosting.scope_helpers.populate_invoke_agent_scope import populate as populate_invoke_agent_scope
```

### Runtime (microsoft-agents-a365-runtime)

```python
# ❌ OLD
from microsoft_agents_a365.runtime import (
    get_observability_authentication_scope,
    PowerPlatformApiDiscovery,
    ClusterCategory,
    Utility,
    OperationError,
    OperationResult,
)

# ✅ NEW — same symbols, different package path
from microsoft.opentelemetry.a365.runtime import (
    get_observability_authentication_scope,
    PowerPlatformApiDiscovery,
    ClusterCategory,
    Utility,
    OperationError,
    OperationResult,
)
```

### Extensions — LangChain (observability-extensions-langchain)

```python
# ❌ OLD — manual instrumentor setup
from microsoft_agents_a365.observability.extensions.langchain import CustomLangChainInstrumentor

CustomLangChainInstrumentor().instrument()

# ✅ NEW — auto-instrumented by distro, no manual setup needed
# Set ENABLE_A365_OBSERVABILITY_EXPORTER=true in env
use_microsoft_opentelemetry(enable_a365=True)
# LangChain is auto-instrumented if installed
```

### Extensions — OpenAI (observability-extensions-openai)

```python
# ❌ OLD — manual instrumentor setup
from microsoft_agents_a365.observability.extensions.openai import OpenAIAgentsTraceInstrumentor

OpenAIAgentsTraceInstrumentor().instrument()

# ✅ NEW — auto-instrumented by distro, no manual setup needed
# Set ENABLE_A365_OBSERVABILITY_EXPORTER=true in env
use_microsoft_opentelemetry(enable_a365=True)
# OpenAI instrumentation is handled automatically
```

### Extensions — Semantic Kernel (observability-extensions-semantic-kernel)

```python
# ❌ OLD
from microsoft_agents_a365.observability.extensions.semantic_kernel import ...

# ✅ NEW — auto-instrumented by distro
# Set ENABLE_A365_OBSERVABILITY_EXPORTER=true in env
use_microsoft_opentelemetry(enable_a365=True)
```

### Extensions — Agent Framework (observability-extensions-agent-framework)

```python
# ❌ OLD — manual instrumentor setup
from microsoft_agents_a365.observability.extensions.agent_framework import AgentFrameworkTraceInstrumentor

AgentFrameworkTraceInstrumentor().instrument()

# ✅ NEW — auto-instrumented by distro, no manual setup needed
# Set ENABLE_A365_OBSERVABILITY_EXPORTER=true in env
use_microsoft_opentelemetry(enable_a365=True)
# Agent Framework is auto-instrumented if the agent-framework package is installed
```

## Step 3 — Replace configure() with Distro Entry Point

```python
# ❌ OLD — standalone SDK
from microsoft_agents_a365.observability.core import configure, Agent365ExporterOptions

configure(
    service_name="my-agent",
    service_namespace="my-namespace",
    exporter_options=Agent365ExporterOptions(
        cluster_category="prod",
        token_resolver=my_token_resolver,
        use_s2s_endpoint=True,
    ),
    suppress_invoke_agent_input=True,
)

# ✅ NEW — distro entry point with kwargs.
# Set ENABLE_A365_OBSERVABILITY_EXPORTER=true in env.
# `service.name` and `service.namespace` are set via an OTel Resource
# (the old `service_name` / `service_namespace` configure() args).
from opentelemetry.sdk.resources import Resource

from microsoft.opentelemetry import use_microsoft_opentelemetry

use_microsoft_opentelemetry(
    enable_a365=True,
    resource=Resource.create({
        "service.name": "my-agent",
        "service.namespace": "my-namespace",
    }),
    a365_token_resolver=my_token_resolver,
    a365_cluster_category="prod",
    a365_use_s2s_endpoint=True,
    a365_suppress_invoke_agent_input=True,
    a365_enable_observability_exporter=True,
    a365_observability_scope_override="api://<app-id>/.default",
    a365_max_queue_size=4096,
    a365_scheduled_delay_ms=2000,
    a365_exporter_timeout_ms=15000,
    a365_max_export_batch_size=256,
)
```

> **Note.** ``service.name`` and ``service.namespace`` can also be set via the
> ``OTEL_SERVICE_NAME`` and ``OTEL_RESOURCE_ATTRIBUTES`` environment variables
> (e.g. ``OTEL_RESOURCE_ATTRIBUTES="service.namespace=my-namespace"``). Use
> a ``Resource`` when you want to express them in code.

### configure() Parameter Mapping

| Old `configure()` parameter | New equivalent |
|-----------------------------|----------------|
| `service_name` | `OTEL_SERVICE_NAME` env var or `resource` kwarg |
| `service_namespace` | `resource` kwarg with `service.namespace` attribute |
| `token_resolver` | `a365_token_resolver` kwarg |
| `cluster_category` | `a365_cluster_category` kwarg or `A365_CLUSTER_CATEGORY` env var |
| `exporter_options` | Individual kwargs or env vars (see below) |
| `suppress_invoke_agent_input` | `a365_suppress_invoke_agent_input` kwarg or `A365_SUPPRESS_INVOKE_AGENT_INPUT` env var |
| `exporter_options.max_queue_size` | `a365_max_queue_size` kwarg |
| `exporter_options.scheduled_delay_ms` | `a365_scheduled_delay_ms` kwarg |
| `exporter_options.exporter_timeout_ms` | `a365_exporter_timeout_ms` kwarg |
| `exporter_options.max_export_batch_size` | `a365_max_export_batch_size` kwarg |
| _(env var only previously)_ `ENABLE_A365_OBSERVABILITY_EXPORTER` | `a365_enable_observability_exporter` kwarg or `ENABLE_A365_OBSERVABILITY_EXPORTER` env var |
| _(env var only previously)_ `A365_OBSERVABILITY_SCOPE_OVERRIDE` | `a365_observability_scope_override` kwarg or `A365_OBSERVABILITY_SCOPE_OVERRIDE` env var |

### Spectra Sidecar → OTLP

```python
# ❌ OLD — Spectra sidecar exporter
SpectraExporterOptions(protocol="grpc", endpoint="http://localhost:4317")

# ✅ NEW — use standard OTLP env vars
# OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4317
use_microsoft_opentelemetry()
```

## Step 4 — Replace get_tracer()

```python
# ❌ OLD
from microsoft_agents_a365.observability.core import get_tracer
tracer = get_tracer("my-module")

# ✅ NEW — standard OTel API
from opentelemetry import trace
tracer = trace.get_tracer("my-module")
```

## Default Instrumentations With A365 exporter enabled

The distro auto-discovers and activates supported OTel instrumentations.
When `enable_a365=True`, the distro **disables web-framework /
HTTP-client instrumentations by default**. GenAI instrumentations stay enabled.

> **Note:** When both `enable_a365=True` and `enable_azure_monitor=True` are
> set, the original (non-A365) defaults are used and the libraries below
> remain **enabled** so Azure Monitor continues to receive web/HTTP
> telemetry.

| Library | Default with A365 |
|---|---|
| `django` | disabled |
| `fastapi` | disabled |
| `flask` | disabled |
| `psycopg2` | disabled |
| `requests` | disabled |
| `urllib` | disabled |
| `urllib3` | disabled |
| `azure_sdk` | disabled |
| `openai` | enabled |
| `openai_agents` | enabled |
| `langchain` | enabled |
| `semantic_kernel` | enabled |
| `agent_framework` | enabled |

To re-enable any of these, pass `instrumentation_options`:

```python
use_microsoft_opentelemetry(
    enable_a365=True,
    instrumentation_options={
        "fastapi": {"enabled": True},
    },
)
```

When `enable_a365=False` (the default), all supported instrumentations
remain enabled by default.

## Custom SpanProcessors — Filtering Console Output

By default, when `enable_console=True` (or any additional exporter is
configured), **all** spans are exported — including framework-level spans
(`agents.app.*`, `agents.turn.*`, `agents.connector.*`,
`agents.authentication.*`). This can make local development noisy when you
only care about the 4 A365 observability scopes:

- `invoke_agent`
- `chat` (InferenceScope)
- `execute_tool` (ExecuteToolScope)
- `output_messages` (OutputScope)

To filter output to only A365 scopes, pass a custom `SpanProcessor` via the
`span_processors` parameter:

```python
from opentelemetry.trace import SpanContext, TraceFlags
from opentelemetry.sdk.trace import SpanProcessor, ReadableSpan

from microsoft.opentelemetry import use_microsoft_opentelemetry


class A365OnlyConsoleSpanProcessor(SpanProcessor):
    """Send only A365 observability spans to the console exporter."""

    A365_OPS = {"invoke_agent", "chat", "execute_tool", "output_messages"}

    def on_start(self, span, parent_context=None):
        op = (span.attributes or {}).get("gen_ai.operation.name")
        if op not in self.A365_OPS:
            # Create new SpanContext with trace_flags = 0
            span._context = SpanContext(
                span.context.trace_id,
                span.context.span_id,
                span.context.is_remote,
                TraceFlags(0),  # UNSAMPLED
                span.context.trace_state,
            )

    def on_end(self, span: ReadableSpan):
        pass

    def shutdown(self):
        pass

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return True


use_microsoft_opentelemetry(
    enable_a365=True,
    enable_console=True,
    a365_token_resolver=my_token_resolver,
    span_processors=[A365OnlyConsoleSpanProcessor()],
)
```

This leverages standard OpenTelemetry `SpanProcessor` APIs — you can adapt
the filter logic to any criteria (span name, attributes, etc.).

## Environment Variable Mapping

| Old env var | New env var | Notes |
|-------------|-------------|-------|
| `ENABLE_A365_OBSERVABILITY_EXPORTER` | `ENABLE_A365_OBSERVABILITY_EXPORTER` | Same — enables A365 HTTP exporter. Set to `false` with `enable_a365=True` to get enriched span attributes without exporting to A365. |
| `A365_CLUSTER_CATEGORY` | `A365_CLUSTER_CATEGORY` | Same — or use `a365_cluster_category` kwarg |
| `A365_USE_S2S_ENDPOINT` | `A365_USE_S2S_ENDPOINT` | Same — or use `a365_use_s2s_endpoint` kwarg |
| `A365_SUPPRESS_INVOKE_AGENT_INPUT` | `A365_SUPPRESS_INVOKE_AGENT_INPUT` | Same — or use `a365_suppress_invoke_agent_input` kwarg |
| `ENABLE_OTLP_EXPORTER` | `OTEL_EXPORTER_OTLP_ENDPOINT` | Use standard OTel env var instead |
| `ENABLE_OBSERVABILITY` | `ENABLE_OBSERVABILITY` | Same — master switch for A365 scope classes to emit spans |

## Auth Scopes — Breaking Change

> **⚠️ Breaking change:** The default observability authentication scope changed
> from `https://api.powerplatform.com/.default` (old A365 SDK) to
> `api://9b975845-388f-4429-889e-eab1ef63949c/Agent365.Observability.OtelWrite`.
> If you previously hardcoded the old scope, you will get auth failures after
> migration. You must also grant the `Agent365.Observability.OtelWrite`
> permission — see [HTTP 403 after upgrading](#http-403-after-upgrading).

Do **not** hardcode scope strings. Instead, use the runtime helper to get the
correct scope automatically:

```python
from microsoft.opentelemetry.a365.runtime import get_observability_authentication_scope

scopes = get_observability_authentication_scope()  # returns list[str]
```

If you need to override the scope for testing, use the
`a365_observability_scope_override` kwarg or the
`A365_OBSERVABILITY_SCOPE_OVERRIDE` environment variable:

```bash
# Linux / macOS
export A365_OBSERVABILITY_SCOPE_OVERRIDE="api://test/.default"

# Windows PowerShell
$env:A365_OBSERVABILITY_SCOPE_OVERRIDE = "api://test/.default"
```

## Full Migration Example

```python
# ❌ OLD — using standalone Agent365 SDK packages
from microsoft_agents_a365.observability.core import (
    configure,
    Agent365ExporterOptions,
    AgentDetails,
    BaggageBuilder,
    InvokeAgentScope,
    InvokeAgentScopeDetails,
    Request,
    get_tracer,
)
from microsoft_agents_a365.observability.extensions.langchain import CustomLangChainInstrumentor

configure(
    service_name="my-agent",
    service_namespace="my-ns",
    exporter_options=Agent365ExporterOptions(
        cluster_category="prod",
        token_resolver=my_resolver,
    ),
)

CustomLangChainInstrumentor().instrument()
tracer = get_tracer("my-module")

# ✅ NEW — using microsoft-opentelemetry distro
from opentelemetry.sdk.resources import Resource

from microsoft.opentelemetry import use_microsoft_opentelemetry
from microsoft.opentelemetry.a365.core import (
    AgentDetails,
    BaggageBuilder,
    InvokeAgentScope,
    InvokeAgentScopeDetails,
    Request,
)

use_microsoft_opentelemetry(
    enable_a365=True,
    resource=Resource.create({
        "service.name": "my-agent",
        "service.namespace": "my-ns",
    }),
    a365_token_resolver=my_resolver,
)
# LangChain auto-instrumented — no manual setup needed

# Scope classes work exactly the same after initialization
agent = AgentDetails(agent_id="my-agent", tenant_id="my-tenant")
with BaggageBuilder().tenant_id(agent.tenant_id).agent_id(agent.agent_id).build():
    with InvokeAgentScope.start(
        request=Request(content="hello"),
        scope_details=InvokeAgentScopeDetails(),
        agent_details=agent,
    ) as scope:
        scope.record_response("world")
```

## Avoiding Duplicate Spans After Migration

The distro auto-instruments supported GenAI frameworks (LangChain, Semantic
Kernel, OpenAI, OpenAI Agents SDK, Microsoft Agent Framework) automatically via
its OpenTelemetry instrumentor entry points. If your migrated code still calls
the old standalone instrumentor's `instrument()` method (for example
`CustomLangChainInstrumentor().instrument()` from
`microsoft-agents-a365-observability-extensions-langchain`), **you will get
duplicate spans** — one from the legacy instrumentor and one from the distro.

After migrating, remove all explicit `instrument()` calls from your code:

```python
# ❌ Remove — the distro handles this automatically
from microsoft_agents_a365.observability.extensions.langchain import (
    CustomLangChainInstrumentor,
)

CustomLangChainInstrumentor().instrument()
```

If you genuinely need to disable a specific auto-instrumentation, use
`instrumentation_options` on `use_microsoft_opentelemetry()` instead — see the
[Default Instrumentations](#default-instrumentations-with-a365-exporter-enabled)
section above.

## Validating Locally

Before pointing at the production A365 endpoint, you can validate the migration
by exporting spans to the console.

Set the exporter toggle to `False` (or set
`ENABLE_A365_OBSERVABILITY_EXPORTER=false`) while keeping `enable_a365=True`.
This keeps A365 baggage propagation and span enrichment active but skips the
HTTP export. With no other exporter configured, the distro automatically enables
the console exporter so you can see enriched spans in your terminal:

```python
from microsoft.opentelemetry import use_microsoft_opentelemetry

use_microsoft_opentelemetry(
    enable_a365=True,
    a365_enable_observability_exporter=False,  # disable HTTP export for local runs
    a365_token_resolver=my_token_resolver,
)
```

You can also explicitly enable the console exporter via `enable_console=True`
to view spans in your terminal for debugging:

```python
use_microsoft_opentelemetry(
    enable_a365=True,
    a365_enable_observability_exporter=False,
    enable_console=True,  # view spans in the console
    a365_token_resolver=my_token_resolver,
)
```

To see verbose distro and A365 diagnostic logs while validating, raise the log
level for the relevant Python loggers:

```python
import logging

logging.basicConfig(level=logging.INFO)
logging.getLogger("microsoft.opentelemetry").setLevel(logging.DEBUG)
logging.getLogger("microsoft.opentelemetry.a365").setLevel(logging.DEBUG)
```

You can also raise the OpenTelemetry SDK's own log level via the standard env
var:

```bash
# Linux / macOS
export OTEL_LOG_LEVEL=DEBUG

# Windows PowerShell
$env:OTEL_LOG_LEVEL = "DEBUG"
```

Once you've confirmed locally that A365 scope spans (`invoke_agent`, `chat`,
`execute_tool`, `output_messages`) appear with the expected
`gen_ai.operation.name`, `microsoft.tenant.id`, and `gen_ai.agent.id`
attributes, switch the exporter back on for production.

## Troubleshooting — Permissions and Setup

### HTTP 403 after upgrading

Your app registration or Managed Identity must have the
`Agent365.Observability.OtelWrite` permission. Without it, the distro's
exporter receives HTTP 403 from the A365 ingest endpoint and telemetry is not
recorded.

Grant the permission using one of the following options:

**Option A — Agent 365 CLI** (requires `a365.config.json` and
`a365.generated.config.json`, a Global Administrator account, and
[Agent 365 CLI v1.1.139-preview](https://www.nuget.org/packages/Microsoft.Agents.A365.DevTools.Cli/1.1.139-preview)
or later):

```bash
a365 setup admin --config-dir "<path-to-config-dir>"
```

**Option B — Entra Portal** (requires Global Administrator access):

1. Go to **Entra portal** > **App registrations** > select your Blueprint app.
2. Go to **API permissions** > **Add a permission** > **APIs my organization
   uses** > search for `9b975845-388f-4429-889e-eab1ef63949c`.
3. Select **Delegated permissions** > check `Agent365.Observability.OtelWrite`
   > **Add permissions**.
4. Repeat steps 2–3, this time select **Application permissions** > check
   `Agent365.Observability.OtelWrite` > **Add permissions**.
5. Click **Grant admin consent** and confirm.

### License requirements

Your tenant must have one of the following licenses assigned in the
[Microsoft 365 admin center](https://admin.cloud.microsoft/?source=applauncher#/homepage):

- Test - Microsoft 365 E7
- Microsoft 365 E7
- Microsoft Agent 365 Frontier

For the full troubleshooting guide, see the
[official troubleshooting documentation](https://learn.microsoft.com/en-us/microsoft-agent-365/developer/troubleshooting).

## Migration Checklist

Use this checklist to track progress through the migration.

**Packages & initialization**

- [ ] Uninstall all `microsoft-agents-a365-observability-*` packages
- [ ] Uninstall `microsoft-agents-a365-runtime` if present
- [ ] `pip install microsoft-opentelemetry`
- [ ] Replace `configure(...)` with `use_microsoft_opentelemetry(enable_a365=True, ...)`
- [ ] Set `service.name` / `service.version` via `resource=Resource.create(...)` or `OTEL_RESOURCE_ATTRIBUTES`

**Auto-instrumentation**

- [ ] Remove explicit `CustomLangChainInstrumentor().instrument()` (and equivalents) calls — the distro handles this automatically
- [ ] Review which instrumentations are active by default with A365 (GenAI only; HTTP/web frameworks disabled)
- [ ] Re-enable any non-GenAI instrumentations you still need via `instrumentation_options`

**Imports**

- [ ] Rewrite `from microsoft_agents_a365.observability.core import ...` to `from microsoft.opentelemetry.a365.core import ...`
- [ ] Rewrite hosting imports under `microsoft.opentelemetry.a365.hosting`
- [ ] Rewrite runtime imports under `microsoft.opentelemetry.a365.runtime`
- [ ] Replace `get_tracer(...)` with `from opentelemetry import trace; trace.get_tracer(...)`

**Token management**

- [ ] Pass any custom token resolver via `a365_token_resolver=...`
- [ ] If no resolver is supplied, confirm `DefaultAzureCredential` works in your environment

**Exporter customization**

- [ ] Replace `Agent365ExporterOptions` fields with `a365_*` kwargs on `use_microsoft_opentelemetry()` (e.g. `a365_max_queue_size`, `a365_scheduled_delay_ms`, `a365_exporter_timeout_ms`, `a365_max_export_batch_size`, `a365_use_s2s_endpoint`)

**Hosting middleware**

- [ ] Replace standalone `ObservabilityHostingManager` with the version exported from `microsoft.opentelemetry.a365.hosting`

**Permissions**

- [ ] Ensure `Agent365.Observability.OtelWrite` permission is granted on your app registration or Managed Identity
- [ ] Verify your tenant has one of the required licenses (Test - Microsoft 365 E7, Microsoft 365 E7, or Microsoft Agent 365 Frontier)

**Logging & validation**

- [ ] Optionally raise the `microsoft.opentelemetry` and `microsoft.opentelemetry.a365` Python logger levels for diagnostics
- [ ] Set `OTEL_LOG_LEVEL=DEBUG` for OTel SDK diagnostics during local validation
- [ ] Validate locally with `a365_enable_observability_exporter=False` to inspect enriched spans on the console

**Verification**

- [ ] Run unit tests to confirm A365 scope usage compiles and imports cleanly
- [ ] Inspect emitted spans for `gen_ai.operation.name`, `microsoft.tenant.id`, and `gen_ai.agent.id` attributes
- [ ] Validate end-to-end A365 export in the telemetry backend
- [ ] Test token refresh on long-running agents

## Next Steps

- [A365 Documentation](A365_DOCUMENTATION.md) — full distro usage guide (configuration, auto-instrumentation, baggage, scope classes, validate locally, troubleshooting)
- [README](README.md) — general distro options (Azure Monitor, OTLP, sampling, console exporter)
- [Microsoft OpenTelemetry SDK docs](https://learn.microsoft.com/en-us/microsoft-agent-365/developer/microsoft-opentelemetry?tabs=python) — official documentation on Microsoft Learn
- [Troubleshooting](https://learn.microsoft.com/en-us/microsoft-agent-365/developer/troubleshooting) — official troubleshooting guide
