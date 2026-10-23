# P19 final task routing and stream behavior

Task categories (`chat`, `retrieval`, `simple_execution`, `complex_execution`) select an existing entry point. They do not grant tool permissions. `cloud_direct`, `local_direct`, and `hybrid_delegate` remain model/execution-location routes in the existing route decision.

## Streaming behavior

`POST /api/chat/stream` sends ordinary chat and history/RAG retrieval through `Brain.stream_chat()`. Chat and retrieval requests set `no_tools=True`; history/RAG evidence is added by the existing scoped conversation context builder. The client receives incremental `text` events and the final `done` event. Coding requests accepted by the existing Agent Run gate continue through Agent Run event streaming.

`Brain.stream_chat()` does not implement function calling. A retrieval request on this endpoint can use the existing scoped history/RAG context, but it cannot invoke live web, workspace, or other function tools. A tool-backed simple execution that is not admitted to Agent Run keeps using the existing Brain function-calling path, which currently returns a non-streaming answer over the SSE connection. `/api/chat` is non-streaming by design and uses the existing Brain function-calling path; it has no Agent Run/Coding Harness lifecycle context. These limits are intentional and are not represented as successful tool execution.

When workspace authorization or classification is unavailable, execution requests fall back to a no-tool answer. Ambiguous references also stay on the no-tool chat path. Tool permissions remain enforced by the existing Brain and tool gates.

## Classification regression set

The offline P19 set includes mixed explanatory/action wording, follow-ups, and unresolved references. Explanations and references without a safely resolvable target remain `chat`; fully scoped file operations remain execution candidates and still require the existing authorization gate. The set is a deterministic regression contract, not an estimate of production accuracy.
