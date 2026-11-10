# Isolated personal-agent deployment

## Authorized outcome

Fresh Hermes Agent deployment on the explicitly authorized VPS, using the authorized root SSH credential, with a minimal web chat, separate protected secrets page, OpenAI API support, status visibility and isolation from unrelated applications. No credentials are recorded here.

## Deployed access

- Chat: https://alvaro.2.24.115.58.sslip.io/
- Secrets: https://alvaro.2.24.115.58.sslip.io/secrets
- Status: https://alvaro.2.24.115.58.sslip.io/status
- Private owner access instructions: `private/ACCESS.md` on the owner's local computer, excluded from Git.
- VPS project root: `/srv/personal-agent`; active source: `/srv/personal-agent/current`.

## Design boundaries

- Three resource-limited Docker services: Hermes, owner portal/provider relay, filtered Squid outbound proxy. No public application ports; only an additive route through the existing Caddy TLS edge.
- Hermes v0.23.1, upstream commit `3b421578588243f920886e6fe79a6faa87ea026e`, archive SHA256 `2525046f165880c22f177f2214c8da3e26c58cb0aeab5bd09aad940556538897`. Python 3.14 matches this release's runtime dependencies. SQLite 3.51.3 is built inside the image to avoid the base distribution's WAL-reset bug; host libraries are unchanged.
- Agent UID 10001, read-only root, all capabilities dropped, no-new-privileges, no Docker/SSH/host-project mounts. Writable workspace is a separate 12 GiB ext4 filesystem. Container limit: 1 CPU, 2 GiB RAM, 256 PIDs.
- Owner portal UID 10002: 0.5 CPU, 384 MiB RAM, 96 PIDs; proxy: 0.25 CPU, 128 MiB RAM, 64 PIDs. Bounded tmpfs and rotated logs.
- Internal runtime subnet `172.30.40.0/28`, proxy outbound subnet `172.30.41.0/28`, internal edge subnet `172.30.42.0/28`. Dedicated source-scoped firewall chains block agent direct outbound and host access. Squid allows public HTTP/HTTPS while denying private, metadata and this VPS's public address.
- Portal holds the real OpenAI credential in a Fernet-encrypted vault; Hermes receives only a delegated service token for the fixed OpenAI relay. Owner-granted generic environment variables ARE readable by the agent and must be scoped accordingly.
- HTTPS, single-owner scrypt authentication, signed secure/HttpOnly/SameSite cookies, origin/CSRF checks, request-size limits, fixed model/output limits and secret-safe error responses. Login attempts are reserved before awaiting hashing, including concurrent requests.
- No third-party browser assets. Chat uses Hermes native persistent sessions and SSE, not a replacement agent loop.

## Verified evidence — 2026-11-10 UTC

- Local portal suite: **51 Python + 9 Node passed**; one dependency deprecation warning. Concurrent-login regression observed failing before the fix and passing afterward; independent scoped recheck confirmed 5 failures admitted and 7 requests throttled from a burst of 12.
- Actual HTTPS certificate validation passed. Authenticated HTTPS smoke checks passed owner cookies, protected endpoints, origin/CSRF denial, rejected reserved variables without state change, native session creation/history and missing-provider-key refusal.
- Actual container probes confirmed non-root UID, zero effective capabilities, no-new-privileges, workspace writes, denied writes to image paths, absent host files/socket, blocked host/private/direct-outbound connections and denied metadata/private/self-IP proxy destinations. Public OpenAI endpoint reachable through the filtered proxy (unauthenticated 401 expected).
- A test variable reached the Hermes process and was then removed. The owner subsequently configured a private OpenAI key; it was never displayed or copied into the agent.
- Hermes/web restart preserved login, encrypted settings and native session history. No whole-VPS reboot test was performed.
- Daily project-only backup timer enabled; first backup succeeded. A database extracted from the backup passed SQLite integrity validation and retained the session.
- All **16 pre-existing containers** retained their IDs, images, start times and running states after deployment and recovery tests. Existing Caddy routes were preserved; Caddy was gracefully reloaded, not restarted.
- No project source was committed or pushed by this deployment work. Private profile/context files were seeded only into the isolated workspace, not the public repository.

## Operations and recovery

Own units: `personal-agent.service`, `personal-agent-firewall.service`, `personal-agent-edge-link.service`, `personal-agent-backup.service`, `personal-agent-backup.timer`, and the dedicated workspace mount unit. Local copies of installed units are under `deploy/units/`.

`deploy/status.sh` inspects this project's containers, limits, workspace, units and HTTPS health. The edge-link service reconnects only the dedicated edge network if the shared Caddy container is recreated. It does not manage other projects' routes or containers.

Backups live under `/srv/personal-agent/backups`, root-only, retain three archives and snapshot SQLite through its backup API. Backups include the vault's decryption key and therefore are **not independently encrypted**: protect the entire archive. No offsite backup was authorized or configured. A complete crash-consistent snapshot of arbitrary agent-written files is not guaranteed during active writes. Full restore must be performed into a stopped, isolated target and verified before switching; it has not been exercised against the live workspace.

## Responses integration correction — 2026-11-10 UTC

The owner selected `gpt-6.1-sol`. Model-access validation succeeded, but the first real turn failed HTTP 400: OpenAI rejected function tools with reasoning through Chat Completions. Hermes resolves the legacy `openai` alias and a private relay hostname as `custom/chat_completions`; explicit `openai-api` resolves to `codex_responses` while preserving the relay.

Main, compression and title routes now explicitly use `openai-api` + `codex_responses`. No model substitution, direct agent API key, expanded networking, security bypass or unrelated service changes were needed. Local config regression first failed then passed; the full suite now has 26 passing cases. Independent scoped config review passed. Only the Hermes image/container was rebuilt for this correction.

Actual deployed resolver confirmed `openai-api/codex_responses`. A real browser chat turn returned `HERMES_RESPONSES_OK — archivo creado y leído.` after tool use. Independent container readback confirmed `/workspace/projects/responses-acceptance-20261110.txt` contains the expected marker. Authenticated status confirmed selected model, configured provider, healthy Hermes/proxy and idle completion. Browser reload preserved the actual answer in history. The recreated container passed the isolation probe again, and all 16 pre-existing containers remained unchanged.

## Remaining limitations

Telegram, sustained model-load latency, custom-domain setup and offsite backups have NOT been verified or configured here. Native full-text session search currently reports SQLite FTS5 unavailable; ordinary session/history persistence works, but FTS5 needs a separate image-build correction. Container isolation is defense-in-depth, not a claim of protection against an unknown kernel/runtime exploit.


## Spanish chat and media update

See `CHAT.md` for Spanish usage, limits, privacy and live acceptance evidence. The portal now provides text-only document attachments, streamed recorded-audio transcription and authenticated live PCM transcription through the existing egress proxy. New parser dependencies are pinned; no original upload is persisted, no workspace mount was added, and the model credential remains server-only. The own-hostname Caddy body limit is11MB with stricter route limits (5MiB documents,10MiB audio). Microphone permission is self-only, camera/geolocation remain denied; CSP allows only the own-origin WebSocket. Independent review and34Python+2Node tests passed. Actual synthetic file/live audio and browser document roundtrip passed. Physical-microphone permission testing remains owner-controlled.


## Model and reasoning selector

See `MODELOS.md` for the Spanish model-selector guide. Owner-authenticated account inventory is intersected with an official, verified effort catalog; only streaming/tool-compatible aliases appear. Selections persist encrypted and are locked per turn without resetting sessions or restarting Hermes. The trusted relay forces the selected model and exact effort for primary and auxiliary requests. GPT-4.1 omits reasoning parameters. Runtime revision is separate from picker preference revision; key/variable changes retain the existing restart behavior. Independent concurrency/security verification passed, including in-flight inventory and key-rotation races. Full suite43Python+2Nodepassed. ActualLuna/low->Sol/medium switches preserved the same session and gatewayPID.

## Saved history and icon-only UI — 2026-11-10 UTC

See `HISTORIAL.md` for the Spanish usage and deletion-scope guide. Icon-only actions retain accessible names and tooltips; chats are automatically saved in native Hermes storage. Paginated history uses stable owner lineage roots and can reopen chats or load older messages. Single/all deletion requires owner, Origin, CSRF and explicit confirmation; bulk deletion snapshots a bounded inventory before writing and reports partial failures. A pinned fail-closed image patch enables native compression-chain deletion. Backups, agent memory, workspace files and minimal native retry receipts are not secure-erased.

51 Python + 3 Node checks passed, with independent deletion/concurrency review and a scoped pagination correction/recheck. A disposable native API compressed-chain probe observed RED before the patch and GREEN after deployment. Authenticated live deletion removed only a newly created empty fixture; all five pre-existing portal chats survived. Browser history retrieval, reload persistence and destructive-dialog cancellation were verified. Only web/Hermes were recreated; all 16 unrelated containers remained unchanged. No source commit or push.

## Visible tool and skill activity — 2026-11-10 UTC

See `ACTIVIDAD.md`. Frontend-only activity traces show validated tool names and skill_view names, with conservative observable statuses. Raw args, previews, outputs and reasoning are not rendered. Live session/run event dialects and historical call/result pairs are supported; parallel completions lacking IDs are not arbitrarily attributed. Historical result presence is labelled recorded, not successful. No extra telemetry storage, backend or native changes were needed.

51 Python +9 Node checks and independent scoped security/display review passed. Only web was rebuilt/recreated. A real browser conversation used skills_list then skill_view(popular-web-designs), answered ACTIVIDAD_OK and displayed both actions from persisted native messages. All 16 unrelated containers remained unchanged; no source commit/push.
