# Auto-Instrumentation Architecture — GenAI Agent Frameworks

This document describes how **auto-instrumentation** works end-to-end in the
`microsoft-opentelemetry` Python distro for the four supported GenAI agent
frameworks:

| Framework | Instrumentor | Entry-point name |
|---|---|---|
| Agent Framework (MAF) | `AgentFrameworkInstrumentor` | `agent_framework` |
| Semantic Kernel | `SemanticKernelInstrumentor` | `semantic_kernel` |
| LangChain | `LangChainInstrumentor` | `langchain` |
| OpenAI Agents SDK | upstream `openai-agents-v2` **or** A365 `A365OpenAIAgentsInstrumentor` | `openai_agents` |

The goal is to explain how a single call to `use_microsoft_opentelemetry(...)`
discovers, activates, and wires each framework's instrumentation so that the
framework emits OpenTelemetry spans — and how those spans are normalized and
exported, including the divergence between the **generic** (Azure Monitor / OTLP)
path and the **A365** path.

> Scope: GenAI agent frameworks only. Web/HTTP/DB instrumentations
> (`django`, `fastapi`, `flask`, `requests`, `httpx`, `urllib`, `urllib3`,
> `psycopg2`) and plain `openai` chat instrumentation are intentionally out of
> scope here — see [Architecture.md](Architecture.md) for the full distro view.

---

## 1. How auto-instrumentation is triggered

Auto-instrumentation is **not** a separate process or a `sitecustomize` hook in
this distro. It is driven explicitly from the single onboarding call:

```python
from microsoft.opentelemetry import use_microsoft_opentelemetry

use_microsoft_opentelemetry(
    enable_azure_monitor=True,        # or enable_a365=True, enable_console, ...
    enable_sensitive_data=False,
)
```

Inside [`_distro.py`](src/microsoft/opentelemetry/_distro.py), after the
exporter pipelines and global providers are built,
`_setup_instrumentations(...)` runs the discovery + activation loop. The key
function is `_setup_instrumentations` ([src/microsoft/opentelemetry/_distro.py](src/microsoft/opentelemetry/_distro.py#L712)).

```mermaid
%%{init: {'theme': 'base', 'themeVariables': {
  'primaryColor':'#E3F2FD','primaryBorderColor':'#1565C0','primaryTextColor':'#0D47A1',
  'lineColor':'#37474F','fontFamily':'Segoe UI'
}}}%%
flowchart TB
    classDef api fill:#FFF59D,stroke:#F9A825,stroke-width:2px,color:#000
    classDef cfg fill:#E1BEE7,stroke:#6A1B9A,stroke-width:2px,color:#000
    classDef instr fill:#C8E6C9,stroke:#2E7D32,stroke-width:2px,color:#000
    classDef decision fill:#FFE0B2,stroke:#E65100,stroke-width:2px,color:#000

    Start(["use_microsoft_opentelemetry(**kwargs)"]):::api
    Providers["Build + register global providers<br/>TracerProvider / MeterProvider / LoggerProvider"]:::cfg
    Setup["_setup_instrumentations(otel_kwargs, enable_a365, enable_sensitive_data)"]:::cfg
    DisableOpenAI["_disable_openai_v2_instrumentation()<br/>auto-disable plain 'openai' when<br/>langchain_core or agent_framework present"]:::cfg
    Loop{"for entry_point in<br/>entry_points('opentelemetry_instrumentor')"}:::decision
    Supported{"name in<br/>_SUPPORTED_INSTRUMENTED_LIBRARIES?"}:::decision
    Enabled{"_is_instrumentation_enabled()?<br/>(instrumentation_options)"}:::decision
    A365Route{"name == 'openai_agents'<br/>and enable_a365?"}:::decision
    A365OAI["_setup_a365_openai_agents_instrumentation()"]:::instr
    Conflict{"get_dist_dependency_conflicts()?"}:::decision
    Activate["instrumentor().instrument(skip_dep_check=True, **merged_kwargs)"]:::instr
    Stats["set_sdkstats_instrumentation_by_name(lib_name)"]:::cfg
    Next(["next entry point"]):::decision

    Start --> Providers --> Setup --> DisableOpenAI --> Loop
    Loop --> Supported
    Supported -- no --> Next
    Supported -- yes --> Enabled
    Enabled -- disabled --> Next
    Enabled -- enabled --> A365Route
    A365Route -- yes --> A365OAI --> Stats --> Next
    A365Route -- no --> Conflict
    Conflict -- conflict --> Next
    Conflict -- ok --> Activate --> Stats --> Next
    Next --> Loop
```

### Discovery and gating rules

1. **Entry-point discovery** — `entry_points(group="opentelemetry_instrumentor")`
   enumerates every installed instrumentor (both this distro's GenAI
   instrumentors declared in [`pyproject.toml`](pyproject.toml#L82) and any
   upstream contrib instrumentors).
2. **Allow-list filter** — only names in `_SUPPORTED_INSTRUMENTED_LIBRARIES`
   ([src/microsoft/opentelemetry/_constants.py](src/microsoft/opentelemetry/_constants.py#L31))
   are eligible.
3. **Per-library enable check** — `instrumentation_options={"<lib>": {"enabled": False}}`
   can disable any library.
4. **OpenAI Agents routing** — when `enable_a365=True`, the loop **skips** the
   upstream `openai_agents` entry point and instead activates the A365-specific
   `A365OpenAIAgentsInstrumentor` (versioned message format for A365 consumers).
5. **Dependency-conflict check** — `get_dist_dependency_conflicts()`
   ([src/microsoft/opentelemetry/_instrumentation.py](src/microsoft/opentelemetry/_instrumentation.py#L67))
   verifies the instrumented library's installed version satisfies the
   instrumentor's `instruments` / `instruments-any` requirements before
   activation.
6. **`openai` auto-suppression** — `_disable_openai_v2_instrumentation()`
   ([src/microsoft/opentelemetry/_utils.py](src/microsoft/opentelemetry/_utils.py#L157))
   disables the plain `openai` instrumentation when `langchain_core` or
   `agent_framework` is present, to avoid double-instrumenting the same LLM
   calls. (Out of scope here, but relevant to agent workloads.)
7. **SDKStats** — each activated library is recorded via
   `set_sdkstats_instrumentation_by_name(lib_name)` for self-telemetry.

---

## 2. Component architecture

Each GenAI instrumentor is a `BaseInstrumentor` whose `_instrument()` does two
things: (a) make the underlying framework emit spans, and (b) register
attribute-normalization hooks. The mechanism differs per framework because each
SDK exposes a different extension point.

```mermaid
%%{init: {'theme': 'base', 'themeVariables': {
  'primaryColor':'#E3F2FD','primaryBorderColor':'#1565C0','primaryTextColor':'#0D47A1',
  'lineColor':'#37474F','fontFamily':'Segoe UI'
}}}%%
flowchart TB
    classDef fw fill:#FFF59D,stroke:#F9A825,stroke-width:2px,color:#000
    classDef instr fill:#C8E6C9,stroke:#2E7D32,stroke-width:2px,color:#000
    classDef proc fill:#B3E5FC,stroke:#0277BD,stroke-width:2px,color:#000
    classDef enrich fill:#D1C4E9,stroke:#4527A0,stroke-width:2px,color:#000
    classDef sdk fill:#E1BEE7,stroke:#6A1B9A,stroke-width:2px,color:#000
    classDef exp fill:#FFCDD2,stroke:#C62828,stroke-width:2px,color:#000
    classDef backend fill:#263238,stroke:#000,color:#FFF

    subgraph FW ["Agent Framework SDKs"]
      AF["agent_framework"]:::fw
      SK["semantic_kernel"]:::fw
      LC["langchain_core"]:::fw
      OAI["openai-agents SDK"]:::fw
    end

    subgraph INSTR ["Instrumentors (BaseInstrumentor)"]
      AFI["AgentFrameworkInstrumentor<br/>calls enable_instrumentation()"]:::instr
      SKI["SemanticKernelInstrumentor<br/>adds SpanProcessor"]:::instr
      LCI["LangChainInstrumentor<br/>wraps BaseCallbackManager.__init__"]:::instr
      OAII["upstream openai_agents-v2<br/>OR A365OpenAIAgentsInstrumentor"]:::instr
    end

    subgraph HOOKS ["Per-framework hooks"]
      AFP["AgentFrameworkSpanProcessor<br/>(no-op, interface only)"]:::proc
      SKP["SemanticKernelSpanProcessor<br/>renames chat.* -> 'chat &lt;model&gt;'"]:::proc
      LCT["LangChainTracer<br/>(BaseTracer callback)"]:::proc
      OAIP["OpenAIAgentsTraceProcessor<br/>(A365 TracingProcessor)"]:::proc
    end

    subgraph SDKCORE ["OpenTelemetry SDK"]
      TP[(TracerProvider<br/>global singleton)]:::sdk
    end

    subgraph A365PIPE ["A365 pipeline (enable_a365=True)"]
      ENR["Span enricher registry<br/>register_span_enricher()"]:::enrich
      BAGG["A365SpanProcessor<br/>baggage -> span attributes"]:::enrich
      EBSP["_EnrichingBatchSpanProcessor<br/>enrich + suppress + batch"]:::enrich
      A365EXP["_Agent365Exporter"]:::exp
    end

    GENPIPE["Generic exporters<br/>Azure Monitor / OTLP / Console<br/>BatchSpanProcessor"]:::exp
    A365EP[("agent365.svc.cloud.microsoft")]:::backend
    GENEP[("App Insights / OTLP / stdout")]:::backend

    AFI -->|enable_instrumentation| AF
    SKI --> SK
    LCI -->|monkey-patch| LC
    OAII --> OAI

    AFI --> AFP --> TP
    SKI --> SKP --> TP
    LCI --> LCT
    LCT -. attached to callbacks .-> LC
    OAII --> OAIP

    AF -->|emits spans| TP
    SK -->|emits spans| TP
    LC -->|callbacks -> spans| TP
    OAI -->|trace events -> spans| TP

    AFI -. register .-> ENR
    SKI -. register .-> ENR

    TP --> EBSP
    EBSP -. applies .-> ENR
    BAGG --> TP
    EBSP --> A365EXP --> A365EP

    TP --> GENPIPE --> GENEP
```

### Instrumentation mechanism per framework

| Framework | How spans get created | Normalization hook |
|---|---|---|
| **Agent Framework** | Instrumentor calls `agent_framework.observability.enable_instrumentation(enable_sensitive_data=...)`; the **SDK itself** emits OTel spans. | `enrich_agent_framework_span` enricher (A365 only) + no-op `AgentFrameworkSpanProcessor`. |
| **Semantic Kernel** | SK emits its own `chat.*` spans; instrumentor adds `SemanticKernelSpanProcessor` to rename them and set `gen_ai.operation.name`. | `SemanticKernelSpanProcessor.on_start` (always) + `enrich_semantic_kernel_span` enricher (A365 only). |
| **LangChain** | Instrumentor monkey-patches `BaseCallbackManager.__init__` (via `wrapt`) to attach a `LangChainTracer` (a `BaseTracer`) to every callback manager; LangChain run callbacks drive span creation. | `LangChainTracer` builds spans directly with normalized `gen_ai.*` attributes. |
| **OpenAI Agents** | A `TracingProcessor` is registered with the OpenAI Agents SDK trace provider; SDK trace/span events are translated to OTel spans. | A365 `OpenAIAgentsTraceProcessor` emits versioned message format; upstream emits standard format. |

---

## 3. End-to-end runtime flow (generic path)

This is the common runtime path when exporting to **Azure Monitor**, **OTLP**,
or **Console** (i.e. `enable_a365=False`). A365-specific steps are added in
[§5](#5-a365-divergence).

```mermaid
%%{init: {'theme': 'base', 'themeVariables': {
  'primaryColor':'#E3F2FD','primaryBorderColor':'#1565C0','primaryTextColor':'#0D47A1',
  'lineColor':'#37474F','fontFamily':'Segoe UI'
}}}%%
sequenceDiagram
    autonumber
    participant App as User App
    participant FW as Agent Framework SDK
    participant Hook as Instrumentor hook<br/>(processor / tracer / callback)
    participant TP as TracerProvider
    participant SP as GenAIMainAgentSpanProcessor<br/>(Azure Monitor only)
    participant BSP as BatchSpanProcessor
    participant EXP as Exporter (AzMon / OTLP / Console)
    participant BE as Backend

    App->>FW: invoke agent / run chain / call tool
    FW->>Hook: SDK extension point fires
    Hook->>TP: start span (gen_ai.* attributes)
    TP->>SP: on_start(span)
    Note over SP: copy microsoft.gen_ai.main_agent.*<br/>from parent span (multi-agent attribution)
    FW->>Hook: operation completes
    Hook->>TP: end span (set output, usage, status)
    TP->>SP: on_end(span)
    Note over SP: if invoke_agent + not enriched,<br/>self-copy gen_ai.* -> main_agent.*
    TP->>BSP: on_end(span) -> queue
    BSP-->>EXP: export batch
    EXP-->>BE: send telemetry
```

> `GenAIMainAgentSpanProcessor` / `GenAIMainAgentLogRecordProcessor`
> ([src/microsoft/opentelemetry/_genai/main_agent/_processor.py](src/microsoft/opentelemetry/_genai/main_agent/_processor.py))
> are **prepended** to the processor lists only when `enable_azure_monitor=True`.
> They attribute all child telemetry in a multi-agent system to the top-level
> ("main") agent by propagating `microsoft.gen_ai.main_agent.*` attributes.

---

## 4. Per-framework sequence flows

### 4.1 Agent Framework (MAF)

The instrumentor delegates span creation entirely to the Agent Framework SDK.

```mermaid
%%{init: {'theme': 'base', 'themeVariables': {'primaryColor':'#E3F2FD','lineColor':'#37474F','fontFamily':'Segoe UI'}}}%%
sequenceDiagram
    autonumber
    participant D as _setup_instrumentations
    participant I as AgentFrameworkInstrumentor
    participant AF as agent_framework.observability
    participant TP as TracerProvider
    participant ENR as Span enricher registry

    D->>I: instrument(skip_dep_check=True, enable_sensitive_data)
    I->>AF: enable_instrumentation(enable_sensitive_data)
    Note over AF: SDK now emits OTel spans natively
    I->>TP: add_span_processor(AgentFrameworkSpanProcessor)
    Note over I,TP: processor is a no-op (interface compat)
    alt A365 pipeline available
        I->>ENR: register_span_enricher(enrich_agent_framework_span)
        Note over ENR: transforms AF-specific keys -><br/>gen_ai.input/output.messages,<br/>gen_ai.tool.call.* at export
    else A365 not available
        Note over I: enricher registration skipped (ImportError)
    end
```

Key files:
- [src/microsoft/opentelemetry/_agent_framework/_trace_instrumentor.py](src/microsoft/opentelemetry/_agent_framework/_trace_instrumentor.py)
- [src/microsoft/opentelemetry/_agent_framework/_span_enricher.py](src/microsoft/opentelemetry/_agent_framework/_span_enricher.py)

### 4.2 Semantic Kernel

The SK SDK already emits spans; the instrumentor adds a processor that
**renames** `chat.*` spans at `on_start` and an enricher applied at export.

```mermaid
%%{init: {'theme': 'base', 'themeVariables': {'primaryColor':'#E3F2FD','lineColor':'#37474F','fontFamily':'Segoe UI'}}}%%
sequenceDiagram
    autonumber
    participant SK as semantic_kernel
    participant SKP as SemanticKernelSpanProcessor
    participant TP as TracerProvider
    participant EBSP as _EnrichingBatchSpanProcessor (A365)
    participant ENR as enrich_semantic_kernel_span

    SK->>TP: start span "chat.<model>"
    TP->>SKP: on_start(span)
    Note over SKP: set gen_ai.operation.name = "chat"<br/>update_name -> "chat <model_name>"
    SK->>TP: end span
    alt A365 enabled
        TP->>EBSP: on_end(span)
        EBSP->>ENR: enrich(span)
        Note over ENR: normalize SK attributes -> gen_ai.*
        EBSP->>EBSP: batch + export
    else generic
        Note over TP: BatchSpanProcessor exports as-is
    end
```

Key files:
- [src/microsoft/opentelemetry/_semantic_kernel/_trace_instrumentor.py](src/microsoft/opentelemetry/_semantic_kernel/_trace_instrumentor.py)
- [src/microsoft/opentelemetry/_semantic_kernel/_span_processor.py](src/microsoft/opentelemetry/_semantic_kernel/_span_processor.py)

### 4.3 LangChain

LangChain has no span API; instead the instrumentor injects a `BaseTracer`
callback handler into **every** callback manager via a `wrapt` monkey-patch of
`BaseCallbackManager.__init__`. LangChain's run lifecycle callbacks then drive
span creation inside `LangChainTracer`.

```mermaid
%%{init: {'theme': 'base', 'themeVariables': {'primaryColor':'#E3F2FD','lineColor':'#37474F','fontFamily':'Segoe UI'}}}%%
sequenceDiagram
    autonumber
    participant I as LangChainInstrumentor
    participant CM as BaseCallbackManager
    participant T as LangChainTracer (BaseTracer)
    participant LC as LangChain runnable
    participant TP as TracerProvider

    I->>CM: wrap __init__ (wrapt)
    Note over I,CM: every new callback manager gets<br/>LangChainTracer added as inheritable handler
    LC->>CM: create callback manager (per run)
    CM->>T: add_handler(LangChainTracer, inherit=True)
    LC->>T: on_chain_start / on_llm_start / on_tool_start
    T->>TP: start span (run_id -> span map)
    LC->>T: on_llm_end / on_chain_end / on_tool_end
    T->>TP: end span (usage, output, finish attributes)
    Note over T: run_map tracks parent_run_id<br/>for span ancestry
```

Key files:
- [src/microsoft/opentelemetry/_genai/_langchain/_tracer_instrumentor.py](src/microsoft/opentelemetry/_genai/_langchain/_tracer_instrumentor.py)
- [src/microsoft/opentelemetry/_genai/_langchain/_tracer.py](src/microsoft/opentelemetry/_genai/_langchain/_tracer.py)

> Note: LangChain uses **no** span enricher. The `LangChainTracer` writes
> normalized `gen_ai.*` attributes directly when building each span, so both the
> generic and A365 paths consume the same span shape.

### 4.4 OpenAI Agents SDK

The OpenAI Agents SDK has its own tracing system. A `TracingProcessor` is
registered with its trace provider. Which processor depends on `enable_a365`:

```mermaid
%%{init: {'theme': 'base', 'themeVariables': {'primaryColor':'#E3F2FD','lineColor':'#37474F','fontFamily':'Segoe UI'}}}%%
flowchart TB
    classDef decision fill:#FFE0B2,stroke:#E65100,stroke-width:2px,color:#000
    classDef instr fill:#C8E6C9,stroke:#2E7D32,stroke-width:2px,color:#000
    classDef proc fill:#B3E5FC,stroke:#0277BD,stroke-width:2px,color:#000

    Q{"enable_a365?"}:::decision
    Up["upstream opentelemetry-instrumentation-<br/>openai-agents-v2 (entry point)"]:::instr
    A365["A365OpenAIAgentsInstrumentor"]:::instr
    UpP["standard OTel spans<br/>(Azure Monitor / OTLP)"]:::proc
    A365P["OpenAIAgentsTraceProcessor<br/>versioned message format,<br/>custom.parent.span.id, indexed messages"]:::proc
    Reg["agents trace provider<br/>set_processors([...existing, processor])"]:::proc

    Q -- "no" --> Up --> UpP --> Reg
    Q -- "yes" --> A365 --> A365P --> Reg
```

```mermaid
%%{init: {'theme': 'base', 'themeVariables': {'primaryColor':'#E3F2FD','lineColor':'#37474F','fontFamily':'Segoe UI'}}}%%
sequenceDiagram
    autonumber
    participant I as A365OpenAIAgentsInstrumentor
    participant AP as agents trace provider
    participant TP as OpenAIAgentsTraceProcessor
    participant OAI as OpenAI Agents runtime
    participant OT as TracerProvider

    I->>TP: create OpenAIAgentsTraceProcessor(tracer)
    I->>AP: get_trace_provider().set_processors([...existing, TP])
    Note over I,AP: fallback to set_trace_processors([TP])<br/>if provider API unavailable
    OAI->>TP: on_trace_start / on_span_start (Agent, Generation,<br/>Function, Handoff, Response, MCPListTools)
    TP->>OT: map SDK span data -> OTel span<br/>(gen_ai.operation.name, messages, tool ids)
    OAI->>TP: on_span_end / on_trace_end
    TP->>OT: end span (status, output messages)
```

Key files:
- [src/microsoft/opentelemetry/_genai/_openai_agents/_trace_instrumentor.py](src/microsoft/opentelemetry/_genai/_openai_agents/_trace_instrumentor.py)
- [src/microsoft/opentelemetry/_genai/_openai_agents/_trace_processor.py](src/microsoft/opentelemetry/_genai/_openai_agents/_trace_processor.py)

---

## 5. A365 divergence

When `enable_a365=True`, three additional mechanisms apply on top of the generic
flow. All are wired in `_append_a365_components`
([src/microsoft/opentelemetry/_distro.py](src/microsoft/opentelemetry/_distro.py#L407)).

```mermaid
%%{init: {'theme': 'base', 'themeVariables': {'primaryColor':'#E3F2FD','lineColor':'#37474F','fontFamily':'Segoe UI'}}}%%
sequenceDiagram
    autonumber
    participant FW as Agent Framework SDK
    participant BAGG as A365SpanProcessor
    participant TP as TracerProvider
    participant EBSP as _EnrichingBatchSpanProcessor
    participant ENR as registered span enricher
    participant EXP as _Agent365Exporter
    participant EP as agent365.svc.cloud.microsoft

    FW->>TP: start span
    TP->>BAGG: on_start(span)
    Note over BAGG: copy baggage entries -> span attributes<br/>(gen_ai.agent.id, microsoft.tenant.id,<br/>user.name, conversation.id, ...)
    FW->>TP: end span
    TP->>EBSP: on_end(span)
    EBSP->>ENR: enrich(span) (framework-specific)
    Note over ENR: AF: gen_ai.tool.call.* -> gen_ai.tool.args/result<br/>SK: rename/normalize attributes
    opt suppress_invoke_agent_input
        EBSP->>EBSP: drop gen_ai.input.messages on InvokeAgent spans
    end
    EBSP->>EXP: export enriched batch
    EXP->>EP: HTTPS (token via resolver / DefaultAzureCredential)
```

### A365-specific behaviors

1. **Baggage propagation** — `A365SpanProcessor.on_start`
   ([src/microsoft/opentelemetry/a365/core/exporters/span_processor.py](src/microsoft/opentelemetry/a365/core/exporters/span_processor.py))
   copies documented baggage keys (tenant/user/agent/conversation identifiers)
   onto each new span without overwriting existing attributes. Registered
   whenever `enable_a365=True`.
2. **Single span enricher** — only **one** enricher may be registered at a time
   (`register_span_enricher` raises `RuntimeError` on a second registration),
   because auto-instrumentation is platform-specific. Agent Framework and
   Semantic Kernel register enrichers; LangChain and OpenAI Agents do not (they
   emit normalized attributes directly). The enricher runs inside
   `_EnrichingBatchSpanProcessor.on_end` just before batching.
3. **Input suppression** — when `a365_suppress_invoke_agent_input=True` (or
   `A365_SUPPRESS_INVOKE_AGENT_INPUT=true`), `gen_ai.input.messages` is stripped
   from `InvokeAgent` spans via an `EnrichedReadableSpan` wrapper before export.
4. **Web/HTTP suppression** — when `enable_a365=True` and Azure Monitor is
   **off**, the distro disables `django`, `fastapi`, `flask`, `httpx`,
   `psycopg2`, `requests`, `urllib`, `urllib3`, `azure_sdk` by default
   (`_A365_DISABLED_INSTRUMENTATIONS`,
   [src/microsoft/opentelemetry/_constants.py](src/microsoft/opentelemetry/_constants.py#L50)),
   since agent workloads typically only need GenAI spans. Users can re-enable
   any library via `instrumentation_options`.
5. **OpenAI Agents routing** — the A365 instrumentor replaces the upstream
   entry point so spans carry the versioned message format A365 consumers
   expect (see [§4.4](#44-openai-agents-sdk)).

---

## 6. Attribute normalization summary

All four frameworks converge on the standard `gen_ai.*` semantic-convention
attributes, but each starts from a different source shape:

| Framework | Source span shape | Normalization point | Notable target attributes |
|---|---|---|---|
| Agent Framework | SDK-native OTel spans with AF-specific keys (`gen_ai.tool.call.arguments`, `gen_ai.tool.call.result`) | `enrich_agent_framework_span` at export (A365) | `gen_ai.input.messages`, `gen_ai.output.messages`, `gen_ai.tool.args`, `gen_ai.tool.call.result` |
| Semantic Kernel | `chat.*` spans | `SemanticKernelSpanProcessor.on_start` + enricher | `gen_ai.operation.name`, renamed span `chat <model>` |
| LangChain | run callbacks (no spans) | `LangChainTracer` at span build time | `gen_ai.operation.name`, `gen_ai.input/output.messages`, `gen_ai.usage.*`, `gen_ai.request.model` |
| OpenAI Agents | SDK trace/span data objects | `OpenAIAgentsTraceProcessor` at translation | `gen_ai.operation.name`, `gen_ai.input/output.messages`, `gen_ai.tool.call.id`, `custom.parent.span.id` |

Common operation names: `invoke_agent`, `chat`, `execute_tool`.

---

## 7. Key takeaways

- Auto-instrumentation is **explicitly orchestrated** by
  `use_microsoft_opentelemetry(...)` via entry-point discovery — not by an
  implicit Python startup hook.
- Each framework is instrumented through its **own** extension point:
  SDK enablement (Agent Framework), span processor (Semantic Kernel), callback
  monkey-patch (LangChain), or a tracing processor (OpenAI Agents).
- The **generic** path (Azure Monitor / OTLP / Console) and the **A365** path
  share span creation but diverge at export: A365 adds baggage propagation, a
  single framework-specific enricher, optional input suppression, web
  instrumentation suppression, and a dedicated OpenAI Agents processor.
- All frameworks normalize to the standard `gen_ai.*` semantic conventions so
  downstream consumers see a consistent schema regardless of the originating
  SDK.
