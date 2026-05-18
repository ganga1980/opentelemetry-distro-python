# OpenAI Samples

These samples demonstrate how to use OpenAI and Azure OpenAI with OpenTelemetry tracing exported to Azure Monitor (Application Insights).

## Prerequisites

- Python 3.10+
- An OpenAI API key or an Azure OpenAI resource
- An Application Insights resource (for the connection string)

## Installation

```bash
pip install -r requirements.txt
```

## Configuration

| Environment Variable                    | Description                                              |
| --------------------------------------- | -------------------------------------------------------- |
| `APPLICATIONINSIGHTS_CONNECTION_STRING` | Azure Monitor connection string                          |
| `OPENAI_API_KEY`                        | OpenAI API key                                           |
| `AZURE_OPENAI_ENDPOINT`                 | Azure OpenAI endpoint (e.g. `https://<resource>.openai.azure.com/`) |
| `AZURE_OPENAI_API_KEY`                  | Azure OpenAI API key                                     |
| `AZURE_OPENAI_DEPLOYMENT`               | Azure OpenAI deployment/model name                       |
| `OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT` | Set to capture GenAI message content (prompts and completions). The options are `SPAN_ONLY`, `EVENT_ONLY` and `SPAN_AND_EVENT` |
| `OTEL_SEMCONV_STABILITY_OPT_IN`        | Set to `gen_ai_latest_experimental` to enable experimental attributes. (must be set if the above is set) |

## Samples

### 1. `sample_openai_chat.py`

Demonstrates chat completions using both the OpenAI and Azure OpenAI clients.

The `openai` instrumentor (`opentelemetry-instrumentation-openai-v2`) wraps
chat-completion and embedding calls. It only accepts provider-level kwargs
(`tracer_provider`, `logger_provider`, `meter_provider`) — no agent-specific
configuration.

**Run:**
```bash
python sample_openai_chat.py
```

### 2. `sample_openai_agents.py`

Demonstrates the OpenAI Agents SDK with tool calling and tracing.

The `openai_agents` instrumentor (`opentelemetry-instrumentation-openai-agents-v2`)
accepts several configuration options that can be passed via `instrumentation_options`:

| Option                    | Type   | Description                                                                 |
| ------------------------- | ------ | --------------------------------------------------------------------------- |
| `enabled`                 | `bool` | Enable / disable the instrumentation (default `True`)                       |
| `agent_id`                | `str`  | Static agent identifier → `gen_ai.agent.id` attribute                       |
| `agent_name`              | `str`  | Static agent name → `gen_ai.agent.name` attribute                           |
| `agent_description`       | `str`  | Static agent description → `gen_ai.agent.description` attribute             |
| `server_address`          | `str`  | Server address → `server.address` attribute                                 |
| `server_port`             | `int`  | Server port → `server.port` attribute                                       |
| `base_url`                | `str`  | Base URL (derives `server.address` and `server.port` when not set directly) |
| `system`                  | `str`  | GenAI system value (default `"openai"`)                                     |
| `capture_message_content` | `str`  | Content capture mode: `SPAN_ONLY`, `EVENT_ONLY`, `SPAN_AND_EVENT`          |
| `capture_metrics`         | `bool` | Enable metric capture (default `True`)                                      |

Example:
```python
use_microsoft_opentelemetry(
    enable_azure_monitor=True,
    instrumentation_options={
        "openai_agents": {
            "agent_id": "my-agent-001",
            "agent_name": "My_Agent",
            "capture_message_content": "SPAN_AND_EVENT",
        },
    },
)
```

**Run:**
```bash
python sample_openai_agents.py
```
