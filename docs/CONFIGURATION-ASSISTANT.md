# Configuration assistant

`Configurador` is a native Hermes profile, `app-config`, served by the existing gateway. It is not a second Telegram bot. Its SOUL, memories and sessions live under `/workspace/.hermes/profiles/app-config`; the two existing profiles and their Telegram route are unchanged.

The image-owned seeder installs the `configuration-assistant` skill into all three project profiles without overwriting later owner edits. The specialist SOUL is create-once/marker-preserving. Runtime configuration preserves supported owner-editable values and MCP definitions while pinning the private relay, smart approvals and protected instructions. Scoped credentials use the existing internal relay credential and a distinct native API key: HMAC-SHA256 keyed by the native default API token over UTF-8 `app-config`. The browser never receives this key, the relay token or native tool events. Profile `.env` is never copied from another profile and may not contain a Telegram bot credential.

## Owner API

`POST /api/settings/assistant` requires the existing owner session, same-origin check and CSRF token. Body: `{"message":"..."}` to start, or `{"message":"...","session_id":"owner_config_<32 lowercase hex>"}` to continue. Unknown fields, other session prefixes and messages over 20,000 characters are rejected. The response contains only `session_id` and the final `reply`. Sessions are created and answered exclusively through native `/p/app-config/api/sessions` routes with the specialist's key; they are not mixed into main Web history.

The existing internal HTTP client provides bounded network timeouts. Responses are size-limited; native error payloads, tool events and credential-shaped replies are not returned. Shared runtime saturation returns a clean busy message with no restart or retry. A failed initial turn can leave an empty native configuration session; no other conversation is deleted to recover it.

The same trusted shared chat-quarantine handler intercepts skill import requests and review commands before native inference. Reports use a separate deterministic display-only transcript identity. A new specialist conversation may reserve an empty native session, but no candidate message or raw imported bytes reach native chat. Approval and delivery remain restricted to the existing authorized target profiles.

## Boundaries

This is a real specialist profile using existing native tools, not an unrestricted settings API. Tool availability, owner authorization, secret fields, quarantine and native approval rules continue to apply. The skill does not automatically install plugins, execute scripts, approve imports or grant external application access. It asks the owner to use credential fields rather than paste secrets into chat. Profiles share one runtime UID: this is conversation/credential-routing isolation, not an OS sandbox against a compromised agent process.
