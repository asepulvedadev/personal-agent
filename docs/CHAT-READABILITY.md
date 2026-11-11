# Readable answers without executable content

Web assistant replies now use restrained typographic structure instead of displaying Markdown punctuation as an undifferentiated text block. The existing dark palette, composer, user messages, authorization and transports remain unchanged.

## Supported presentation

- Paragraphs and line breaks, Markdown headings, short standalone emoji section labels, and flat numbered or bulleted lists, including `•`.
- Bold emphasis, inline code and fenced code, including an unfinished streamed fence.
- Links and URLs remain readable, literal text rather than navigation controls.

HTML is never parsed. External image syntax and links stay visible text, including HTTP(S), unsafe URL schemes and credentials in URLs. The sole image exception is a strict relative `/api/images/<32 lowercase hexadecimal characters>` registry URL: it displays a private generated PNG and a same-origin download control. The endpoint requires owner authentication; arbitrary URLs and filesystem paths cannot load. Code is not executed or syntax-highlighted.

Generated image nodes are reused during streaming so later text does not repeatedly request the same image. Reloaded messages retain the same private registry reference. Images inside fenced code remain literal examples.

## Source and outcomes

`app/static/chat-renderer.js` constructs a small allowlisted DOM using element creation and `textContent`, never `innerHTML`. The original source is retained separately in a `WeakMap`. Formatting is a view, not a transcript format or an authority signal.

`app/static/app.js` accumulates SSE deltas in a raw string. It never rebuilds that string from formatted DOM. Saved assistant messages, including quarantine history, use the same rendering path. Existing pagination and history merging remain responsible for deduplication.

Local cancellation/error explanations render outside unfinished Markdown blocks. Reload reconciliation compares original strings, so syntax markers cannot cause a partial answer to be duplicated or silently discarded. If the local renderer cannot load, the safe fallback remains plain text.

## Verification

```sh
node --test tests/test_chat_renderer.cjs
node --test tests/*.cjs
```

The focused tests cover semantic structure, malicious HTML and URLs, non-loading images, every streamed prefix, history-equivalent rendering, incomplete fences and raw-source retention. Browser layout and mobile scrolling require visual verification; DOM unit tests do not establish visual quality.
