# P19 final task routing and stream behavior

Task categories (`chat`, `retrieval`, `simple_execution`, `complex_execution`) select an existing entry point. They do not grant tool permissions. `cloud_direct`, `local_direct`, and `hybrid_delegate` remain model/execution-location routes in the existing route decision.

## Streaming behavior

`POST /api/chat/stream` sends ordinary chat and historical memory/RAG retrieval through `Brain.stream_chat(no_tools=True)`. History/RAG evidence is added by the existing scoped conversation context builder. The client receives incremental `text` events and the final `done` event.

Live-information retrieval (such as current weather, news, or software versions) selects the existing `Brain.chat()` Function Calling path. The route checks that the corresponding enabled built-in tool (`weather`, `search`, or `tracking`) is available for the request channel before calling the model. If it is unavailable or disabled, the endpoint returns an explicit “无法核实” response without asking the model to guess. Agent Run and Coding Harness routing remains unchanged.

`Brain.stream_chat()` does not implement function calling. Historical retrieval streams through existing scoped context; live retrieval goes through `Brain.chat()` and returns its final answer over the SSE connection rather than incremental text. A tool-backed simple execution that is not admitted to Agent Run keeps using the same existing non-streaming Brain path. `/api/chat` is non-streaming by design and has no Agent Run/Coding Harness lifecycle context.

When workspace authorization or classification is unavailable, execution requests fall back to a no-tool answer. Ambiguous references also stay on the no-tool chat path. Tool permissions remain enforced by the existing Brain and tool gates; the live-retrieval availability check only decides whether a matching tool is exposed.

## Classification regression set

The offline P19 set includes mixed explanatory/action wording, follow-ups, and unresolved references. Explanations and references without a safely resolvable target remain `chat`; fully scoped file operations remain execution candidates and still require the existing authorization gate. The set is a deterministic regression contract, not an estimate of production accuracy.
