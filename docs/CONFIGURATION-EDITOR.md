# Owner configuration editor

The Web chat exposes a minimal agent selector and fixed logical files for the default
Hermes Web profile and existing live named profiles:

- `config.yaml`: an editable non-secret projection, not the complete raw file.
- `SOUL.md`: the profile identity and instructions.
- `USER.md`: the real file at `memories/USER.md`.

Carpediev uses its real VPS profile, not a copy from the local computer. This feature
does not expose `.env`, authentication files, MCP credentials,
agent memories, arbitrary paths, host files, or project files. It does not remove the
owner's existing administrative grant. Credentials remain in the existing Secrets UI.

## Editable configuration

Only these exact typed fields are supported:

| Field | Type | Allowed values |
| --- | --- | --- |
| `agent.max_turns` | Integer | 1–200 |
| `terminal.timeout` | Integer | 1–3600 seconds |
| `memory.memory_enabled` | Boolean | `true` or `false` |
| `memory.user_profile_enabled` | Boolean | `true` or `false` |
| `skills.creation_nudge_interval` | Integer | 0–1000 |

The response is JSON, which is valid YAML. YAML input is accepted with a safe parser;
unknown fields, duplicate keys, anchors, aliases, custom tags, incorrect types and
out-of-range values are rejected rather than silently discarded. Empty mappings are
not an instruction to reset configuration. Omitted recognized fields retain their
current values. Model/provider routing, security, approval mode and MCP settings cannot
be edited here; their existing values are retained when recognized values are merged.

A save serializes the complete resulting mapping as JSON-compatible YAML, so existing
formatting and comments are not preserved. The private pre-save backup retains the
original bytes. The supervisor reuses the same validation schema to preserve supported
owner values across its later configuration regeneration, without inheriting arbitrary
provider, relay or terminal backend overrides.

Saving does **not** restart, signal or send a message to Hermes. Some configuration
changes require a later authorized runtime restart; identity and user-context changes
apply to new conversations. The UI must not claim that saving proves immediate adoption.

## Request boundaries

Owner-facing routes:

- `GET /api/settings/files`: fixed metadata only.
- `GET /api/settings/files/{id}`: `id`, `name`, `content`, `revision`, `note`.
- `PUT /api/settings/files/{id}` with exactly `{content, revision}`: updated snapshot
  including `saved: true`.

`GET /api/settings/profiles` returns bounded `{profiles: [{id, name}]}` metadata.
An optional `?profile=<id>` selects a profile on all fixed-file routes; omission retains
existing default behavior. The selector reloads discovery every time the modal opens.
Changing profiles requires explicit discard if a draft is dirty, blocks during saves,
and ignores stale reads. A save is bound to the profile selected when it began.

Discovery matches native lowercase/digit/hyphen/underscore IDs up to 64 characters,
immediate non-symlink directories under the startup home's `profiles`, and a regular,
non-hard-linked native identity marker (`config.yaml`, `.env`, `SOUL.md`, `profile.yaml`,
`auth.json` or `state.db`). Marker contents are never read for discovery. Hidden partial
profiles, markerless shells and `profiles/.deleted/<id>` tombstones are excluded. There
is a bound of 64 named profiles plus default. Eligibility is rechecked for each operation;
unknown/deleted IDs never fall back to default or create a directory. Only persistent
profiles appear, not temporary delegated runs. No external host or local filesystem is
searched. Named profiles without a safe `memories` directory do not offer USER.md; the
editor never substitutes a root USER.md for the real `memories/USER.md`.

IDs are exactly `config`, `soul`, and `user`. All reads require owner authentication;
writes also require the existing same-origin CSRF token. Responses are not cached.
The existing 65,536-byte Web request cap still applies. Text content has an additional
32,768-byte UTF-8 cap; the Web proxy bounds native responses before parsing them and
never relays native error bodies or raw file contents from exceptions.

The helper registers only these unprefixed routes on the **existing authenticated
Hermes aiohttp listener**, after native profile-route mirroring. It requires a configured
nonempty native API key and calls the existing native authentication check before file
I/O. There is no `/p/{profile}` editor mirror, additional port, host service, LLM-mediated
file operation, shell command or Web workspace mount. The startup default `HERMES_HOME`
is captured once; only the explicitly selected and validated profile ID changes the
fixed file root, never a request-time profile ContextVar or arbitrary path.
The build patch loads the fixed image-owned helper path, not a workspace import.

## Persistence and limits

Filesystem traversal uses directory descriptors and `O_NOFOLLOW` on each component,
lock, target and backup directory. Non-regular and hard-linked targets are rejected.
Malformed UTF-8, NULs and recognizable private-key or credential literals in Markdown
are blocked on both read and write, with generic errors. This is a best-effort accidental
exposure check, not a comprehensive secret detector; owners must not store secrets in
identity or profile documents.

Saves use a cooperative exclusive file lock and compare the actual raw-file SHA-256
revision supplied by the browser. Stale edits receive HTTP 409 without replacing the
file. The revision is checked again immediately before atomic replacement. Valid saves
retain at most five private pre-save backups per logical file, write with mode 0600,
fsync the contents and directory, and atomically replace the target. No delete endpoint
exists. Missing Markdown files may be created in an existing safe parent directory;
missing configuration or missing parent directories are not silently initialized.

The lock coordinates editor requests, **not arbitrary root or other uncooperative
writers**. Such a writer can still race after the final check. Administrative access
means this mechanism is not a security boundary against a compromised agent or root.

## Verification and deployment state

Offline tests exercise projection preservation, owner/CSRF boundaries, real aiohttp
native authentication and route registration, conflict rejection, unsafe paths, invalid
content, bounded responses and backups. A minimal pinned-seam fixture verifies exact-once
patch application, AST validity and placement after profile mirroring. Unexpected or
repeated patch matches fail the image build. Production image dependencies are unchanged;
`aiohttp` is pinned only as an offline test dependency because Hermes already supplies it.

Local implementation and tests do not establish a production rollout. The new helper
requires a reviewed Hermes image rebuild and the Web changes require the established
authorized delivery process. Neither a save nor local tests imply a deployment occurred.

The profile selector does not change Telegram transport, tokens, routing, cron delivery,
supervisor reconciliation or profile configuration regeneration. Existing Carpediev
preparation already preserves its owner edits. No restart occurs when selecting a profile.
