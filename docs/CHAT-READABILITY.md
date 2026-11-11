# Readable answers without executable content

Web assistant replies now use restrained typographic structure instead of displaying Markdown punctuation as an undifferentiated text block. The existing dark palette, composer, user messages, authorization and transports remain unchanged.

## Supported presentation

- Paragraphs and line breaks, Markdown headings, short standalone emoji section labels, and flat numbered or bulleted lists, including `•`.
- Bold emphasis, inline code and fenced code, including an unfinished streamed fence.
- Links and URLs remain readable, literal text rather than navigation controls.

HTML and image syntax stay visible text. Images never load; HTML is never parsed. No source text becomes a link or interactive control, including HTTP(S), unsafe URL schemes or credentials in URLs. Code is not executed or syntax-highlighted.

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
