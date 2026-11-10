# Chat outcomes without empty failures

The chat stream and native history remain the source of truth. This change does not add a transcript database, browser cache, or synthetic messages to the agent's context.

## Live response handling

- Failed or cancelled runs, SSE error events, empty/whitespace completions, malformed event data, network errors, HTTP failures, and streams ending without a terminal event receive an inline Spanish explanation and a next step.
- Partial assistant text is preserved. Terminal reconciliation reuses native history and its pagination offsets, avoids repeating an already saved answer, and reapplies the safe explanation. If a received answer is not in native history yet, the current view retains it and explains the missing saved result.
- Error explanations come from a local allowlist. Raw exception messages, provider errors, URLs, and credentials in error payloads are never interpolated. Unknown errors do not claim that OpenAI or a particular tool caused the failure.
- SSE parsing supports event-name or payload-type events, multiline JSON data, split UTF-8 chunks, CRLF, and a final frame without a trailing newline. The stream reader is cancelled after a terminal event; normal answer text and tool activity continue using the existing renderers.
- The portal translates upstream connection failures and timeouts into sanitized HTTP responses. Read failures after streaming starts emit a safe SSE error code. Response cleanup releases the busy lock and active model selection even on failure. Successful native stream bytes are unchanged.

## Reopening history

A user/tool turn without a nonempty final assistant response receives a display-only message: no final response was saved, and the outcome is unconfirmed. Older-page boundaries do not create a synthetic missing-response warning. Missing responses between complete turns are also explained.

Native history does not necessarily store terminal failure reasons. The live explanation is therefore not advertised as durable. A reload cannot recover a specific error cause absent from native history, and an already saved nonempty partial assistant message may be indistinguishable from a final answer. A request rejected before native history is written also has no saved turn to annotate. Browser crashes, process termination, or failures outside the chat path are not covered by a universal never-fail guarantee.

## Verification

`tests/test_chat_errors.cjs` executes the real submit, rendering, and history-loading functions in a Node VM with a minimal DOM. `tests/test_history_ui.cjs` retains native-history pagination coverage. Portal tests stub upstream calls and verify transport failures, sanitization, normal stream passthrough, and lock release without paid provider calls.
