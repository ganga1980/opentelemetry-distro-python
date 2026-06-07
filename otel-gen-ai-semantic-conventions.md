---
title: OpenTelemetry GenAI semantic conventions for multimodal prompts, outputs, and tool calls
description: Reference for the OpenTelemetry Generative AI semantic conventions that govern how to capture multimodal prompt inputs, model outputs, system instructions, tool definitions, and tool calls in spans and events.
author: agent-samples
ms.date: 2026-05-31
ms.topic: reference
keywords:
  - opentelemetry
  - semantic conventions
  - generative ai
  - multimodal
  - tool calling
  - observability
---

## Scope

This document captures the OpenTelemetry (OTel) Generative AI (GenAI) semantic conventions that are relevant to **multimodal prompt inputs, model outputs, system instructions, tool definitions, and tool calls**. It excludes unrelated GenAI areas (embeddings, retrieval, evaluation events, provider-specific overlays, exception details, and pure-token metrics) and excludes non-GenAI conventions.

All material below is sourced from the OpenTelemetry Semantic Conventions for GenAI, currently in `Development` status:

* GenAI overview: <https://opentelemetry.io/docs/specs/semconv/gen-ai/>
* GenAI client spans: <https://opentelemetry.io/docs/specs/semconv/gen-ai/gen-ai-spans/>
* GenAI agent and framework spans: <https://opentelemetry.io/docs/specs/semconv/gen-ai/gen-ai-agent-spans/>
* GenAI events: <https://opentelemetry.io/docs/specs/semconv/gen-ai/gen-ai-events/>
* LLM call examples (multimodal, tool calls, server-side tools): <https://opentelemetry.io/docs/specs/semconv/gen-ai/non-normative/examples-llm-calls/>
* Input messages JSON schema: <https://opentelemetry.io/docs/specs/semconv/gen-ai/gen-ai-input-messages.json>
* Output messages JSON schema: <https://opentelemetry.io/docs/specs/semconv/gen-ai/gen-ai-output-messages.json>
* System instructions JSON schema: <https://opentelemetry.io/docs/specs/semconv/gen-ai/gen-ai-system-instructions.json>
* Tool definitions JSON schema: <https://opentelemetry.io/docs/specs/semconv/gen-ai/gen-ai-tool-definitions.json>

## Stability and opt-in

The GenAI conventions are still in `Development`. Instrumentations already emitting the conventions from `v1.36.0` or prior MUST continue to do so by default and SHOULD expose the `OTEL_SEMCONV_STABILITY_OPT_IN` environment variable. The value `gen_ai_latest_experimental` opts in to the latest experimental version of the GenAI conventions described in this document.

## Multimodal-relevant operation names

`gen_ai.operation.name` (Required, string) identifies the operation being performed. The values most relevant to multimodal prompts, outputs, and tool calling are:

| Value | Meaning |
|---|---|
| `chat` | Chat completion operation (for example, OpenAI Chat API). Carries multimodal `gen_ai.input.messages` and `gen_ai.output.messages`. |
| `generate_content` | Multimodal content generation operation (for example, Gemini Generate Content). |
| `text_completion` | Legacy text completions operation. |
| `execute_tool` | Execute a tool (used by the `execute_tool` span). |
| `invoke_agent` | Invoke a GenAI agent. |
| `invoke_workflow` | Invoke a GenAI workflow that orchestrates multiple agents or steps. |
| `create_agent` | Create a GenAI agent. |

## Output modality (`gen_ai.output.type`)

`gen_ai.output.type` (Conditionally Required when applicable, string) declares the **content type requested by the client**. It captures the requested **modality** of the output, not the concrete wire format. The well-known values are:

| Value | Description |
|---|---|
| `text` | Plain text |
| `json` | JSON object (known or unknown schema) |
| `image` | Image |
| `speech` | Speech |

The model may return zero or more outputs of this type. For example, if `image` is requested the actual output may be a URL pointing to an image file. Future revisions may add per-type detail attributes of the form `gen_ai.output.{type}.*`.

## Inference span attributes that carry multimodal content

The inference span is emitted for any client call to a GenAI model that produces a response or a tool-call request, including multimodal generations. Span kind SHOULD be `CLIENT` (or `INTERNAL` for in-process models). Span name SHOULD be `{gen_ai.operation.name} {gen_ai.request.model}`. The attributes that carry multimodal content and tool calls are:

| Attribute | Requirement | Type | Purpose |
|---|---|---|---|
| `gen_ai.operation.name` | Required | string | Operation being performed (see table above). |
| `gen_ai.provider.name` | Required | string | The GenAI provider as identified by the instrumentation (for example `openai`, `azure.ai.openai`, `azure.ai.inference`, `aws.bedrock`, `gcp.gemini`, `gcp.vertex_ai`, `gcp.gen_ai`, `anthropic`, `cohere`, `mistral_ai`, `ibm.watsonx.ai`, `deepseek`, `groq`, `perplexity`, `x_ai`). |
| `gen_ai.request.model` | Conditionally Required | string | Requested model name. |
| `gen_ai.response.model` | Recommended | string | Model that produced the response. |
| `gen_ai.output.type` | Conditionally Required | string | Requested output modality (see table above). |
| `gen_ai.conversation.id` | Conditionally Required | string | Identifier for a session/thread when available. |
| `gen_ai.request.choice.count` | Conditionally Required | int | Number of candidate completions requested when != 1. |
| `gen_ai.response.finish_reasons` | Recommended | string[] | One entry per generated choice. |
| `gen_ai.system_instructions` | Opt-In | any | System message or instructions provided separately from chat history. Structured form follows the [System instructions JSON schema](https://opentelemetry.io/docs/specs/semconv/gen-ai/gen-ai-system-instructions.json). |
| `gen_ai.input.messages` | Opt-In | any | Chat history provided as input to the model. Structured form follows the [Input messages JSON schema](https://opentelemetry.io/docs/specs/semconv/gen-ai/gen-ai-input-messages.json). |
| `gen_ai.output.messages` | Opt-In | any | Messages returned by the model; each message represents one choice/candidate. Structured form follows the [Output messages JSON schema](https://opentelemetry.io/docs/specs/semconv/gen-ai/gen-ai-output-messages.json). |
| `gen_ai.tool.definitions` | Opt-In | any | Tools available to the model. Structured form follows the [Tool Definitions JSON schema](https://opentelemetry.io/docs/specs/semconv/gen-ai/gen-ai-tool-definitions.json). |

Instrumentations MUST record `gen_ai.input.messages`, `gen_ai.output.messages`, and `gen_ai.system_instructions` in structured form on events. On spans they SHOULD also use structured form; only when the language SDK does not yet support structured attributes MAY they be serialized to a JSON string. Messages MUST be ordered as they were sent.

## Multimodal message-parts data model

All multimodal content flows through a uniform `parts: []` array on each `ChatMessage` (input) or `OutputMessage` (output). The parts model is the same across input, output, and system instructions and is the heart of the multimodal conventions.

### Roles, finish reasons, and modality

```text
Role         = "system" | "user" | "assistant" | "tool"
FinishReason = "stop" | "length" | "content_filter" | "tool_call" | "error"
Modality     = "image" | "video" | "audio"
```

Notes:

* Text-only content does not carry a `Modality` value; `Modality` applies to `BlobPart`, `FilePart`, and `UriPart`.
* `OutputMessage.finish_reason` is required and is recorded per-choice; the corresponding span attribute `gen_ai.response.finish_reasons` is the array across all choices.

### `ChatMessage` (input) and `OutputMessage` (output)

```text
ChatMessage    = { role: Role, parts: Part[], name?: string }
OutputMessage  = { role: Role, parts: Part[], finish_reason: FinishReason, name?: string }
```

The `parts` array is a polymorphic list of the part types described below. Output messages additionally carry `finish_reason`; one `OutputMessage` corresponds to exactly one choice/candidate.

### Part types

| Part type | Where used | Required fields | Purpose |
|---|---|---|---|
| `TextPart` | input, output, system | `type="text"`, `content: string` | Text content sent to or received from the model. |
| `BlobPart` | input, output, system | `type="blob"`, `modality: Modality`, `content: base64` (optional `mime_type`) | Inline binary payload (image, audio, video). `content` MUST be base64-encoded when serialized to JSON. |
| `UriPart` | input, output, system | `type="uri"`, `modality: Modality`, `uri: string` (optional `mime_type`) | Reference to remote media by URI (for example `gs://bucket/object.png` or a public URL). Use `BlobPart` for inline data, not `data:` URLs. |
| `FilePart` | input, output, system | `type="file"`, `modality: Modality`, `file_id: string` (optional `mime_type`) | Reference to a file pre-uploaded to the provider (for example the OpenAI Files API). |
| `ReasoningPart` | input, output, system | `type="reasoning"`, `content: string` | Chain-of-thought / extended thinking content emitted by the model. |
| `ToolCallRequestPart` | input, output, system | `type="tool_call"`, `name: string` (optional `id`, `arguments`) | A tool call requested by the model (client-side tools). |
| `ToolCallResponsePart` | input, output, system | `type="tool_call_response"`, `response: any` (optional `id`) | Result of a client-side tool execution fed back to the model. |
| `ServerToolCallPart` | input, output | `type="server_tool_call"`, `name: string`, `server_tool_call: { type, ... }` (optional `id`) | A tool call executed on the **provider side** (for example `code_interpreter`, `web_search`). The inner object is polymorphic and typed by provider. |
| `ServerToolCallResponsePart` | input, output | `type="server_tool_call_response"`, `server_tool_call_response: { type, ... }` (optional `id`) | Outcome of a provider-side tool execution, polymorphic by tool type. |
| `GenericPart` | input, output, system | `type: string` (custom) | Extensibility escape hatch for any custom part type. |

Notes on multimodal media parts:

* `modality` SHOULD be set when known. Instrumentations SHOULD also set `mime_type` (IANA media type) when the specific type is known, in addition to `modality`.
* Use `BlobPart` for inline data, `UriPart` for remote references (including provider-specific URI schemes), and `FilePart` for pre-uploaded file IDs.

## Tool definitions

`gen_ai.tool.definitions` is an array of tool descriptors made available to the model. Two shapes are normative:

* `FunctionToolDefinition`: `{ type: "function", name: string, description?: string, parameters?: JSON Schema draft-07 }`. The `parameters` value MUST conform to JSON Schema draft-07.
* `GenericToolDefinition`: `{ type: string, name: string }` for non-function tools (extension, datastore, custom).

Because tool definitions can be large, instrumentations SHOULD NOT populate the optional `description` and `parameters` fields by default; they MAY expose an option to enable them.

## Execute-tool span (client-side tool execution)

The `execute_tool` span describes execution of a tool invoked as a result of a model tool-call request. Span kind SHOULD be `INTERNAL`. Span name SHOULD be `execute_tool {gen_ai.tool.name}`.

| Attribute | Requirement | Type | Purpose |
|---|---|---|---|
| `gen_ai.operation.name` | Required | string | MUST be `execute_tool`. |
| `gen_ai.tool.name` | Required | string | Name of the tool. |
| `gen_ai.tool.call.id` | Recommended | string | Tool call identifier from the model output. |
| `gen_ai.tool.description` | Recommended | string | Free-form description of the tool. |
| `gen_ai.tool.type` | Recommended | string | One of `function` (client-side), `extension` (agent-side bridge to external APIs), `datastore` (data retrieval). |
| `gen_ai.tool.call.arguments` | Opt-In | any | Parameters passed to the tool. Object form preferred; JSON string allowed on spans that lack structured attributes. |
| `gen_ai.tool.call.result` | Opt-In | any | Result returned by the tool (object form preferred). |

MCP-driven tool executions MAY be covered by the corresponding MCP instrumentation rather than `execute_tool`.

## Agent and workflow spans that carry multimodal content

The following spans extend the inference attributes above and propagate the same multimodal `gen_ai.input.messages`, `gen_ai.output.messages`, `gen_ai.system_instructions`, and `gen_ai.tool.definitions` attributes.

* **Create agent span** (`gen_ai.operation.name = create_agent`, kind `CLIENT`). Adds `gen_ai.agent.id`, `gen_ai.agent.name`, `gen_ai.agent.description`, `gen_ai.agent.version`. May carry `gen_ai.system_instructions`.
* **Invoke agent client span** (`gen_ai.operation.name = invoke_agent`, kind `CLIENT`). For remote agents (for example OpenAI Assistants, AWS Bedrock Agents). Carries the full multimodal attribute set.
* **Invoke agent internal span** (`gen_ai.operation.name = invoke_agent`, kind `INTERNAL`). For in-process agents (for example LangChain, CrewAI). Carries the full multimodal attribute set.
* **Invoke workflow span** (`gen_ai.operation.name = invoke_workflow`, kind `INTERNAL`). Adds `gen_ai.workflow.name`. Carries `gen_ai.input.messages` and `gen_ai.output.messages`.

## GenAI events that carry multimodal content

When emitted as logs/events instead of (or in addition to) span attributes:

* **`gen_ai.client.inference.operation.details`** carries the same multimodal attribute set as the inference span, including `gen_ai.system_instructions`, `gen_ai.input.messages`, `gen_ai.output.messages`, and `gen_ai.tool.definitions`. On events these structured attributes MUST be recorded in structured form. The event is opt-in and allows storing inputs/outputs independently from traces.

## Recording sensitive multimodal content

`gen_ai.system_instructions`, `gen_ai.input.messages`, `gen_ai.output.messages`, `gen_ai.tool.call.arguments`, and `gen_ai.tool.call.result` are likely to contain user/PII data and may be large (binary media). The spec defines three usage patterns:

1. **Default — do not record content.** Recommended for production.
2. **Record content on the GenAI span/event** using the attributes above. Suitable when telemetry volume is manageable and privacy controls match telemetry storage (for example pre-production).
3. **Upload content to external storage** and record references on the span. Recommended for production. Instrumentations MAY expose an in-process hook that receives the instructions/input/output objects and the span before serialization, runs independently of the opt-in flags, can enrich or replace the data, and can attach upload references.

Instrumentations SHOULD provide a way to truncate individual message contents while preserving JSON structure, and SHOULD provide opt-in flags for capturing each of the three content attributes independently.

## Examples

These examples are condensed from the [LLM call examples](https://opentelemetry.io/docs/specs/semconv/gen-ai/non-normative/examples-llm-calls/) page.

### Multimodal input (`gen_ai.input.messages`)

```json
[
  {
    "role": "user",
    "parts": [
      { "type": "text", "content": "What is in the attached data?" },
      {
        "type": "uri",
        "modality": "image",
        "mime_type": "image/png",
        "uri": "https://example.com/diagram.png"
      },
      {
        "type": "uri",
        "modality": "video",
        "mime_type": "video/mp4",
        "uri": "gs://my-bucket/my-video.mp4"
      },
      {
        "type": "file",
        "modality": "image",
        "file_id": "provider_fileid_123"
      },
      {
        "type": "blob",
        "modality": "image",
        "mime_type": "image/png",
        "content": "aGVsbG8gd29ybGQgaW1hZ2luZSB0aGlzIGlzIGFuIGltYWdlCg=="
      },
      {
        "type": "blob",
        "modality": "audio",
        "mime_type": "audio/wav",
        "content": "aGVsbG8gd29ybGQgaW1hZ2luZSB0aGlzIGlzIGFuIGltYWdlCg=="
      }
    ]
  }
]
```

### Multimodal output (`gen_ai.output.messages`)

```json
[
  {
    "role": "assistant",
    "finish_reason": "stop",
    "parts": [
      {
        "type": "blob",
        "modality": "image",
        "mime_type": "image/jpg",
        "content": "aGVsbG8gd29ybGQgaW1hZ2luZSB0aGlzIGlzIGFuIGltYWdlCg=="
      }
    ]
  }
]
```

### Client-side tool call (function tool)

`gen_ai.tool.definitions`:

```json
[
  {
    "type": "function",
    "name": "get_current_weather",
    "description": "Get the current weather in a given location",
    "parameters": {
      "type": "object",
      "properties": {
        "location": { "type": "string", "description": "The city and state, e.g. San Francisco, CA" },
        "unit": { "type": "string", "enum": ["celsius", "fahrenheit"] }
      },
      "required": ["location", "unit"]
    }
  }
]
```

Model output requesting the tool call (`gen_ai.output.messages`):

```json
[
  {
    "role": "assistant",
    "finish_reason": "tool_call",
    "parts": [
      {
        "type": "tool_call",
        "id": "call_VSPygqKTWdrhaFErNvMV18Yl",
        "name": "get_weather",
        "arguments": { "location": "Paris" }
      }
    ]
  }
]
```

Follow-up chat history including the tool response (`gen_ai.input.messages`):

```json
[
  { "role": "user", "parts": [{ "type": "text", "content": "Weather in Paris?" }] },
  {
    "role": "assistant",
    "parts": [
      {
        "type": "tool_call",
        "id": "call_VSPygqKTWdrhaFErNvMV18Yl",
        "name": "get_weather",
        "arguments": { "location": "Paris" }
      }
    ]
  },
  {
    "role": "tool",
    "parts": [
      {
        "type": "tool_call_response",
        "id": "call_VSPygqKTWdrhaFErNvMV18Yl",
        "response": "rainy, 57°F"
      }
    ]
  }
]
```

Corresponding `execute_tool` span:

| Attribute | Value |
|---|---|
| Span name | `execute_tool get_weather` |
| `gen_ai.operation.name` | `execute_tool` |
| `gen_ai.tool.name` | `get_weather` |
| `gen_ai.tool.call.id` | `call_VSPygqKTWdrhaFErNvMV18Yl` |
| `gen_ai.tool.type` | `function` |

### Server-side (built-in) tool call

Built-in tools executed by the provider (for example OpenAI `code_interpreter`, `web_search`) use `server_tool_call` and `server_tool_call_response` parts with polymorphic inner objects. Example `gen_ai.output.messages`:

```json
[
  {
    "role": "assistant",
    "finish_reason": "stop",
    "parts": [
      {
        "type": "server_tool_call",
        "id": "call_VSPygqKTWdrhaFErNvMV18Yl",
        "name": "code_interpreter",
        "server_tool_call": {
          "type": "code_interpreter",
          "code": "import random\nrandom_number = random.randint(1, 100)\nresult = random_number ** 2\nrandom_number, result",
          "container_id": "cntr_690bdbfed8688190884efd4c7ae6435b0db1f006442e8941"
        }
      },
      {
        "type": "server_tool_call_response",
        "id": "call_VSPygqKTWdrhaFErNvMV18Yl",
        "server_tool_call_response": {
          "type": "code_interpreter",
          "outputs": [{ "type": "logs", "logs": "(10, 20)" }]
        }
      },
      {
        "type": "text",
        "content": "The generated random number is 89, and the result of squaring it is 7921."
      }
    ]
  }
]
```

### Reasoning content (`ReasoningPart`)

```json
[
  {
    "role": "assistant",
    "finish_reason": "stop",
    "parts": [
      {
        "type": "reasoning",
        "content": "Alright, the user wants a joke about OpenTelemetry... let me put it all together."
      },
      {
        "type": "text",
        "content": "Why did the developer bring OpenTelemetry to the party? Because it always knows how to trace the fun!"
      }
    ]
  }
]
```

### System instructions provided separately from chat history

```json
[
  { "type": "text", "content": "You must never tell jokes" }
]
```

System instructions follow the same `Part[]` schema as input messages, but without a `role`/`parts` wrapper — they are a top-level array of parts.
